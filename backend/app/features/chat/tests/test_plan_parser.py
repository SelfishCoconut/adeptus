"""Unit tests for the tolerant plan/claims extractor (Slice 13, §5.3).

The extractor must never break a turn and never redact prose (§5.5): every malformed
shape degrades to an empty plan/claims, and the sentinel block must never leak into the
prose the user sees (Risk 2).
"""

from __future__ import annotations

import json
from uuid import uuid4

from app.features.chat import plan_parser
from app.features.chat.plan_parser import END_MARKER, START_MARKER, extract
from app.features.chat.schemas import PlanStepStatus


def _block(payload: dict[str, object]) -> str:
    return f"{START_MARKER}\n{json.dumps(payload)}\n{END_MARKER}"


def test_parses_well_formed_block() -> None:
    nid = uuid4()
    raw = "Here is my reasoning.\n\n" + _block(
        {
            "plan": [
                {"step": "Enumerate the login endpoint", "status": "done"},
                {"step": "Test SQLi on username", "status": "in_progress"},
                {"step": "Check cookie flags", "status": "todo"},
            ],
            "claims": [
                {"text": "Service is likely Apache 2.4", "certainty": 60, "node_id": str(nid)},
            ],
        }
    )

    prose, plan, claims = extract(raw)

    assert len(plan) == 3
    assert plan[0].step == "Enumerate the login endpoint"
    assert plan[0].status == PlanStepStatus.DONE
    assert plan[1].status == PlanStepStatus.IN_PROGRESS
    assert plan[2].status == PlanStepStatus.TODO
    assert len(claims) == 1
    assert claims[0].text == "Service is likely Apache 2.4"
    assert claims[0].certainty == 60
    assert claims[0].node_id == nid
    assert prose == "Here is my reasoning."


def test_strips_block_from_prose() -> None:
    raw = "Clean prose here.\n\n" + _block({"plan": [], "claims": []})
    prose, _, _ = extract(raw)
    assert prose == "Clean prose here."
    assert START_MARKER not in prose
    assert END_MARKER not in prose


def test_no_block_returns_prose_unchanged() -> None:
    raw = "Just a plain answer with no metadata block at all."
    prose, plan, claims = extract(raw)
    assert prose == raw  # byte-for-byte unchanged (§5.5)
    assert plan == []
    assert claims == []


def test_malformed_json_degrades_to_empty() -> None:
    raw = f"Prose.\n{START_MARKER}\n{{not valid json,,,}}\n{END_MARKER}"
    prose, plan, claims = extract(raw)
    assert plan == []
    assert claims == []
    # The block is still stripped even though its body was garbage.
    assert START_MARKER not in prose
    assert prose == "Prose."


def test_unterminated_block_is_stripped_not_leaked() -> None:
    """A START marker with no END (truncated stream) must not leak the sentinel (Risk 2)."""
    raw = f'Answer text.\n\n{START_MARKER}\n{{"plan": [' + '{"step": "half'
    prose, plan, claims = extract(raw)
    assert START_MARKER not in prose
    assert prose == "Answer text."
    assert plan == []
    assert claims == []


def test_certainty_clamped() -> None:
    raw = _block(
        {
            "claims": [
                {"text": "way over", "certainty": 9000},
                {"text": "way under", "certainty": -50},
                {"text": "fractional", "certainty": 72.9},
            ]
        }
    )
    _, _, claims = extract(raw)
    by_text = {c.text: c.certainty for c in claims}
    assert by_text["way over"] == 100
    assert by_text["way under"] == 0
    assert by_text["fractional"] == 72


def test_invalid_status_defaults_todo() -> None:
    raw = _block(
        {
            "plan": [
                {"step": "weird status", "status": "banana"},
                {"step": "missing status"},
                {"step": "hyphen variant", "status": "in-progress"},
            ]
        }
    )
    _, plan, _ = extract(raw)
    assert plan[0].status == PlanStepStatus.TODO
    assert plan[1].status == PlanStepStatus.TODO
    # A common hyphenated variant is tolerated, not defaulted.
    assert plan[2].status == PlanStepStatus.IN_PROGRESS


def test_invalid_node_id_dropped() -> None:
    raw = _block(
        {
            "claims": [
                {"text": "bad id", "certainty": 50, "node_id": "not-a-uuid"},
                {"text": "no id", "certainty": 50},
            ]
        }
    )
    _, _, claims = extract(raw)
    assert len(claims) == 2
    assert all(c.node_id is None for c in claims)


def test_claim_without_certainty_dropped() -> None:
    raw = _block(
        {
            "claims": [
                {"text": "not really a certainty claim"},
                {"text": "real one", "certainty": 40},
            ]
        }
    )
    _, _, claims = extract(raw)
    assert [c.text for c in claims] == ["real one"]


def test_block_never_leaks_into_prose() -> None:
    raw = "Lead-in.\n" + _block(
        {"plan": [{"step": "s", "status": "todo"}], "claims": [{"text": "t", "certainty": 1}]}
    )
    prose, _, _ = extract(raw)
    assert "adeptus-meta" not in prose
    assert "certainty" not in prose
    assert "status" not in prose


def test_prose_not_redacted() -> None:
    """Prose outside the block — including secret-looking text — passes through verbatim."""
    secret = "the db password is hunter2-DO-NOT-STRIP"
    raw = f"Note: {secret}.\n\n" + _block({"plan": [], "claims": []})
    prose, _, _ = extract(raw)
    assert secret in prose
    assert prose == f"Note: {secret}."


def test_oversized_block_degrades_to_empty() -> None:
    huge = "x" * (plan_parser.MAX_BLOCK_CHARS + 10)
    raw = "Prose.\n" + _block({"plan": [{"step": huge, "status": "todo"}]})
    prose, plan, claims = extract(raw)
    assert plan == []
    assert claims == []
    assert START_MARKER not in prose  # still stripped


def test_plan_and_claims_truncated_to_cap() -> None:
    raw = _block(
        {
            "plan": [
                {"step": f"s{i}", "status": "todo"} for i in range(plan_parser.MAX_PLAN_STEPS + 5)
            ],
            "claims": [
                {"text": f"c{i}", "certainty": 10} for i in range(plan_parser.MAX_CLAIMS + 5)
            ],
        }
    )
    _, plan, claims = extract(raw)
    assert len(plan) == plan_parser.MAX_PLAN_STEPS
    assert len(claims) == plan_parser.MAX_CLAIMS
