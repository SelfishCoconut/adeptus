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
from app.features.chat import (
    anthropic_client,
    egress_scan,
    ollama_client,
    plan_parser,
    subset_builder,
)
from app.features.chat import repository as chat_repo
from app.features.chat.models import ChatMessage
from app.features.chat.ollama_client import LlmUnreachableError, OllamaUsage
from app.features.chat.schemas import (
    ChatMessagePage,
    ChatMessageRead,
    ChatMessageStatus,
    ChatRole,
    ChatTurnDebug,
    Claim,
    GraphSubsetEdge,
    GraphSubsetNode,
    OllamaChatMessage,
    PlanStep,
    SendChatMessageResult,
    WebSocketChatChunk,
)
from app.features.engagements import repository as eng_repo
from app.features.graph import repository as graph_repo
from app.features.personas import service as personas_service
from app.features.personas.models import Persona
from app.features.personas.seed import GENERAL_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Open Question 1: ship the last N messages verbatim, no summarization.
RECENT_WINDOW = 20

# Open Question 3: wedged-socket safety valve. If the model emits no token (and no error)
# for this long, mark the turn failed and close. Reset by every token, so a slow-but-
# progressing model is never aborted (§5.1 / Risk 4).
NO_PROGRESS_TIMEOUT_SECONDS = 600.0

# Slice 15 (resolved Open Question 3): the base system prompt is now the ``general``
# built-in persona's prompt, kept as the single source of truth in ``personas.seed`` and
# re-imported here so the no-persona default stays byte-identical (the ``general`` built-in
# IS the default). A turn with a selected persona replaces this base (Decision 4); a turn
# with no persona resolves to ``general`` and so uses exactly this text.
SYSTEM_PROMPT = GENERAL_SYSTEM_PROMPT

# Slice 13 (§5.3): the structured-output instruction. Appended AFTER the base prompt and
# the Slice-12 graph context block so the model emits a trailing, machine-readable metadata
# block we parse server-side (plan_parser owns the sentinel constants). Purely additive —
# if the model ignores it the parser degrades to clean prose + empty plan/claims (Risk 1).
PLAN_CERTAINTY_INSTRUCTION = (
    "\n\n---\n"
    "After your prose answer, append EXACTLY ONE metadata block, on its own lines, wrapped "
    f"in {plan_parser.START_MARKER} and {plan_parser.END_MARKER}, containing a single JSON "
    "object with two keys:\n"
    '  - "plan": an ordered list of the steps you are tracking this turn, each '
    '{"step": "<short text>", "status": "todo" | "in_progress" | "done"}.\n'
    '  - "claims": a list of statements you are NOT fully certain about, each '
    '{"text": "<the claim>", "certainty": <integer 0-100>, "node_id": <the relevant graph '
    "node's uuid from the graph context above, or null>}.\n"
    "Use empty lists if you have no plan or no uncertain claims. Put NOTHING after the "
    "closing marker, and do not mention or describe this block in your prose."
)

# Stable, non-leaky reason surfaced to the client (matches the demo copy).
UNREACHABLE_MESSAGE = "AI is unreachable — local model is offline"

# Slice 14 (§5.1): a cloud_enabled turn whose engagement has no configured key fails rather
# than silently using local (no auto-fallback). Same shape as UNREACHABLE_MESSAGE.
CLOUD_NOT_CONFIGURED_MESSAGE = "Cloud LLM is not configured for this engagement"

# Slice 14 (§5.1 / Risk 1): a turn whose content matches a secret pattern reaches the cloud
# egress point WITHOUT a per-send confirmation (e.g. it was POSTed under local_only and the
# engagement was flipped to cloud_enabled before streaming). The turn fails — the message is
# never sent — and the user re-sends to get the friction modal. Stable, non-leaky.
EGRESS_UNCONFIRMED_MESSAGE = (
    "This message may contain a secret and was not confirmed for cloud egress — re-send to confirm"
)


class EngagementArchivedError(ConflictError):
    """Raised when a new chat message targets an archived engagement (§4 read-only).

    Subclasses the core ``ConflictError`` so the registered handler maps it to HTTP 409;
    existing messages remain browsable (the GET history endpoint still works).
    """

    def __init__(self, message: str = "Engagement is archived (read-only)") -> None:
        super().__init__(message)


