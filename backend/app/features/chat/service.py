"""Business logic for private per-user chat backed by the local Ollama model (Slice 11).

Membership + privacy chokepoints (§5.4 / §17.1):
  Every engagement-scoped entry point runs ``eng_repo.get_engagement_for_member`` first.
  Both "engagement missing" and "caller not a member" collapse to ``NotFoundError`` (404)
  so a non-member cannot infer the engagement exists. Reads and the WS auth additionally
  scope to the caller's own ``user_id`` so one user can never see another's conversation.

No redaction (§5.5): user content is forwarded to the model byte-for-byte. The local path
has no egress; the cloud egress-friction layer is Slice 14.

Audit (§14): a completed AND a failed turn each emit exactly one ``ai_call`` entry via the
already-reviewed ``audit.service.record`` chokepoint, atomic with the final persist. A
reconnect replay of an already-terminal message emits nothing (no double-count, Risk 6).
"""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.errors import ConflictError, NotFoundError
from app.features.audit import service as audit_service
from app.features.audit.schemas import AuditAction
from app.features.auth import repository as auth_repo
from app.features.auth.models import User
from app.features.chat import ollama_client
from app.features.chat import repository as chat_repo
from app.features.chat.models import ChatMessage
from app.features.chat.ollama_client import LlmUnreachableError, OllamaUsage
from app.features.chat.schemas import (
    ChatMessagePage,
    ChatMessageRead,
    OllamaChatMessage,
    SendChatMessageResult,
    WebSocketChatChunk,
)
from app.features.engagements import repository as eng_repo

logger = logging.getLogger(__name__)

# Open Question 1: ship the last N messages verbatim, no summarization.
RECENT_WINDOW = 20

# Open Question 3: wedged-socket safety valve. If the model emits no token (and no error)
# for this long, mark the turn failed and close. Reset by every token, so a slow-but-
# progressing model is never aborted (§5.1 / Risk 4).
NO_PROGRESS_TIMEOUT_SECONDS = 600.0

# Open Question 4: single neutral general-style system prompt until personas (Slice 15).
SYSTEM_PROMPT = (
    "You are a penetration-testing assistant embedded in the Adeptus platform. "
    "Help the operator reason about their authorized engagement: explain techniques, "
    "interpret tool output, and suggest next steps. Be concise and technical."
)

# Stable, non-leaky reason surfaced to the client (matches the demo copy).
UNREACHABLE_MESSAGE = "AI is unreachable — local model is offline"


class EngagementArchivedError(ConflictError):
    """Raised when a new chat message targets an archived engagement (§4 read-only).

    Subclasses the core ``ConflictError`` so the registered handler maps it to HTTP 409;
    existing messages remain browsable (the GET history endpoint still works).
    """

    def __init__(self, message: str = "Engagement is archived (read-only)") -> None:
        super().__init__(message)


# ---------------------------------------------------------------------------
# Cursor helpers (opaque base64 of "created_at_iso|message_id")
# ---------------------------------------------------------------------------


def _encode_cursor(created_at: datetime, message_id: UUID) -> str:
    raw = f"{created_at.isoformat()}|{message_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Decode an opaque cursor; raises ValueError when malformed."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts_part, id_part = raw.split("|", 1)
        return datetime.fromisoformat(ts_part), UUID(id_part)
    except Exception as exc:  # noqa: BLE001 — collapse every malformed cursor to one error
        raise ValueError(f"Malformed cursor: {cursor!r}") from exc


def _user_id(user: User) -> UUID:
    return cast(UUID, user.id)


# ---------------------------------------------------------------------------
# send_message
# ---------------------------------------------------------------------------


async def send_message(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    requester: User,
    content: str,
) -> SendChatMessageResult:
    """Persist the user message + an empty ``pending`` assistant placeholder.

    Membership chokepoint (404 for non-members/missing, §17.1), then an archived-
    engagement guard (409, §4). The assistant reply is streamed separately over
    ``WS /ws/chat/{assistant_message_id}``. The caller (router) commits.
    """
    member = await eng_repo.get_engagement_for_member(db, engagement_id, _user_id(requester))
    if member is None:
        raise NotFoundError("Engagement not found")
    engagement, _membership = member
    if engagement.status == "archived":
        raise EngagementArchivedError()

    user_message, assistant_message = await chat_repo.insert_user_and_pending_assistant(
        db,
        engagement_id=engagement_id,
        user_id=_user_id(requester),
        content=content,
    )
    return SendChatMessageResult(
        user_message=ChatMessageRead.model_validate(user_message),
        assistant_message=ChatMessageRead.model_validate(assistant_message),
    )


