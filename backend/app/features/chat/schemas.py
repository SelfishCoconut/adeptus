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
    """Request body for POST .../chat/messages."""

    content: str = Field(
        min_length=1,
        max_length=MAX_MESSAGE_CHARS,
        description="The user's message text, sent verbatim to the model (no redaction, §5.5).",
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