class EgressConfirmationRequiredError(ConflictError):
    """Raised when a cloud_enabled send matched a likely-secret pattern without confirmation.

    The §5.1 pattern-friction gate (Decision 1): server-authoritative, fired at the POST
    before the pending row exists and before any token can leave the machine. Subclasses
    ``ConflictError`` (→ 409); the router renders the matched category NAMES (never the secret
    value, §5.5) into the friction modal body. Carries only the category names.
    """

    def __init__(self, *, matched_categories: list[str]) -> None:
        self.matched_categories = matched_categories
        super().__init__("Message may contain a secret; egress confirmation required")


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


def _as_uuid(value: object) -> UUID:
    """Normalize a model id column to ``uuid.UUID`` (mirrors ``subset_builder._uuid``).

    ``load_live_graph`` returns ``uuid.UUID`` at runtime; this keeps both mypy configs happy
    without scattering ``cast`` at each use."""
    return value if isinstance(value, UUID) else UUID(str(value))


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
    confirmed_egress: bool = False,
    persona_id: UUID | None = None,
) -> SendChatMessageResult:
    """Persist the user message + an empty ``pending`` assistant placeholder.

    Membership chokepoint (404 for non-members/missing, §17.1), then an archived-
    engagement guard (409, §4), then — on a ``cloud_enabled`` engagement only — the §5.1
    pattern-friction egress gate (Decision 1): the content is re-scanned server-side
    (authoritative; the client scan is only UX), and a secret-matching send that was NOT
    confirmed raises ``EgressConfirmationRequiredError`` (409) *before* the pending row exists
    and before any token can leave the machine. A ``local_only`` send is never scanned (no
    egress to gate, §5.5). The content is NEVER modified (§5.5) — the scan only flags.

    The assistant reply is streamed separately over ``WS /ws/chat/{assistant_message_id}``.
    The caller (router) commits.

    The three id lists are the client-supplied §5.3 union inputs (Slice 12); the egress
    decision (Slice 14) is stashed alongside them on the pending assistant row (Decision 4)
    so the WS streamer can record it in the ``ai_call`` audit payload at finalize. They are
    NOT resolved or trusted here.
    """
    member = await eng_repo.get_engagement_for_member(db, engagement_id, _user_id(requester))
    if member is None:
        raise NotFoundError("Engagement not found")
    engagement, _membership = member
    if engagement.status == "archived":
        raise EngagementArchivedError()

    egress = _evaluate_egress(engagement.privacy_mode, content, confirmed_egress)

    # Resolve the selected persona for this turn (§5.3, Slice 15). A built-in or one of the
    # caller's own personas is used; an unknown/foreign id falls back to general (§17.1 —
    # never errors, never another user's prompt). Resolving HERE (not only at stream time)
    # lets the read schema + the audit reflect the actual persona even if it is deleted
    # before streaming; the streamer re-resolves for defense in depth (Risk 6 / TOCTOU).
    persona = await personas_service.resolve_for_turn(
        db, persona_id=persona_id, user_id=_user_id(requester)
    )

    user_message, assistant_message = await chat_repo.insert_user_and_pending_assistant(
        db,
        engagement_id=engagement_id,
        user_id=_user_id(requester),
        content=content,
        graph_context=_input_stash(
            pinned_node_ids,
            recent_node_ids,
            mentioned_node_ids,
            egress=egress,
            persona=persona,
        ),
    )
    return SendChatMessageResult(
        user_message=ChatMessageRead.model_validate(user_message),
        assistant_message=ChatMessageRead.model_validate(assistant_message),
    )