# ---------------------------------------------------------------------------
# list_messages
# ---------------------------------------------------------------------------


async def list_messages(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    requester: User,
    cursor: str | None,
    limit: int,
) -> ChatMessagePage:
    """Return one page of the caller's own conversation (oldest-first, §5.4)."""
    if await eng_repo.get_engagement_for_member(db, engagement_id, _user_id(requester)) is None:
        raise NotFoundError("Engagement not found")

    decoded: tuple[datetime, UUID] | None = None
    if cursor:
        try:
            decoded = _decode_cursor(cursor)
        except ValueError:
            # A malformed cursor is treated as the first page rather than a hard error;
            # the read is harmless and idempotent.
            decoded = None

    rows, next_cursor_raw = await chat_repo.list_conversation(
        db,
        engagement_id=engagement_id,
        user_id=_user_id(requester),
        cursor=decoded,
        limit=limit,
    )
    next_cursor = (
        _encode_cursor(next_cursor_raw[0], next_cursor_raw[1])
        if next_cursor_raw is not None
        else None
    )
    return ChatMessagePage(
        items=[ChatMessageRead.model_validate(r) for r in rows],
        next_cursor=next_cursor,
    )


# ---------------------------------------------------------------------------
# WebSocket auth
# ---------------------------------------------------------------------------


async def authenticate_ws_chat_message(
    db: AsyncSession,
    *,
    session_id: str | None,
    message_id: UUID,
) -> ChatMessage | None:
    """Authenticate + authorize a WebSocket subscription to an assistant message.

    Mirrors ``mcp.service.authenticate_ws_tool_run``: resolves the session WITHOUT
    sliding expiry or emitting Set-Cookie, then checks ownership (the message belongs to
    the caller) AND membership of its engagement. Returns the assistant ``ChatMessage``
    row, or ``None`` on ANY failure so the router collapses every case to one close code
    (4003, no existence disclosure). Only ``assistant`` messages are streamable.
    """
    if session_id is None:
        return None

    db_session = await auth_repo.get_session(db, session_id)
    if db_session is None:
        return None

    exp = db_session.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    if exp <= datetime.now(UTC):
        return None

    user = await auth_repo.get_user_by_id(db, cast(UUID, db_session.user_id))
    if user is None:
        return None

    message = await chat_repo.get_message_for_owner(
        db, message_id=message_id, user_id=cast(UUID, user.id)
    )
    if message is None or message.role != "assistant":
        return None

    membership = await eng_repo.get_engagement_for_member(
        db, cast(UUID, message.engagement_id), cast(UUID, user.id)
    )
    if membership is None:
        return None

    return message


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _build_prompt(
    window: Sequence[ChatMessage],
    *,
    current_assistant_id: UUID,
) -> list[OllamaChatMessage]:
    """Build the Ollama messages array: system + completed window verbatim (§5.4/§5.5).

    Skips the in-flight assistant placeholder and any non-complete/empty rows; the
    triggering user message (always ``complete``) is included unchanged — no redaction.
    """
    messages: list[OllamaChatMessage] = [OllamaChatMessage(role="system", content=SYSTEM_PROMPT)]
    for m in window:
        if m.id == current_assistant_id:
            continue
        if m.status != "complete" or not m.content:
            continue
        messages.append(
            OllamaChatMessage(role=cast(Literal["user", "assistant"], m.role), content=m.content)
        )
    return messages


# ---------------------------------------------------------------------------
# stream_assistant_reply
# ---------------------------------------------------------------------------


