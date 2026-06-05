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
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.core.errors import ConflictError, NotFoundError
from app.features.audit import service as audit_service
from app.features.audit.schemas import AuditAction
from app.features.auth import repository as auth_repo
from app.features.auth.models import User
from app.features.chat import ollama_client, subset_builder
from app.features.chat import repository as chat_repo
from app.features.chat.models import ChatMessage
from app.features.chat.ollama_client import LlmUnreachableError, OllamaUsage
from app.features.chat.schemas import (
    ChatMessagePage,
    ChatMessageRead,
    ChatMessageStatus,
    ChatTurnDebug,
    GraphSubsetEdge,
    GraphSubsetNode,
    OllamaChatMessage,
    SendChatMessageResult,
    WebSocketChatChunk,
)
from app.features.engagements import repository as eng_repo
from app.features.graph import repository as graph_repo

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
    pinned_node_ids: Sequence[UUID] = (),
    recent_node_ids: Sequence[UUID] = (),
    mentioned_node_ids: Sequence[UUID] = (),
) -> SendChatMessageResult:
    """Persist the user message + an empty ``pending`` assistant placeholder.

    Membership chokepoint (404 for non-members/missing, §17.1), then an archived-
    engagement guard (409, §4). The assistant reply is streamed separately over
    ``WS /ws/chat/{assistant_message_id}``. The caller (router) commits.

    The three id lists are the client-supplied §5.3 union inputs (Slice 12). They are
    stashed verbatim onto the pending assistant row (Decision 4) so the WS streamer can
    resolve them against the live graph at stream time; the streamer overwrites the stash
    with the canonical subset at finalize. They are NOT resolved or trusted here.
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
        graph_context=_input_stash(pinned_node_ids, recent_node_ids, mentioned_node_ids),
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

    # Re-check membership even though the owner necessarily had it at send time: it may
    # have been revoked after the message was persisted (§17.1), and a removed member
    # must not keep streaming the engagement's model.
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
    context_block: str = "",
) -> list[OllamaChatMessage]:
    """Build the Ollama messages array: system + completed window verbatim (§5.4/§5.5).

    Skips the in-flight assistant placeholder and any non-complete/empty rows; the
    triggering user message (always ``complete``) is included unchanged — no redaction.

    ``context_block`` (Slice 12) is the rendered §5.3 relevant subset; when non-empty it is
    appended to the single system message verbatim (§5.5). When empty the prompt is exactly
    the Slice-11 prompt (empty-graph / empty-subset turns are unchanged).
    """
    system_content = f"{SYSTEM_PROMPT}\n\n{context_block}" if context_block else SYSTEM_PROMPT
    messages: list[OllamaChatMessage] = [OllamaChatMessage(role="system", content=system_content)]
    for m in window:
        if m.id == current_assistant_id:
            continue
        if m.status != "complete" or not m.content:
            # A failed/empty earlier turn is excluded from context; log it so the
            # shortened window (and the audit prompt_message_count) is explainable.
            logger.debug("Skipping non-complete message %s from chat prompt window", m.id)
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
) -> AsyncGenerator[WebSocketChatChunk, None]:
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

        # Build the §5.3 relevant subset from the POST-time stash + the live graph, and
        # prepend it to the system prompt (Slice 12). On an empty graph / empty subset the
        # context block is empty and the prompt is exactly the Slice-11 prompt. The canonical
        # debug record (resolved subset + raw prompt) is persisted at finalize for §14.
        subset = await _build_turn_subset(
            session,
            engagement_id=engagement_id,
            message_text=_triggering_user_text(window),
            stash=current.graph_context,
        )
        prompt = _build_prompt(
            window, current_assistant_id=message_id, context_block=subset.context_block
        )
        debug_record = _debug_record(subset, _render_raw_prompt(prompt))

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
                    graph_context=debug_record,
                    subset=subset,
                )
                yield WebSocketChatChunk(type="error", message=UNREACHABLE_MESSAGE)
                return
            chunks.append(token)
            yield WebSocketChatChunk(type="token", data=token)

        full_content = "".join(chunks)
        finalized = await chat_repo.finalize_assistant(
            session,
            message_id=message_id,
            content=full_content,
            status="complete",
            model=model_name,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            graph_context=debug_record,
        )
        # Emit the audit entry only when THIS call won the pending→terminal transition;
        # a racing socket that already finalized the row gets None and must not re-emit
        # (Risk 6 — exactly one ai_call per turn).
        if finalized is not None:
            await _emit_ai_call(
                session,
                actor_user_id=user_id,
                engagement_id=engagement_id,
                message_id=message_id,
                model_name=model_name,
                prompt_count=len(prompt),
                status="complete",
                nodes_injected=subset.nodes_injected,
                edges_injected=subset.edges_injected,
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
    graph_context: dict[str, Any] | None = None,
    subset: subset_builder.GraphSubset | None = None,
) -> None:
    """Persist the assistant row ``failed`` + emit the ``ai_call`` audit entry, atomic.

    Like the success path, the ``ai_call`` is emitted only when this call won the
    pending→failed transition (Risk 6 — exactly one ai_call per turn). The §14 debug record
    (the resolved subset + raw prompt) is persisted even on failure so the debug panel can
    show what the AI was shown on a turn that never produced output (model_output empty)."""
    finalized = await chat_repo.finalize_assistant(
        session,
        message_id=message_id,
        content="",
        status="failed",
        model=model_name,
        prompt_tokens=None,
        completion_tokens=None,
        graph_context=graph_context,
    )
    if finalized is not None:
        await _emit_ai_call(
            session,
            actor_user_id=actor_user_id,
            engagement_id=engagement_id,
            message_id=message_id,
            model_name=model_name,
            prompt_count=prompt_count,
            status="failed",
            nodes_injected=subset.nodes_injected if subset is not None else 0,
            edges_injected=subset.edges_injected if subset is not None else 0,
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
    nodes_injected: int = 0,
    edges_injected: int = 0,
) -> None:
    """Record one ``ai_call`` audit entry attributed to the acting user (§14).

    The payload carries the §5.3 subset *counts* (Slice 12) so the forensic log records how
    much graph context each turn used — no new audit action/table, just a widened payload."""
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
            "graph_nodes_injected": nodes_injected,
            "graph_edges_injected": edges_injected,
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


# ---------------------------------------------------------------------------
# §5.3 relevant-subset assembly (Slice 12)
# ---------------------------------------------------------------------------


def _input_stash(
    pinned: Sequence[UUID], recent: Sequence[UUID], mentioned: Sequence[UUID]
) -> dict[str, Any]:
    """Serialize the client-supplied §5.3 union inputs for the pending-row stash (Decision 4).

    Stored as JSON-safe id strings under ``inputs`` so the streamer can re-resolve them
    against the live graph at stream time; overwritten with the canonical subset at finalize.
    """
    return {
        "inputs": {
            "pinned_node_ids": [str(x) for x in pinned],
            "recent_node_ids": [str(x) for x in recent],
            "mentioned_node_ids": [str(x) for x in mentioned],
        }
    }


def _triggering_user_text(window: Sequence[ChatMessage]) -> str:
    """The user message that triggered this turn = the most recent user row in the window.

    Used for the §5.3 keyword arm. The window is oldest-first; the triggering user message
    sits just before the pending assistant placeholder, so the last user row is it."""
    for m in reversed(window):
        if m.role == "user" and m.content:
            return m.content
    return ""


def _parse_uuid_list(raw: object) -> list[UUID]:
    """Parse a stashed list of id strings back to UUIDs, dropping anything malformed."""
    if not isinstance(raw, list):
        return []
    out: list[UUID] = []
    for item in raw:
        try:
            out.append(UUID(str(item)))
        except (ValueError, TypeError):
            continue
    return out


async def _build_turn_subset(
    session: AsyncSession,
    *,
    engagement_id: UUID,
    message_text: str,
    stash: dict[str, Any] | None,
) -> subset_builder.GraphSubset:
    """Resolve the stashed inputs + the engagement's live graph into the §5.3 subset.

    Reads the live graph via the existing ``graph.repository.load_live_graph`` read path
    (engagement-scoped, non-deleted only); chat never writes the graph (ADR-0001)."""
    inputs = stash.get("inputs", {}) if isinstance(stash, dict) else {}
    inputs = inputs if isinstance(inputs, dict) else {}
    nodes, edges = await graph_repo.load_live_graph(session, engagement_id)
    settings = get_settings()
    return subset_builder.build(
        nodes=nodes,
        edges=edges,
        message_text=message_text,
        pinned_node_ids=_parse_uuid_list(inputs.get("pinned_node_ids")),
        recent_node_ids=_parse_uuid_list(inputs.get("recent_node_ids")),
        mentioned_node_ids=_parse_uuid_list(inputs.get("mentioned_node_ids")),
        n_recent=settings.ADEPTUS_GRAPH_CONTEXT_RECENT_LIMIT,
        k_mentioned=settings.ADEPTUS_GRAPH_CONTEXT_MENTIONED_LIMIT,
    )


def _render_raw_prompt(prompt: Sequence[OllamaChatMessage]) -> str:
    """Render the Ollama messages array to the verbatim raw-prompt text (§14 "raw prompts")."""
    return "\n\n".join(f"[{m.role}]\n{m.content}" for m in prompt)


def _debug_record(subset: subset_builder.GraphSubset, raw_prompt: str) -> dict[str, Any]:
    """The canonical per-turn §14 debug record persisted into ``chat_messages.graph_context``."""
    return {
        "nodes": [n.model_dump(mode="json") for n in subset.nodes],
        "edges": [e.model_dump(mode="json") for e in subset.edges],
        "context_block": subset.context_block,
        "raw_prompt": raw_prompt,
    }


# ---------------------------------------------------------------------------
# get_turn_debug (§14 AI debug panel)
# ---------------------------------------------------------------------------


async def get_turn_debug(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    requester: User,
    message_id: UUID,
) -> ChatTurnDebug:
    """Return the §14 debug record for one of the caller's own assistant turns.

    Membership + ownership chokepoint (§17.1 / §5.4): non-member, non-owner, wrong
    engagement, or a non-assistant row all collapse to ``NotFoundError`` (404) — no
    existence disclosure. The record contains graph labels/values, so this gate is the
    sole guard against a cross-user/cross-engagement leak (Risk 5)."""
    if await eng_repo.get_engagement_for_member(db, engagement_id, _user_id(requester)) is None:
        raise NotFoundError("Engagement not found")
    message = await chat_repo.get_message_for_owner(
        db, message_id=message_id, user_id=_user_id(requester)
    )
    if (
        message is None
        or message.role != "assistant"
        or cast(UUID, message.engagement_id) != engagement_id
    ):
        raise NotFoundError("Message not found")
    return _to_turn_debug(message)


def _to_turn_debug(message: ChatMessage) -> ChatTurnDebug:
    """Map a persisted assistant row + its ``graph_context`` into the §14 read schema.

    A row whose ``graph_context`` still holds only the POST-time stash (pending), or is NULL
    (pre-slice), yields an empty subset and empty prompt blocks — the panel shows the
    empty-subset state. ``model_output`` is the row's ``content`` (empty while pending/failed).
    """
    gc = message.graph_context if isinstance(message.graph_context, dict) else {}
    raw_nodes = gc.get("nodes")
    raw_edges = gc.get("edges")
    nodes = (
        [GraphSubsetNode.model_validate(n) for n in raw_nodes]
        if isinstance(raw_nodes, list)
        else []
    )
    edges = (
        [GraphSubsetEdge.model_validate(e) for e in raw_edges]
        if isinstance(raw_edges, list)
        else []
    )
    return ChatTurnDebug(
        message_id=cast(UUID, message.id),
        model=message.model,
        status=ChatMessageStatus(message.status),
        nodes=nodes,
        edges=edges,
        context_block=str(gc.get("context_block", "")),
        raw_prompt=str(gc.get("raw_prompt", "")),
        model_output=message.content,
    )