def _evaluate_egress(privacy_mode: str, content: str, confirmed_egress: bool) -> dict[str, Any]:
    """Run the §5.1 pattern-friction gate for one send; return the egress decision to stash.

    On a ``cloud_enabled`` engagement the content is scanned for likely-secret patterns
    (``egress_scan``, server-authoritative — Risk 3). A match that was NOT confirmed raises
    ``EgressConfirmationRequiredError`` (the friction 409) carrying the category NAMES only
    (never the value, §5.5). A ``local_only`` send is never scanned — there is no cloud egress
    to gate (§5.5). The scan only flags; the content is never modified (§5.5 / Risk 2).
    """
    if privacy_mode != "cloud_enabled":
        return {"secret_flagged": False, "confirmed": False, "match_categories": []}
    categories = egress_scan.category_names(content)
    if categories and not confirmed_egress:
        raise EgressConfirmationRequiredError(matched_categories=categories)
    flagged = bool(categories)
    return {
        "secret_flagged": flagged,
        # A confirmed send is audited as confirmed; a clean send has nothing to confirm.
        "confirmed": flagged and confirmed_egress,
        "match_categories": categories,
    }


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
        items=[_to_message_read(r) for r in rows],
        next_cursor=next_cursor,
        low_confidence_threshold=get_settings().ADEPTUS_CHAT_LOW_CONFIDENCE_THRESHOLD,
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
    system_prompt: str = SYSTEM_PROMPT,
    context_block: str = "",
) -> list[OllamaChatMessage]:
    """Build the Ollama messages array: system + completed window verbatim (§5.4/§5.5).

    Skips the in-flight assistant placeholder and any non-complete/empty rows; the
    triggering user message (always ``complete``) is included unchanged — no redaction.

    ``system_prompt`` (Slice 15) is the selected persona's distinct prompt and REPLACES the
    base term; it defaults to the neutral ``SYSTEM_PROMPT`` (= the ``general`` built-in) so a
    no-persona turn is byte-identical to the pre-slice prompt. ``context_block`` (Slice 12)
    is the rendered §5.3 relevant subset, appended after the persona prompt verbatim (§5.5)
    when non-empty. The Slice-13 structured-output instruction is appended last, after the
    (optional) context block, so the model always sees it (composition order unchanged).
    """
    base = f"{system_prompt}\n\n{context_block}" if context_block else system_prompt
    system_content = f"{base}{PLAN_CERTAINTY_INSTRUCTION}"
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


