"""Native tool-calling support for the chat clients (Slice 16, §5.2 / Resolved decision 1).

The AI proposes a command by calling a single ``propose_command`` tool. This module owns:

* the canonical tool definition + its per-wire-format mappings (Ollama vs Anthropic);
* the out-of-band ``ProposedCalls`` holder (analogous to ``OllamaUsage``) the clients
  populate as tool-call frames arrive, read by the streamer after the text stream ends;
* normalization of a raw tool-call into the internal ``ProposedAction`` (shared by the
  native and instructed-block-fallback paths, so classify/gate/audit/frontend are identical
  regardless of which mechanism produced the proposal).

The clients stay decoupled from the approvals classifier; the chat streamer does the
classify→gate step on the normalized actions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.core.config import get_settings
from app.features.approvals.schemas import ProposedAction

ToolCallMode = Literal["native", "fallback"]

# The single tool presented to both backends (mapped to each wire format below).
PROPOSE_COMMAND_NAME = "propose_command"
_PROPOSE_COMMAND_DESCRIPTION = (
    "Propose a single pentest tool command to run against the engagement target. "
    "The platform classifies it; dangerous commands require human approval before execution."
)
_PROPOSE_COMMAND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["server", "tool", "args"],
    "properties": {
        "server": {"type": "string", "description": "MCP server name (must exist in config)."},
        "tool": {"type": "string", "description": "Tool name on that server."},
        "args": {
            "type": "object",
            "additionalProperties": True,
            "description": "Tool arguments, verbatim — no redaction (§5.5).",
        },
        "preset": {
            "type": "string",
            "description": "Optional named preset (stealth/normal/aggressive).",
        },
        "rationale": {"type": "string", "description": "Why this command, in one sentence."},
    },
}


@dataclass
class ProposedToolCall:
    """One raw tool-call surfaced by a client (name + accumulated JSON arguments)."""

    name: str
    arguments: dict[str, Any]


@dataclass
class ProposedCalls:
    """Mutable out-of-band holder for the tool-calls a client parsed this turn.

    Passed into ``stream_chat`` (like ``OllamaUsage``); the client appends each parsed
    ``propose_command`` call so the streamer can read them after the text stream ends. Its
    presence ALSO signals the client to include the ``propose_command`` tool in the request
    (native tool-calling enabled). Empty when the model emitted no tool-call.
    """

    calls: list[ProposedToolCall] = field(default_factory=list)


def ollama_tools() -> list[dict[str, Any]]:
    """The Ollama ``/api/chat`` ``tools`` array entry for ``propose_command``."""
    return [
        {
            "type": "function",
            "function": {
                "name": PROPOSE_COMMAND_NAME,
                "description": _PROPOSE_COMMAND_DESCRIPTION,
                "parameters": _PROPOSE_COMMAND_SCHEMA,
            },
        }
    ]


def anthropic_tools() -> list[dict[str, Any]]:
    """The Anthropic Messages API ``tools`` array entry for ``propose_command``."""
    return [
        {
            "name": PROPOSE_COMMAND_NAME,
            "description": _PROPOSE_COMMAND_DESCRIPTION,
            "input_schema": _PROPOSE_COMMAND_SCHEMA,
        }
    ]


def resolve_mode() -> ToolCallMode:
    """Resolve ``ADEPTUS_TOOLCALL_MODE`` to a concrete mode.

    ``fallback`` → fallback; ``auto`` and ``native`` → native (auto currently assumes the
    configured backend advertises tool support; capability probing is a future refinement).
    """
    return "fallback" if get_settings().ADEPTUS_TOOLCALL_MODE.lower() == "fallback" else "native"


def to_proposed_action(arguments: dict[str, Any]) -> ProposedAction | None:
    """Normalize one ``propose_command`` argument map into a ``ProposedAction``.

    Returns ``None`` (the call is dropped) when ``server`` or ``tool`` is missing or not a
    string — a malformed proposal must never reach classification.
    """
    server = arguments.get("server")
    tool = arguments.get("tool")
    if not isinstance(server, str) or not server or not isinstance(tool, str) or not tool:
        return None
    raw_args = arguments.get("args")
    args = raw_args if isinstance(raw_args, dict) else {}
    preset = arguments.get("preset")
    rationale = arguments.get("rationale")
    return ProposedAction(
        server_name=server,
        tool_name=tool,
        args=args,
        preset_name=preset if isinstance(preset, str) else None,
        rationale=rationale if isinstance(rationale, str) else None,
    )


def to_proposed_actions(calls: list[ProposedToolCall]) -> list[ProposedAction]:
    """Normalize the holder's tool-calls into ``ProposedAction`` (dropping malformed ones)."""
    actions: list[ProposedAction] = []
    for call in calls:
        if call.name != PROPOSE_COMMAND_NAME:
            continue
        action = to_proposed_action(call.arguments)
        if action is not None:
            actions.append(action)
    return actions
