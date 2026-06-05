"""Instructed-block fallback parser for AI-proposed commands (Slice 16, Resolved decision 1).

When ``ADEPTUS_TOOLCALL_MODE`` is ``fallback`` (a backend with weak/no native tool-calling),
the model is instructed to emit an ``actions`` array inside the same trailing
``<adeptus-meta>`` block Slice 13 uses for plan/claims. This module extracts that array,
tolerantly: a missing or malformed block yields ``[]`` and the turn never fails — exactly the
Slice-13 rule. The result is the SAME ``ProposedAction`` list the native path produces, so
classify/gate/audit/frontend are mechanism-agnostic.
"""

from __future__ import annotations

import json
from typing import Any

from app.features.approvals.schemas import ProposedAction
from app.features.chat import tool_calling
from app.features.chat.plan_parser import END_MARKER, START_MARKER

# Appended to the system prompt in fallback mode so the model knows how to propose a command.
FALLBACK_ACTION_INSTRUCTION = (
    '\n\nTo propose running a pentest tool command, include an "actions" array inside the '
    "trailing <adeptus-meta> JSON block (alongside any plan/claims). Each action is an object "
    'with: "server" (MCP server name), "tool" (tool name), "args" (object of arguments, '
    'verbatim), and optionally "preset" and "rationale". Propose only commands you actually '
    "want run; the platform classifies each and gates dangerous ones for human approval. Omit "
    "the array entirely when no command is needed."
)


def extract_actions(raw_reply: str) -> list[ProposedAction]:
    """Return the proposed actions from the trailing ``<adeptus-meta>`` ``actions`` array.

    Tolerant by design (Slice-13 rule): no block, an unterminated block, malformed JSON, or a
    non-list ``actions`` value all yield ``[]``.
    """
    start = raw_reply.rfind(START_MARKER)
    if start == -1:
        return []
    end = raw_reply.find(END_MARKER, start + len(START_MARKER))
    if end == -1:
        return []
    body = raw_reply[start + len(START_MARKER) : end]
    return _parse_actions(body)


def _parse_actions(body: str) -> list[ProposedAction]:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    raw_actions = data.get("actions")
    if not isinstance(raw_actions, list):
        return []
    actions: list[ProposedAction] = []
    for item in raw_actions:
        if not isinstance(item, dict):
            continue
        action = tool_calling.to_proposed_action(_as_str_keyed(item))
        if action is not None:
            actions.append(action)
    return actions


def _as_str_keyed(item: dict[Any, Any]) -> dict[str, Any]:
    """Coerce JSON object keys to ``str`` (json already guarantees this; keeps mypy happy)."""
    return {str(k): v for k, v in item.items()}