async def stream_assistant_reply(  # noqa: C901 — irreducible yielding stream loop (see docstring)
    *,
    message: ChatMessage,
) -> AsyncGenerator[WebSocketChatChunk, None]:
    """Stream the assistant reply for an authenticated assistant message.

    Opens a FRESH session (the auth session is already closed). Re-reads the row so the
    terminal-state branches use current DB state (reconnect-safe):

      - ``complete``: replay the stored content as one ``token`` frame, then ``done``.
      - ``failed``:   emit the stored failure reason as an ``error`` frame.
      - ``pending``:  pick the backend by the engagement's privacy mode (Slice 14, Decision 2
        — cloud Claude when ``cloud_enabled`` + a key is configured, else local Ollama; a
        ``cloud_enabled`` engagement with NO key fails the turn rather than silently using
        local, §5.1), build the prompt, stream token-by-token, persist the final
        content/status + emit the ``ai_call`` audit entry (atomic, widened with the backend +
        egress decision), then ``done``; on ``LlmUnreachableError`` (or a wedged socket)
        persist ``failed``, emit ``ai_call`` with ``status=failed``, and ``error``.

    The ``noqa: C901`` is retained because the irreducible core is a yielding token loop
    whose failure branch must ``return`` from *this* generator (it cannot be hoisted into a
    helper without losing the early-return semantics). The finalize/parse/persist/audit work
    is already extracted to ``_finalize_complete_turn``.
    """
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
            # Reconnect replay: re-emit the stored (already block-stripped) prose, then a
            # done frame carrying the stored plan/claims so the panel + badges re-render
            # on a reconnect exactly as on the live turn (Slice 13).
            if current.content:
                yield WebSocketChatChunk(type="token", data=current.content)
            stored_plan, stored_claims = _stored_plan_claims(current)
            yield WebSocketChatChunk(type="done", plan=stored_plan, claims=stored_claims)
            return
        if current.status == "failed":
            yield WebSocketChatChunk(type="error", message=UNREACHABLE_MESSAGE)
            return

        # status == "pending": real streaming.
        # Slice 14 (Decision 2): choose the backend by the engagement's privacy mode. A
        # cloud_enabled engagement with a configured key streams from Claude; otherwise local
        # Ollama. cloud_enabled with NO key fails the turn — never silently local (§5.1, no
        # auto-fallback). The POST-time egress decision rides on the stash for the audit
        # payload. Both clients share stream_chat's signature, so only the bound fn differs.
        settings = get_settings()
        egress = _stash_egress(current.graph_context)
        # The persona selected at POST time (Slice 15). Used for the audit on the early-fail
        # paths below; re-resolved authoritatively before the prompt is built (Risk 6).
        stashed_persona_id, stashed_persona_name = _stash_persona(current.graph_context)
        stashed_persona_id_str = str(stashed_persona_id) if stashed_persona_id is not None else None
        privacy_mode = await _engagement_privacy_mode(session, engagement_id, user_id)
        if privacy_mode == "cloud_enabled" and not settings.ADEPTUS_ANTHROPIC_API_KEY:
            await _finalize_failed(
                session,
                message_id=message_id,
                actor_user_id=user_id,
                engagement_id=engagement_id,
                model_name=settings.ADEPTUS_ANTHROPIC_MODEL,
                prompt_count=0,
                backend="cloud",
                egress=egress,
                persona_id=stashed_persona_id_str,
                persona_name=stashed_persona_name,
            )
            yield WebSocketChatChunk(type="error", message=CLOUD_NOT_CONFIGURED_MESSAGE)
            return
        if privacy_mode == "cloud_enabled":
            backend = "cloud"
            model_name = settings.ADEPTUS_ANTHROPIC_MODEL
            stream_fn = anthropic_client.stream_chat
        else:
            backend = "local"
            model_name = settings.ADEPTUS_LLM_MODEL
            stream_fn = ollama_client.stream_chat

        window = await chat_repo.recent_messages(
            session, engagement_id=engagement_id, user_id=user_id, limit=RECENT_WINDOW
        )

        # Authoritative egress re-check at the ACTUAL egress point (Risk 1 / TOCTOU). The POST
        # gate scanned under the POST-time privacy mode, but the backend is chosen here from the
        # *live* mode — a flip from local_only → cloud_enabled between the POST and this stream
        # would otherwise send a never-scanned secret to the cloud with no friction. So on the
        # cloud branch we re-scan the triggering user message and refuse an unconfirmed match
        # before any token leaves the machine (§5.1 / §17.5); a turn confirmed at the POST
        # (stash ``confirmed``) is allowed through. Never falls back to local (§5.1).
        if backend == "cloud":
            rescan = egress_scan.category_names(_triggering_user_text(window))
            if rescan and not egress["confirmed"]:
                egress = {"secret_flagged": True, "confirmed": False, "match_categories": rescan}
                await _finalize_failed(
                    session,
                    message_id=message_id,
                    actor_user_id=user_id,
                    engagement_id=engagement_id,
                    model_name=model_name,
                    prompt_count=0,
                    backend="cloud",
                    egress=egress,
                    persona_id=stashed_persona_id_str,
                    persona_name=stashed_persona_name,
                )
                yield WebSocketChatChunk(type="error", message=EGRESS_UNCONFIRMED_MESSAGE)
                return

        # Build the §5.3 relevant subset from the POST-time stash + the live graph, and
        # prepend it to the system prompt (Slice 12). On an empty graph / empty subset the
        # context block is empty and the prompt is exactly the Slice-11 prompt. The canonical
        # debug record (resolved subset + raw prompt) is persisted at finalize for §14.
        subset, live_node_ids = await _build_turn_subset(
            session,
            engagement_id=engagement_id,
            message_text=_triggering_user_text(window),
            stash=current.graph_context,
        )
        # Re-resolve the persona at stream time (defense in depth, Risk 6 / TOCTOU): a persona
        # deleted between POST and stream falls back to general; its prompt REPLACES the base
        # system prompt (Decision 4). The id + name are recorded on the turn + audit.
        persona = await personas_service.resolve_for_turn(
            session, persona_id=stashed_persona_id, user_id=user_id
        )
        persona_id_str, persona_name = _persona_audit(persona)
        prompt = _build_prompt(
            window,
            current_assistant_id=message_id,
            system_prompt=persona.system_prompt,
            context_block=subset.context_block,
        )
        debug_record = _debug_record(subset, _render_raw_prompt(prompt), persona=persona)

        usage = OllamaUsage()
        # full accumulates the raw reply (incl. the metadata block); emitted tracks how much
        # has been streamed as clean prose. Once the sentinel marker is seen we stop emitting
        # so the raw <adeptus-meta> block never reaches the client (Risk 2). A partial-marker
        # suffix is withheld each step so a marker split across token boundaries is caught.
        full = ""
        emitted = 0
        block_started = False
        agen = stream_fn(messages=prompt, model=model_name, usage=usage).__aiter__()

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
                    backend=backend,
                    egress=egress,
                    persona_id=persona_id_str,
                    persona_name=persona_name,
                )
                yield WebSocketChatChunk(type="error", message=UNREACHABLE_MESSAGE)
                return
            full += token
            if not block_started:
                safe, block_started = _safe_prose_len(full)
                if safe > emitted:
                    yield WebSocketChatChunk(type="token", data=full[emitted:safe])
                    emitted = safe
        # The stream ended without the marker turning up: the tail we were withholding (a
        # false-positive partial marker) is real prose, so flush it.
        if not block_started and emitted < len(full):
            yield WebSocketChatChunk(type="token", data=full[emitted:])

        # Parse, validate, persist, and audit the completed turn (extracted to keep this
        # generator readable); then deliver the parsed plan/claims on the done frame.
        plan, claims = await _finalize_complete_turn(
            session,
            message_id=message_id,
            actor_user_id=user_id,
            engagement_id=engagement_id,
            model_name=model_name,
            prompt_count=len(prompt),
            full_reply=full,
            debug_record=debug_record,
            live_node_ids=live_node_ids,
            subset=subset,
            usage=usage,
            backend=backend,
            egress=egress,
            persona_id=persona_id_str,
            persona_name=persona_name,
        )
        yield WebSocketChatChunk(type="done", plan=plan, claims=claims)


