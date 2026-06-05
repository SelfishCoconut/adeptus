"""Pydantic v2 schemas for the chat feature (Slice 11).

The ``ChatRole`` / ``ChatMessageStatus`` StrEnums mirror ``models.CHAT_ROLES`` /
``models.CHAT_STATUSES`` exactly (guarded by test_schemas) so the API vocabulary and
the DB CHECK constraints can never silently drift.

``WebSocketChatChunk`` and ``OllamaChatMessage`` are internal value objects (the WS
frame contract is hand-mirrored in the frontend hook; the Ollama messages array is the
client's input) — neither is a response_model, so neither appears in the OpenAPI doc.
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ChatMessageCreate",
    "ChatMessagePage",
    "ChatMessageRead",
    "ChatMessageStatus",
    "ChatRole",
    "ChatTurnDebug",
    "GraphSubsetEdge",
    "GraphSubsetNode",
    "GraphSubsetReason",
    "OllamaChatMessage",
    "SendChatMessageResult",
    "WebSocketChatChunk",
]

# Hard cap on a single user message (also enforced by the API contract). Sent to the
# model verbatim — no redaction (§5.5).
MAX_MESSAGE_CHARS = 32768


class ChatRole(StrEnum):
    """Chat message author role. Mirrors ``models.CHAT_ROLES``."""

    USER = "user"
    ASSISTANT = "assistant"


class ChatMessageStatus(StrEnum):
    """Lifecycle of a chat message. Mirrors ``models.CHAT_STATUSES``.

    ``user`` messages are always ``complete``. An ``assistant`` message is ``pending``
    until its stream finishes (``complete``) or errors (``failed``).
    """

    COMPLETE = "complete"
    PENDING = "pending"
    FAILED = "failed"


class ChatMessageCreate(BaseModel):
    """Request body for POST .../chat/messages.

    The three id lists carry the client-supplied arms of the §5.3 "relevant subset"
    union: ``pinned_node_ids`` (the always-included pinned set, §5.4), ``recent_node_ids``
    (the "last N touched in the conversation" approximation, most-recent-first), and
    ``mentioned_node_ids`` (the @-mention arm, empty until Slice 31). They are *inputs* only
    — the server re-resolves each id against the engagement's live graph (foreign/unknown
    ids are ignored, §17.1) and runs the keyword arm itself, so the client can never inject
    node content, only ids.
    """

    content: str = Field(
        min_length=1,
        max_length=MAX_MESSAGE_CHARS,
        description="The user's message text, sent verbatim to the model (no redaction, §5.5).",
    )
    pinned_node_ids: list[UUID] = Field(
        default_factory=list,
        description=(
            "Node ids the user has pinned (client-side pinStore, §5.4). Always-included "
            "arm of the §5.3 union. Unknown/foreign ids are ignored server-side (§17.1)."
        ),
    )
    recent_node_ids: list[UUID] = Field(
        default_factory=list,
        description=(
            "Node ids recently interacted with this session, most-recent-first; the "
            '"last N nodes touched in the conversation" arm (§5.3). Server truncates to N.'
        ),
    )
    mentioned_node_ids: list[UUID] = Field(
        default_factory=list,
        description=(
            "Node ids @-mentioned in recent messages (§5.3). Empty until the @-mention UI "
            "(Slice 31); accepted now for forward-compatibility. Server truncates to K."
        ),
    )


class ChatMessageRead(BaseModel):
    """One chat message as exposed by the read/write API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    engagement_id: UUID
    role: ChatRole
    content: str
    status: ChatMessageStatus
    created_at: datetime


class SendChatMessageResult(BaseModel):
    """Result of POST .../chat/messages: the persisted user message plus a ``pending``
    assistant placeholder. Stream the assistant reply via WS using its id."""

    user_message: ChatMessageRead
    assistant_message: ChatMessageRead


class ChatMessagePage(BaseModel):
    """A page of chat messages (oldest-first) with an opaque cursor for the next
    (older) page; ``next_cursor`` is null on the last page."""

    items: list[ChatMessageRead]
    next_cursor: str | None


class WebSocketChatChunk(BaseModel):
    """A single JSON frame sent over WS /ws/chat/{assistant_message_id}.

    type "token": ``data`` carries an incremental piece of assistant text (append).
    type "done":  the stream finished; the assistant message is persisted ``complete``.
    type "error": ``message`` carries a stable, non-leaky reason (e.g. the local model
                  being offline).
    """

    type: Literal["token", "done", "error"]
    data: str | None = None
    message: str | None = None


class OllamaChatMessage(BaseModel):
    """One entry of the Ollama ``messages`` array (system / user / assistant turn)."""

    role: Literal["system", "user", "assistant"]
    content: str


# ---------------------------------------------------------------------------
# AI debug panel (§14) — the per-turn "relevant subset" record (Slice 12)
# ---------------------------------------------------------------------------


class GraphSubsetReason(StrEnum):
    """Why a node entered the §5.3 relevant subset. A node may carry several reasons."""

    PINNED = "pinned"
    RECENT = "recent"
    MENTIONED = "mentioned"
    KEYWORD = "keyword"


class GraphSubsetNode(BaseModel):
    """One graph node included in the per-turn subset, tagged with its inclusion reason(s).

    Labels/properties are NOT carried here — only the rendered ``context_block`` (on
    ``ChatTurnDebug``) holds the verbatim text sent to the model (§5.5). This row is the
    grouping/affordance data the debug panel renders (id + type + label + reasons)."""

    id: UUID
    type: str
    label: str
    reasons: list[GraphSubsetReason] = Field(
        description="Why this node was included (one node may have several reasons)."
    )


class GraphSubsetEdge(BaseModel):
    """One edge between two selected nodes (only edges with both endpoints in the subset)."""

    id: UUID
    source_id: UUID
    target_id: UUID
    relation: str


class ChatTurnDebug(BaseModel):
    """The AI debug record for one assistant turn (§14): the exact relevant subset of the
    graph injected, the raw prompt, and the model output.

    Read by ``GET .../chat/messages/{message_id}/debug``. ``model_output`` is the assistant
    row's ``content`` (empty while pending/failed). ``nodes``/``edges``/``context_block``/
    ``raw_prompt`` are read back from the persisted ``graph_context`` JSONB; an assistant row
    that completed before this slice (or an empty-graph turn) has an empty subset and an
    empty ``context_block``."""

    message_id: UUID
    model: str | None
    status: ChatMessageStatus
    nodes: list[GraphSubsetNode]
    edges: list[GraphSubsetEdge]
    context_block: str = Field(
        description="The exact graph-context text prepended to the system prompt this turn."
    )
    raw_prompt: str = Field(description='The full prompt sent to the model (§14 "raw prompts").')
    model_output: str = Field(
        description="The model's raw output for this turn (empty while pending/failed)."
    )
