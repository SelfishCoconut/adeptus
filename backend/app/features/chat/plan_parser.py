"""Tolerant extraction of the AI's structured plan + certainty claims (Slice 13, §5.3).

The local model is *instructed* (see ``service.PLAN_CERTAINTY_INSTRUCTION``) to end its
reply with a single machine-readable metadata block delimited by a stable sentinel:

    <adeptus-meta>
    { "plan":   [ {"step": "...", "status": "in_progress"}, ... ],
      "claims": [ {"text": "...", "certainty": 60, "node_id": "..."}, ... ] }
    </adeptus-meta>

This module owns the sentinel constants and the extraction. :func:`extract` returns the
**prose with the block stripped** plus the parsed plan and claims.

Design contract — the parser NEVER breaks a turn and NEVER redacts prose (§5.5):

* No block, malformed JSON, wrong shape, or an oversized block all degrade gracefully to
  an **empty** plan + claims; the prose is returned with only the sentinel block removed
  (so the raw ``<adeptus-meta>`` text never leaks to the user — Risk 2), otherwise verbatim.
* ``status`` is coerced (anything unrecognized → ``todo``); ``certainty`` is clamped to
  0–100 (a claim with no usable certainty is dropped); ``node_id`` is kept only if it is a
  well-formed UUID. Graph-membership validation of ``node_id`` (foreign/unknown ids) is the
  service's job at finalize (§17.1), not this pure module's.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from app.features.chat.schemas import Claim, PlanStep, PlanStepStatus

# Stable sentinel delimiters. Chosen to be unlikely in normal pentest prose and trivial to
# strip. The service's token-stream buffering keys off START_MARKER too, so both layers
# agree on the boundary.
START_MARKER = "<adeptus-meta>"
END_MARKER = "</adeptus-meta>"

# Defensive caps so a misbehaving model can't blow up the turn or the panel. An oversized
# block degrades to empty (but is still stripped from the prose).
MAX_BLOCK_CHARS = 16384
MAX_PLAN_STEPS = 50
MAX_CLAIMS = 50

_VALID_STATUSES = {s.value for s in PlanStepStatus}


def extract(raw_reply: str) -> tuple[str, list[PlanStep], list[Claim]]:
    """Split the metadata block off ``raw_reply``.

    Returns ``(prose, plan, claims)`` where ``prose`` is the reply with the sentinel block
    removed (verbatim otherwise, §5.5). Any failure to find/parse a well-formed block
    yields ``([] , [])`` for plan/claims; when no block is present at all the prose is the
    untouched ``raw_reply``.
    """
    start = raw_reply.rfind(START_MARKER)
    if start == -1:
        # No block at all: the whole reply is clean prose (returned untouched).
        return raw_reply, [], []

    end = raw_reply.find(END_MARKER, start + len(START_MARKER))
    if end == -1:
        # Truncated/unterminated block: strip from the marker to the end so the sentinel
        # never leaks (Risk 2); there is no parseable body, so plan/claims are empty.
        return raw_reply[:start].strip(), [], []

    body = raw_reply[start + len(START_MARKER) : end]
    prose = (raw_reply[:start] + raw_reply[end + len(END_MARKER) :]).strip()
    plan, claims = _parse_body(body)
    return prose, plan, claims


def _parse_body(body: str) -> tuple[list[PlanStep], list[Claim]]:
    """Parse the JSON between the sentinels into a plan + claims, tolerating any garbage."""
    body = body.strip()
    if not body or len(body) > MAX_BLOCK_CHARS:
        return [], []
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return [], []
    if not isinstance(data, dict):
        return [], []
    return _parse_plan(data.get("plan")), _parse_claims(data.get("claims"))


def _parse_plan(raw: Any) -> list[PlanStep]:
    if not isinstance(raw, list):
        return []
    steps: list[PlanStep] = []
    for item in raw:
        if len(steps) >= MAX_PLAN_STEPS:
            break
        if not isinstance(item, dict):
            continue
        step_text = item.get("step")
        if not isinstance(step_text, str) or not step_text.strip():
            continue
        steps.append(PlanStep(step=step_text, status=_coerce_status(item.get("status"))))
    return steps


def _parse_claims(raw: Any) -> list[Claim]:
    if not isinstance(raw, list):
        return []
    claims: list[Claim] = []
    for item in raw:
        if len(claims) >= MAX_CLAIMS:
            break
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        certainty = _coerce_certainty(item.get("certainty"))
        if certainty is None:
            # A "claim" with no usable certainty isn't an uncertainty signal — drop it.
            continue
        claims.append(
            Claim(text=text, certainty=certainty, node_id=_coerce_node_id(item.get("node_id")))
        )
    return claims


def _coerce_status(raw: Any) -> PlanStepStatus:
    """Map a status value onto the enum, defaulting to ``todo`` for anything unrecognized.

    Tolerates the common variants a small model emits: case differences and ``-``/space
    in place of the ``_`` in ``in_progress``."""
    if isinstance(raw, str):
        normalized = raw.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in _VALID_STATUSES:
            return PlanStepStatus(normalized)
    return PlanStepStatus.TODO


def _coerce_certainty(raw: Any) -> int | None:
    """Coerce a certainty to an int clamped to 0–100, or ``None`` if it isn't a number.

    Accepts an int/float, or a string parseable as a decimal float (e.g. ``"60"``,
    ``"60.0"``); a non-numeric string like ``"60%"`` or ``"high"`` yields ``None`` so the
    claim is dropped (no usable certainty)."""
    if isinstance(raw, bool):  # bool is an int subclass — never a certainty
        return None
    value: float
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        try:
            value = float(raw.strip())
        except ValueError:
            return None
    else:
        return None
    return max(0, min(100, int(value)))


def _coerce_node_id(raw: Any) -> UUID | None:
    """Keep ``node_id`` only if it is a well-formed UUID string (else ``None``)."""
    if not isinstance(raw, str):
        return None
    try:
        return UUID(raw.strip())
    except (ValueError, AttributeError):
        return None