async def _finalize_complete_turn(
    session: AsyncSession,
    *,
    message_id: UUID,
    actor_user_id: UUID,
    engagement_id: UUID,
    model_name: str,
    prompt_count: int,
    full_reply: str,
    debug_record: dict[str, Any],
    live_node_ids: set[UUID],
    subset: subset_builder.GraphSubset,
    usage: OllamaUsage,
    backend: str = "local",
    egress: dict[str, Any] | None = None,
    persona_id: str | None = None,
    persona_name: str | None = None,
) -> tuple[list[PlanStep], list[Claim]]:
    """Parse, validate, persist, and audit a completed turn; return its plan + claims.

    Splits the §5.3 metadata block off ``full_reply``, validates each claim's ``node_id``
    against the engagement's live graph (foreign/unknown dropped, §17.1), persists the
    block-stripped prose as ``content`` plus plan/claims/unstripped-output into the per-turn
    JSONB (merging the Slice-12 keys + the Slice-15 persona keys carried on ``debug_record``,
    Risk 6/7), and emits exactly one ``ai_call`` — only when this call won the pending→
    terminal transition (a racing socket gets ``None`` and must not re-emit, Risk 6). The
    caller yields the returned plan/claims on the ``done`` frame.
    """
    prose, plan, claims = plan_parser.extract(full_reply)
    claims = _validate_claim_node_ids(claims, live_node_ids)
    graph_context = _finalize_record(
        debug_record, plan=plan, claims=claims, model_output=full_reply
    )

    finalized = await chat_repo.finalize_assistant(
        session,
        message_id=message_id,
        content=prose,
        status="complete",
        model=model_name,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
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
            status="complete",
            nodes_injected=subset.nodes_injected,
            edges_injected=subset.edges_injected,
            plan_steps=len(plan),
            claims_count=len(claims),
            backend=backend,
            egress=egress,
            persona_id=persona_id,
            persona_name=persona_name,
        )
    await session.commit()
    return plan, claims


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
    backend: str = "local",
    egress: dict[str, Any] | None = None,
    persona_id: str | None = None,
    persona_name: str | None = None,
) -> None:
    """Persist the assistant row ``failed`` + emit the ``ai_call`` audit entry, atomic.

    Like the success path, the ``ai_call`` is emitted only when this call won the
    pending→failed transition (Risk 6 — exactly one ai_call per turn). The §14 debug record
    (the resolved subset + raw prompt, incl. the Slice-15 persona keys) is persisted even on
    failure so the debug panel can show what the AI was shown on a turn that never produced
    output (model_output empty). On the early-fail paths ``graph_context`` is None (the
    POST-time stash — which still carries the persona — is left intact); the persona id/name
    passed here are recorded in the ``ai_call`` payload regardless."""
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
            backend=backend,
            egress=egress,
            persona_id=persona_id,
            persona_name=persona_name,
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
    plan_steps: int = 0,
    claims_count: int = 0,
    backend: str = "local",
    egress: dict[str, Any] | None = None,
    persona_id: str | None = None,
    persona_name: str | None = None,
) -> None:
    """Record one ``ai_call`` audit entry attributed to the acting user (§14).

    The payload carries the §5.3 subset *counts* (Slice 12), the Slice-13 plan/claim counts,
    the Slice-14 egress decision: ``backend`` (``local``|``cloud``), ``egress_secret_flagged``,
    ``egress_confirmed``, ``egress_match_categories`` (pattern category NAMES only — NEVER the
    matched secret value, §5.5 / Risk 7), and — from Slice 15 — the ``persona_id`` +
    ``persona_name`` that shaped the turn (the name is a non-secret display label). No new
    audit action/table, just a widened payload (the ADR-0010 hash-chain covers it as written,
    Resolved decision 3)."""
    decision = egress if egress is not None else {}
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
            "plan_steps": plan_steps,
            "claims_count": claims_count,
            "backend": backend,
            "egress_secret_flagged": bool(decision.get("secret_flagged", False)),
            "egress_confirmed": bool(decision.get("confirmed", False)),
            "egress_match_categories": list(decision.get("match_categories", [])),
            "persona_id": persona_id,
            "persona_name": persona_name,
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
    pinned: Sequence[UUID],
    recent: Sequence[UUID],
    mentioned: Sequence[UUID],
    *,
    egress: dict[str, Any] | None = None,
    persona: Persona | None = None,
) -> dict[str, Any]:
    """Serialize the client-supplied §5.3 union inputs for the pending-row stash (Decision 4).

    Stored as JSON-safe id strings under ``inputs`` so the streamer can re-resolve them
    against the live graph at stream time; overwritten with the canonical subset at finalize.
    The Slice-14 ``egress`` decision (``secret_flagged``/``confirmed``/``match_categories`` —
    category NAMES only, §5.5) rides alongside under ``egress``. The Slice-15 ``persona``
    (resolved id + name) rides under ``persona`` so the streamer can re-resolve it and the
    read schema / audit reflect it even before the canonical record is written at finalize.
    """
    stash: dict[str, Any] = {
        "inputs": {
            "pinned_node_ids": [str(x) for x in pinned],
            "recent_node_ids": [str(x) for x in recent],
            "mentioned_node_ids": [str(x) for x in mentioned],
        }
    }
    if egress is not None:
        stash["egress"] = egress
    if persona is not None:
        stash["persona"] = _persona_stash(persona)
    return stash


