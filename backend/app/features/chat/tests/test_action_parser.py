"""Unit tests for chat.action_parser (Slice 16): the instructed-block fallback."""

import json

from app.features.chat import action_parser
from app.features.chat.plan_parser import END_MARKER, START_MARKER


def _meta(payload: dict[str, object]) -> str:
    return f"prose here\n{START_MARKER}\n{json.dumps(payload)}\n{END_MARKER}"


def test_fallback_mode_parses_actions_block() -> None:
    raw = _meta(
        {
            "plan": [],
            "actions": [
                {
                    "server": "shell-exec",
                    "tool": "run",
                    "args": {"cmd": "id"},
                    "rationale": "check",
                },
            ],
        }
    )
    actions = action_parser.extract_actions(raw)
    assert len(actions) == 1
    assert actions[0].server_name == "shell-exec"
    assert actions[0].args == {"cmd": "id"}


def test_multiple_actions_parsed_in_order() -> None:
    raw = _meta(
        {
            "actions": [
                {"server": "a", "tool": "t1"},
                {"server": "b", "tool": "t2"},
                {"tool": "no-server"},  # malformed → dropped
            ]
        }
    )
    actions = action_parser.extract_actions(raw)
    assert [a.server_name for a in actions] == ["a", "b"]


def test_no_block_yields_no_actions() -> None:
    assert action_parser.extract_actions("just clean prose, no block") == []


def test_unterminated_block_yields_no_actions() -> None:
    assert action_parser.extract_actions(f'x {START_MARKER} {{"actions": []}}') == []


def test_malformed_json_yields_no_actions() -> None:
    raw = f"{START_MARKER}\n{{not json\n{END_MARKER}"
    assert action_parser.extract_actions(raw) == []


def test_actions_not_a_list_yields_no_actions() -> None:
    assert action_parser.extract_actions(_meta({"actions": "nope"})) == []


def test_block_without_actions_key_yields_no_actions() -> None:
    # A Slice-13 plan/claims-only block must not produce actions.
    assert action_parser.extract_actions(_meta({"plan": [{"step": "x"}], "claims": []})) == []