async def stream_assistant_reply(  # noqa: C901 — linear flow; splitting would obscure it
    *,
    message: ChatMessage,
) -> AsyncIterator[WebSocketChatChunk]:
    """Stream the assistant reply for an authenticated assistant message.

    Opens a FRESH session (the auth session is already closed). Re-reads the row so the
    terminal-state branches use current DB state (reconnect-safe):

      - ``complete``: replay the stored content as one ``token`` frame, then ``done``.
      - ``failed``:   emit the stored failure reason as an ``error`` frame.
      - ``pending``:  build the prompt, stream Ollama token-by-token, persist the final
        content/status + emit the ``ai_call`` audit entry (atomic), then ``done``; on
        ``LlmUnreachableError`` (or a wedged socket) persist ``failed``, emit ``ai_call``
        with ``status=failed``, and ``error``.
    """
    settings = get_settings()
    model_name = settings.ADEPTUS_LLM_MODEL
    message_id = cast(UUID, message.id)
    engagement_id = cast(UUID, message.engagement_id)
    user_id = cast(UUID, message.user_id)

    async with get_sessionmaker()() as session:
        current = await chat_repo.get_message_for_owner(
            session, message_id=message_id, user_id=user_id
        )
        if current is None:
            yield WebSocketChatChunk(type="error", message=UNREACHABLE_MESSAGE)
            return

        if current.status == "complete":
            if current.content:
                yield WebSocketChatChunk(type="token", data=current.content)
            yield WebSocketChatChunk(type="done")
            return
        if current.status == "failed":
            yield WebSocketChatChunk(type="error", message=UNREACHABLE_MESSAGE)
            return

        # status == "pending": real streaming.
        window = await chat_repo.recent_messages(
            session, engagement_id=engagement_id, user_id=user_id, limit=RECENT_WINDOW
        )
        prompt = _build_prompt(window, current_assistant_id=message_id)

        usage = OllamaUsage()
        chunks: list[str] = []
        agen = ollama_client.stream_chat(messages=prompt, model=model_name, usage=usage).__aiter__()

        while True:
            try:
                token = await asyncio.wait_for(
                    agen.__anext__(), timeout=NO_PROGRESS_TIMEOUT_SECONDS
                )
            except StopAsyncIteration:
                break
            except (LlmUnreachableError, TimeoutError) as exc:
                await _aclose_quiet(agen)
                logger.info("chat turn %s failed: %s", message_id, type(exc).__name__)
                await _finalize_failed(
                    session,
                    message_id=message_id,
                    actor_user_id=user_id,
                    engagement_id=engagement_id,
                    model_name=model_name,
                    prompt_count=len(prompt),
                )
                yield WebSocketChatChunk(type="error", message=UNREACHABLE_MESSAGE)
                return
            chunks.append(token)
            yield WebSocketChatChunk(type="token", data=token)

        full_content = "".join(chunks)
        await chat_repo.finalize_assistant(
            session,
            message_id=message_id,
            content=full_content,
            status="complete",
            model=model_name,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
        )
        await _emit_ai_call(
            session,
            actor_user_id=user_id,
            engagement_id=engagement_id,
            message_id=message_id,
            model_name=model_name,
            prompt_count=len(prompt),
            status="complete",
        )
        await session.commit()
        yield WebSocketChatChunk(type="done")


async def _finalize_failed(
    session: AsyncSession,
    *,
    message_id: UUID,
    actor_user_id: UUID,
    engagement_id: UUID,
    model_name: str,
    prompt_count: int,
) -> None:
    """Persist the assistant row ``failed`` + emit the ``ai_call`` audit entry, atomic."""
    await chat_repo.finalize_assistant(
        session,
        message_id=message_id,
        content="",
        status="failed",
        model=model_name,
        prompt_tokens=None,
        completion_tokens=None,
    )
    await _emit_ai_call(
        session,
        actor_user_id=actor_user_id,
        engagement_id=engagement_id,
        message_id=message_id,
        model_name=model_name,
        prompt_count=prompt_count,
        status="failed",
    )
    await session.commit()


async def _emit_ai_call(
    session: AsyncSession,
    *,
    actor_user_id: UUID,
    engagement_id: UUID,
    message_id: UUID,
    model_name: str,
    prompt_count: int,
    status: str,
) -> None:
    """Record one ``ai_call`` audit entry attributed to the acting user (§14)."""
    await audit_service.record(
        session,
        action=AuditAction.AI_CALL,
        actor_user_id=actor_user_id,
        engagement_id=engagement_id,
        target_type="chat_message",
        target_id=str(message_id),
        payload={
            "model": model_name,
            "message_id": str(message_id),
            "prompt_message_count": prompt_count,
            "status": status,
        },
    )


async def _aclose_quiet(agen: AsyncIterator[str]) -> None:
    """Close the Ollama generator, swallowing any error from the abandoned stream."""
    aclose = getattr(agen, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception:  # noqa: BLE001 — best-effort cleanup of an abandoned stream
        logger.debug("Error while closing Ollama stream", exc_info=True)