def _persona_stash(persona: Persona) -> dict[str, Any]:
    """The JSON-safe persona record (id + name) stashed on the pending row (§5.3, Slice 15).

    ``id`` is None only for the synthesized-general fallback when the built-ins are unseeded;
    ``name`` is the verbatim display name (non-secret, §5.5), denormalized so a renamed/
    deleted persona still labels the turn."""
    return {
        "id": str(persona.id) if persona.id is not None else None,
        "name": persona.name,
    }


def _opt_uuid(value: object) -> UUID | None:
    """Parse a stashed id string back to UUID, or None when absent/malformed."""
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def _stash_persona(stash: dict[str, Any] | None) -> tuple[UUID | None, str | None]:
    """Read the POST-time persona (id, name) back from the pending row's stash (Slice 15).

    Tolerates absence (pre-slice rows, user rows) → (None, None)."""
    raw = stash.get("persona") if isinstance(stash, dict) else None
    if not isinstance(raw, dict):
        return None, None
    name = raw.get("name")
    return _opt_uuid(raw.get("id")), name if isinstance(name, str) else None


async def _engagement_privacy_mode(
    session: AsyncSession, engagement_id: UUID, user_id: UUID
) -> str:
    """Read the engagement's privacy mode at stream time (Slice 14, Decision 2).

    Re-runs the membership chokepoint (a defensive bonus — membership may have been revoked
    between the POST and the WS stream, §17.1). If membership is gone, default to
    ``local_only`` — the data-safe choice (a revoked member never reaches the cloud branch)."""
    member = await eng_repo.get_engagement_for_member(session, engagement_id, user_id)
    if member is None:
        return "local_only"
    return str(member[0].privacy_mode)


def _stash_egress(stash: dict[str, Any] | None) -> dict[str, Any]:
    """Read the Slice-14 egress decision back from the pending row's stash for the audit.

    Tolerates absence (local rows, pre-slice rows) → a clean, unflagged decision. Carries
    only category NAMES (§5.5), exactly as written by ``send_message``."""
    raw = stash.get("egress") if isinstance(stash, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    categories = raw.get("match_categories")
    return {
        "secret_flagged": bool(raw.get("secret_flagged", False)),
        "confirmed": bool(raw.get("confirmed", False)),
        "match_categories": [str(c) for c in categories] if isinstance(categories, list) else [],
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
) -> tuple[subset_builder.GraphSubset, set[UUID]]:
    """Resolve the stashed inputs + the engagement's live graph into the §5.3 subset.

    Reads the live graph via the existing ``graph.repository.load_live_graph`` read path
    (engagement-scoped, non-deleted only); chat never writes the graph (ADR-0001). Returns
    the subset AND the set of every live node id (Slice 13): a claim may reference any live
    node, not just one in the subset, so the finalize step validates claim ``node_id``s
    against this full set (foreign/unknown ids dropped, §17.1)."""
    inputs = stash.get("inputs", {}) if isinstance(stash, dict) else {}
    inputs = inputs if isinstance(inputs, dict) else {}
    nodes, edges = await graph_repo.load_live_graph(session, engagement_id)
    settings = get_settings()
    subset = subset_builder.build(
        nodes=nodes,
        edges=edges,
        message_text=message_text,
        pinned_node_ids=_parse_uuid_list(inputs.get("pinned_node_ids")),
        recent_node_ids=_parse_uuid_list(inputs.get("recent_node_ids")),
        mentioned_node_ids=_parse_uuid_list(inputs.get("mentioned_node_ids")),
        n_recent=settings.ADEPTUS_GRAPH_CONTEXT_RECENT_LIMIT,
        k_mentioned=settings.ADEPTUS_GRAPH_CONTEXT_MENTIONED_LIMIT,
    )
    live_node_ids = {_as_uuid(n.id) for n in nodes}
    return subset, live_node_ids


def _render_raw_prompt(prompt: Sequence[OllamaChatMessage]) -> str:
    """Render the Ollama messages array to the verbatim raw-prompt text (§14 "raw prompts")."""
    return "\n\n".join(f"[{m.role}]\n{m.content}" for m in prompt)


def _debug_record(
    subset: subset_builder.GraphSubset, raw_prompt: str, *, persona: Persona
) -> dict[str, Any]:
    """The base per-turn §14 debug record persisted into ``chat_messages.graph_context``.

    Holds the Slice-12 keys (subset nodes/edges + context_block + raw_prompt) plus the
    Slice-15 ``persona_id`` / ``persona_name`` for the persona that shaped this turn (carried
    through to both finalize paths so the read schema labels the turn even on a failed turn).
    The Slice-13 plan/claims/model_output keys are layered on by ``_finalize_record`` on the
    success path; on a failed turn this base record is stored as-is."""
    return {
        "nodes": [n.model_dump(mode="json") for n in subset.nodes],
        "edges": [e.model_dump(mode="json") for e in subset.edges],
        "context_block": subset.context_block,
        "raw_prompt": raw_prompt,
        "persona_id": str(persona.id) if persona.id is not None else None,
        "persona_name": persona.name,
    }


def _persona_audit(persona: Persona) -> tuple[str | None, str]:
    """The (persona_id-str, persona_name) recorded on the turn + in the ``ai_call`` audit."""
    return (str(persona.id) if persona.id is not None else None, persona.name)


def _finalize_record(
    debug_record: dict[str, Any],
    *,
    plan: list[PlanStep],
    claims: list[Claim],
    model_output: str,
) -> dict[str, Any]:
    """Merge the Slice-13 plan/claims/raw-output keys onto the Slice-12 debug record.

    Merging (not overwriting) keeps both Slice-12 (subset/raw_prompt) and Slice-13
    (plan/claims) data on the one JSONB blob (Risk 6). ``model_output`` is the UNSTRIPPED
    reply (with the metadata block) so the §14 debug panel can show exactly what was parsed,
    while the row's ``content`` holds the block-stripped prose."""
    return {
        **debug_record,
        "plan": [p.model_dump(mode="json") for p in plan],
        "claims": [c.model_dump(mode="json") for c in claims],
        "model_output": model_output,
    }


def _validate_claim_node_ids(claims: list[Claim], live_node_ids: set[UUID]) -> list[Claim]:
    """Drop a claim's ``node_id`` when it is not a live node of this engagement (§17.1).

    The claim text + certainty always survive; only an unknown/foreign id is nulled so the
    Graph-pane badge can never point at a hallucinated id or a node from another engagement
    (Risk 3). A second user's nodes are never in this engagement's live set, so cross-
    engagement references are dropped here too."""
    validated: list[Claim] = []
    for claim in claims:
        if claim.node_id is not None and claim.node_id not in live_node_ids:
            validated.append(Claim(text=claim.text, certainty=claim.certainty, node_id=None))
        else:
            validated.append(claim)
    return validated


def _to_message_read(message: ChatMessage) -> ChatMessageRead:
    """Map a persisted row into the read schema, populating plan/claims (Slice 13).

    The render-needed plan/claims ride on the normal message read so a reloaded
    conversation re-renders the Plan panel + in-chat certainty badges without the lazy
    debug call. User/pending/pre-slice rows have no stored plan/claims → empty lists."""
    plan, claims = _stored_plan_claims(message)
    persona_id, persona_name = _stored_persona(message)
    return ChatMessageRead(
        id=cast(UUID, message.id),
        engagement_id=cast(UUID, message.engagement_id),
        role=ChatRole(message.role),
        content=message.content,
        status=ChatMessageStatus(message.status),
        created_at=message.created_at,
        plan=plan,
        claims=claims,
        persona_id=persona_id,
        persona_name=persona_name,
    )


def _stored_persona(message: ChatMessage) -> tuple[UUID | None, str | None]:
    """Read the persona (id, name) for a turn back from its ``graph_context`` JSONB (Slice 15).

    Reads the canonical finalized keys (``persona_id`` / ``persona_name``) when present, else
    falls back to the POST-time stash (``persona.id`` / ``persona.name``) so a pending or
    early-failed turn still carries its selected persona. (None, None) for user/pre-slice rows.
    """
    gc = message.graph_context if isinstance(message.graph_context, dict) else {}
    if "persona_id" in gc or "persona_name" in gc:
        name = gc.get("persona_name")
        return _opt_uuid(gc.get("persona_id")), name if isinstance(name, str) else None
    return _stash_persona(gc)


def _stored_plan_claims(message: ChatMessage) -> tuple[list[PlanStep], list[Claim]]:
    """Read the parsed plan/claims back from an assistant row's ``graph_context`` JSONB.

    Tolerates the keys being absent (pre-slice rows, user rows, failed turns) → empty
    lists. The stored shapes were written by us via ``model_dump`` so re-validation is
    lossless; a corrupt blob degrades to empty rather than breaking a read (graceful)."""
    gc = message.graph_context if isinstance(message.graph_context, dict) else {}
    raw_plan = gc.get("plan")
    raw_claims = gc.get("claims")
    try:
        plan = [PlanStep.model_validate(p) for p in raw_plan] if isinstance(raw_plan, list) else []
        claims = (
            [Claim.model_validate(c) for c in raw_claims] if isinstance(raw_claims, list) else []
        )
    except (ValueError, TypeError):
        return [], []
    return plan, claims


def _safe_prose_len(full: str) -> tuple[int, bool]:
    """How many leading chars of ``full`` are safe to stream as prose, and whether the
    sentinel block has started.

    If the full START marker is present, prose ends at it (block_started=True). Otherwise
    withhold the longest suffix of ``full`` that is a prefix of the marker, so a marker
    split across token boundaries (e.g. ``<adeptus`` then ``-meta>``) is never half-emitted.
    """
    idx = full.find(plan_parser.START_MARKER)
    if idx != -1:
        return idx, True
    marker = plan_parser.START_MARKER
    max_overlap = min(len(full), len(marker) - 1)
    for k in range(max_overlap, 0, -1):
        if full.endswith(marker[:k]):
            return len(full) - k, False
    return len(full), False


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
    empty-subset state.

    ``model_output`` (Slice 13) is the UNSTRIPPED reply persisted in ``graph_context``
    (incl. the metadata block, so the §14 panel shows exactly what was parsed); a pre-slice
    row has no stored output and falls back to the row's ``content`` (no block existed then).
    ``plan``/``claims`` are the parsed structures read back from the same blob (empty for
    pre-slice / pending / failed rows).
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
    plan, claims = _stored_plan_claims(message)
    stored_output = gc.get("model_output")
    model_output = stored_output if isinstance(stored_output, str) else message.content
    return ChatTurnDebug(
        message_id=cast(UUID, message.id),
        model=message.model,
        status=ChatMessageStatus(message.status),
        nodes=nodes,
        edges=edges,
        context_block=str(gc.get("context_block", "")),
        raw_prompt=str(gc.get("raw_prompt", "")),
        model_output=model_output,
        plan=plan,
        claims=claims,
    )
