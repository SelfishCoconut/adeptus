"""Business logic for MCP server registry and tool-run execution.

Domain exceptions raised here are translated to HTTP codes in router.py:
  - EngagementNotFound → 404
  - McpServerNotFound  → 400
  - McpServerDown      → 503

Membership check (§4 no-admin-bypass + §17.1 isolation):
  A single fused query (engagements.repository.get_engagement_for_member) is the
  §17.1 isolation chokepoint: it returns the (Engagement, EngagementMember) pair
  only when the caller has an explicit member row, and None otherwise. Both
  "engagement does not exist" and "caller is not a member" collapse to the same
  EngagementNotFound (→ 404) so a non-member cannot infer that the engagement
  exists — matching the established engagements-feature posture.

  Admin role is irrelevant here — the query never consults role, so an admin
  without an explicit membership row is denied exactly like any other user
  (§4 no admin bypass). The denial is a 404, not a 403, to avoid existence
  disclosure.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.core.errors import BadRequestError, ForbiddenError, NotFoundError
from app.features.auth import repository as auth_repo
from app.features.engagements import repository as eng_repo
from app.features.mcp import repository as mcp_repo
from app.features.mcp import subprocess_manager
from app.features.mcp.models import ToolRun
from app.features.mcp.registry import get_registry
from app.features.mcp.schemas import (
    McpServerInfo,
    McpToolDeclaration,
    ToolDescriptor,
    ToolPreset,
    ToolRunPage,
    ToolRunResult,
    ToolRunStatus,
    WebSocketOutputChunk,
)
from app.features.mcp.subprocess_manager import (
    McpServerNotFound,
    StreamChunk,
    StreamDone,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# In-process pub/sub for streaming tool-run output
# ---------------------------------------------------------------------------


@dataclass
class _RunChannel:
    """Per-run pub/sub channel with a replay buffer for mid-run reconnects."""

    replay: list[WebSocketOutputChunk] = field(default_factory=list)
    subscribers: set[asyncio.Queue[WebSocketOutputChunk]] = field(default_factory=set)
    done: bool = False


# Module-level channel map: tool_run_id → _RunChannel.
# Lives for the duration of the async run; discarded once the final DB row is
# committed (Task 6 / Decision 3).  In-process only — not multi-worker safe
# (documented in Risk 3: acceptable for v1 single-process Compose deployment).
_channels: dict[UUID, _RunChannel] = {}

# Keep a strong reference to background tasks so the GC does not collect them
# before they complete.  (asyncio.create_task() returns a weak-referenced task.)
_background_tasks: set[asyncio.Task[None]] = set()


def broadcast_tool_run_output(tool_run_id: UUID, chunk: WebSocketOutputChunk) -> None:
    """Append *chunk* to the replay buffer and post it to every live subscriber.

    Creates the channel on first call so callers do not need to pre-create it.
    Safe to call from a background task (no await needed — Queue.put_nowait is
    synchronous).
    """
    channel = _channels.setdefault(tool_run_id, _RunChannel())
    channel.replay.append(chunk)
    for queue in channel.subscribers:
        queue.put_nowait(chunk)


def subscribe_tool_run(
    tool_run_id: UUID,
) -> tuple[list[WebSocketOutputChunk], asyncio.Queue[WebSocketOutputChunk]]:
    """Register a new subscriber for *tool_run_id* and return the replay snapshot.

    Returns a ``(replay_snapshot, queue)`` tuple.  The caller should first send
    every chunk in *replay_snapshot* to the client, then read *queue* for live
    chunks.  The snapshot is a copy of the buffer at subscription time; chunks
    that arrive after the snapshot but before the caller starts reading *queue*
    will appear in both the snapshot of the *next* subscriber and in the queue —
    the WS handler must deduplicate if needed, but in practice the snapshot +
    queue approach is race-free because broadcast always writes to both at once.
    """
    channel = _channels.setdefault(tool_run_id, _RunChannel())
    queue: asyncio.Queue[WebSocketOutputChunk] = asyncio.Queue()
    channel.subscribers.add(queue)
    return list(channel.replay), queue


def try_subscribe_tool_run(
    tool_run_id: UUID,
) -> tuple[list[WebSocketOutputChunk], asyncio.Queue[WebSocketOutputChunk]] | None:
    """Subscribe to an *existing* channel without creating one if it is absent.

    Returns ``(replay_snapshot, queue)`` if a live channel exists for
    *tool_run_id*, or ``None`` if no channel exists (the run has already
    completed or was never started in async mode).

    This is the non-creating variant of ``subscribe_tool_run``.  Use this in
    the WebSocket handler so a late connect to a finished run does not
    fabricate a channel that would block forever.
    """
    channel = _channels.get(tool_run_id)
    if channel is None:
        return None
    queue: asyncio.Queue[WebSocketOutputChunk] = asyncio.Queue()
    channel.subscribers.add(queue)
    return list(channel.replay), queue


def unsubscribe_tool_run(tool_run_id: UUID, queue: asyncio.Queue[WebSocketOutputChunk]) -> None:
    """Remove *queue* from the channel's live subscribers.

    Safe to call even if the channel has already been discarded (guard included).
    """
    channel = _channels.get(tool_run_id)
    if channel is not None:
        channel.subscribers.discard(queue)


def _discard_channel(tool_run_id: UUID) -> None:
    """Mark the channel done and remove it from the module-level map.

    The replay buffer is discarded; late WebSocket subscribers fall back to the
    persisted DB row (Task 7).
    """
    channel = _channels.pop(tool_run_id, None)
    if channel is not None:
        channel.done = True


def _reset_channels() -> None:
    """Clear all channels.  For use in tests only."""
    _channels.clear()
    _background_tasks.clear()


# ---------------------------------------------------------------------------
# Cursor encoding helpers
# ---------------------------------------------------------------------------


def _encode_cursor(started_at: datetime, run_id: UUID) -> str:
    """Encode a (started_at, id) pair as an opaque base64url string."""
    raw = f"{started_at.isoformat()}|{run_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Decode an opaque cursor string back to (started_at, id).

    Raises ValueError when the cursor is malformed.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts_part, id_part = raw.split("|", 1)
        return datetime.fromisoformat(ts_part), UUID(id_part)
    except Exception as exc:
        raise ValueError(f"Malformed cursor: {cursor!r}") from exc


# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class EngagementNotFound(NotFoundError):
    """Raised when the requested engagement does not exist.

    Subclasses the core ``NotFoundError`` so the registered error handler maps
    it to HTTP 404 — no inline translation needed in the router.
    """

    def __init__(self, message: str = "Engagement not found") -> None:
        super().__init__(message)


class SandboxGuardViolation(ForbiddenError):
    """Raised when a tool run targets a host outside the sandbox allow-list.

    Subclasses the core ``ForbiddenError`` so the registered error handler maps
    it to HTTP 403 — no inline translation needed in the router.

    Active only when ``ADEPTUS_ENV`` is not ``"production"`` (Risk 5: fail-closed
    default — an unset or unrecognised value is treated as a guarded environment).
    """

    def __init__(self, message: str = "Target is outside the sandbox allow-list") -> None:
        super().__init__(message)


# ---------------------------------------------------------------------------
# Sandbox guard
# ---------------------------------------------------------------------------

# Hosts that are allowed as tool targets in dev/test environments.
_SANDBOX_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "juice-shop"})


def _enforce_sandbox_guard(args: dict[str, Any]) -> None:
    """Raise SandboxGuardViolation if ``args["target"]`` is outside the sandbox.

    Guard logic:
    - If ``ADEPTUS_ENV == "production"``: no-op (guard disabled in production).
    - If ``args`` contains no ``"target"`` key, or the value is not a non-empty
      string: no-op (tools without a ``target`` field, e.g. run_command, are not
      guarded at this layer; run_command guarding is deferred to Slice 16 via the
      approval-gating mechanism — Risk 5).
    - Otherwise extract the hostname from ``target`` (handles full URLs such as
      ``http://localhost:3000`` and bare host[:port] strings such as ``localhost``),
      strip any port suffix, lowercase, and check against ``_SANDBOX_HOSTS``.

    Reconciliation note: the slice task text mentions guarding both ``target``
    (run_httpx) and ``command`` (run_command).  Risk 5 in the same spec clarifies
    that run_command has NO target guard at this layer because it surfaces no
    ``target`` field.  We guard generically on the presence of a non-empty
    ``target`` arg key: run_httpx is covered; run_command is not (deferred to
    Slice 16); future target-bearing tools are covered automatically.

    Env-read source: ``os.environ.get("ADEPTUS_ENV", "dev")``.  The ``Settings``
    object in ``app.core.config`` exposes an ``ENVIRONMENT`` field with a default
    of ``"production"``, which is fail-OPEN — unusable here.  This function reads
    ``ADEPTUS_ENV`` directly so that an unset variable defaults to ``"dev"``
    (fail-closed per Risk 5).  Only the exact value ``"production"`` disables the
    guard.
    """
    env = os.environ.get("ADEPTUS_ENV", "dev")
    if env == "production":
        return  # Guard is a no-op in production.

    target = args.get("target")
    if not isinstance(target, str) or not target:
        return  # Nothing to guard for tools without a target.

    # Extract the hostname from the target value.
    # urlparse handles full URLs: ``http://localhost:3000`` → netloc ``localhost:3000``.
    # For bare strings like ``localhost`` or ``juice-shop:3000``, urlparse treats
    # them as ``scheme:path`` (scheme="localhost", path="3000") so netloc is empty.
    # In that case parse the raw target string directly: split on ``:`` and take the
    # first component to strip any port, then strip any path suffix.
    parsed = urlparse(target)
    if parsed.netloc:
        # Full URL with scheme: use parsed.hostname which already strips the port
        # AND any ``user:pass@`` userinfo (so ``http://localhost@evil.com`` → evil.com).
        host = parsed.hostname or ""
    else:
        # Bare host[:port][/path] — urlparse gives no netloc. Re-parse with a
        # synthetic ``//`` so the stdlib parser extracts the true authority. This
        # is what defeats userinfo smuggling: a naive ``split(':')[0]`` reads
        # ``localhost:3000@evil.com`` as the sandbox host ``localhost``, but the
        # httpx binary would actually scan ``evil.com``. urlparse('//...').hostname
        # correctly returns ``evil.com`` here, so the guard blocks it.
        host = urlparse(f"//{target}").hostname or ""

    host = host.lower()

    if host not in _SANDBOX_HOSTS:
        raise SandboxGuardViolation(
            f"Target {host!r} is outside the sandbox allow-list"
            " (dev/test only allow localhost, 127.0.0.1, juice-shop)"
        )


# ---------------------------------------------------------------------------
# list_servers
# ---------------------------------------------------------------------------


async def list_servers() -> list[McpServerInfo]:
    """Return all registered MCP servers with their declared tools and live subprocess status.

    Iterates the static registry and queries the subprocess manager for each
    server's current status (``running`` / ``stopped``).  No DB access required.
    """
    registry = get_registry()
    result: list[McpServerInfo] = []

    for server_name, config in registry.items():
        status = subprocess_manager.get_server_status(server_name)
        tools = [
            McpToolDeclaration(
                name=tool.name,
                weight=cast(Literal["light", "heavy"], tool.weight),
                capability_flags=tool.capability_flags,
            )
            for tool in config.tools
        ]
        result.append(
            McpServerInfo(
                server_name=server_name,
                status=cast(Literal["running", "stopped"], status),
                tools=tools,
            )
        )

    return result


# ---------------------------------------------------------------------------
# list_tools
# ---------------------------------------------------------------------------


async def list_tools() -> list[ToolDescriptor]:
    """Return all configured tools across all registered MCP servers.

    Iterates the static registry and builds a flat list of ToolDescriptor
    objects enriched with preset definitions and arg_schema from the config.

    All tools are included regardless of subprocess running status — the
    descriptor has no status field, so configured tools appear in the tool
    picker even if their subprocess is momentarily down.

    Ordering: registry insertion order, tools in declared order.
    """
    registry = get_registry()
    result: list[ToolDescriptor] = []

    for server_name, config in registry.items():
        for tool in config.tools:
            presets = [
                ToolPreset(
                    name=p.name,
                    description=p.description,
                    args=p.args,
                )
                for p in tool.presets
            ]
            result.append(
                ToolDescriptor(
                    server_name=server_name,
                    tool_name=tool.name,
                    weight=cast(Literal["light", "heavy"], tool.weight),
                    capability_flags=tool.capability_flags,
                    presets=presets,
                    arg_schema=tool.arg_schema,
                )
            )

    return result


# ---------------------------------------------------------------------------
# execute_tool_run
# ---------------------------------------------------------------------------


async def execute_tool_run(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    server_name: str,
    tool_name: str,
    args: dict[str, Any],
    timeout_seconds: int,
    user_id: UUID,
    async_mode: bool = False,
    preset_name: str | None = None,
) -> ToolRunResult:
    """Execute a tool call via the named MCP server and persist the result.

    Sync flow (async_mode=False):
      1. Fused existence + membership check via get_engagement_for_member —
         EngagementNotFound (404) if the engagement is missing OR the caller is
         not an explicit member (no admin bypass per §4; no existence disclosure
         per §17.1).
      2. Server name validation — McpServerNotFound if not in registry.
      3. Insert an in-flight ToolRun row.
      4. Call subprocess_manager.send_tool_call.
         On McpServerDown we propagate the exception to the router (→ 503).
         We do NOT update the row on McpServerDown — leaving exit_code NULL
         indicates the run never completed.  The router is responsible for the 503
         response; crash-recovery cleanup (§13) is handled at startup (Slice 38).
      5. Update the ToolRun row with the raw result.
      6. Return a ToolRunResult assembled from the updated row.

    Async flow (async_mode=True):
      1–2. Same membership + registry checks.
      3. Insert a ToolRun row with status='running'; flush+refresh for id+started_at.
      4. Commit the row immediately so the WS endpoint and the background task
         (which opens its own session) can see it.
      5. Launch _stream_to_channel as an asyncio background task.
      6. Return a partial ToolRunResult (exit_code=None, stdout/stderr='', finished_at=None).

    Args:
        db:               Async database session (caller commits for sync; service
                          commits immediately for async after inserting the row).
        engagement_id:    UUID of the engagement.
        server_name:      Key in the MCP registry.
        tool_name:        Name of the tool on that server.
        args:             Tool-specific argument map forwarded verbatim.
        timeout_seconds:  Per-request wall-clock budget (1–300 s).
        user_id:          ID of the requesting user.
        async_mode:       When True, return immediately after inserting the running row.
        preset_name:      Optional name of the preset the user selected.

    Returns:
        ToolRunResult — fully populated for sync; partial (running) for async.

    Raises:
        EngagementNotFound:  engagement_id does not exist OR user_id is not an
                             explicit member (even if admin — §4/§17.1).
        McpServerNotFound:   server_name not in the registry.
        McpServerDown:       Subprocess not running / timed out (sync path only;
                             async path surfaces failures via WS error chunk).
    """
    # Step 1: fused existence + membership check (§17.1 isolation chokepoint).
    # get_engagement_for_member returns None when the engagement is missing OR the
    # caller has no explicit member row; both collapse to 404 so a non-member
    # cannot infer the engagement exists. Role is never consulted (§4 no-bypass).
    if await eng_repo.get_engagement_for_member(db, engagement_id, user_id) is None:
        raise EngagementNotFound(f"Engagement {engagement_id} not found")

    # Step 2: server name validation.
    registry = get_registry()
    if server_name not in registry:
        raise McpServerNotFound(f"MCP server {server_name!r} is not in the registry")

    # Step 2b: sandbox guard — applies in dev/test for any tool whose args carry a
    # non-empty ``target`` key.  Must run BEFORE any DB row is created so a guarded
    # run never produces a tool_runs row.  Applies to both async and sync paths.
    # See ``_enforce_sandbox_guard`` docstring for the Risk-5 reconciliation note.
    _enforce_sandbox_guard(args)

    if async_mode:
        # Async path: insert running row, commit, launch background task, return partial.
        tool_run = await mcp_repo.create_tool_run(
            db,
            engagement_id=engagement_id,
            server_name=server_name,
            tool_name=tool_name,
            args=args,
            status="running",
            preset_name=preset_name,
        )
        # Commit so the row is durable before we return 202 and hand off to the task.
        # The background task opens its own session and must see the committed row.
        await db.commit()

        tool_run_id: UUID = cast(UUID, tool_run.id)
        task = asyncio.create_task(
            _stream_to_channel(
                tool_run_id=tool_run_id,
                server_name=server_name,
                tool_name=tool_name,
                args=args,
                timeout_seconds=float(timeout_seconds),
            )
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        return ToolRunResult(
            tool_run_id=tool_run_id,
            engagement_id=cast(UUID, tool_run.engagement_id),
            server_name=tool_run.server_name,
            tool_name=tool_run.tool_name,
            exit_code=None,
            stdout="",
            stderr="",
            started_at=tool_run.started_at,
            finished_at=None,
            status="running",
            preset_name=preset_name,
        )

    # Step 3: insert in-flight ToolRun row (sync path).
    tool_run = await mcp_repo.create_tool_run(
        db,
        engagement_id=engagement_id,
        server_name=server_name,
        tool_name=tool_name,
        args=args,
        preset_name=preset_name,
    )

    # Step 4: call the MCP subprocess.
    # McpServerDown propagates to the router; the row is left with exit_code NULL.
    raw = await subprocess_manager.send_tool_call(
        server_name=server_name,
        tool_name=tool_name,
        args=args,
        timeout_seconds=float(timeout_seconds),
    )

    # Step 5: update the row with the final result.
    finished_at = datetime.now(tz=UTC)
    updated = await mcp_repo.update_tool_run_result(
        db,
        tool_run.id,  # type: ignore[arg-type]
        exit_code=raw.exit_code,
        stdout=raw.stdout,
        stderr=raw.stderr,
        finished_at=finished_at,
    )

    # Step 6: build and return the result schema.
    # updated.status may be None if the server_default hasn't been flushed back to the
    # in-memory object; the sync path always ends in "completed", so fall back safely.
    raw_status: str | None = getattr(updated, "status", None)
    result_status = cast(ToolRunStatus, raw_status) if raw_status else "completed"
    return ToolRunResult(
        tool_run_id=cast(UUID, updated.id),
        engagement_id=cast(UUID, updated.engagement_id),
        server_name=updated.server_name,
        tool_name=updated.tool_name,
        exit_code=cast(int, updated.exit_code),
        stdout=updated.stdout,
        stderr=updated.stderr,
        started_at=updated.started_at,
        finished_at=cast(datetime, updated.finished_at),
        status=result_status,
        preset_name=getattr(updated, "preset_name", None),
    )


async def _stream_to_channel(
    *,
    tool_run_id: UUID,
    server_name: str,
    tool_name: str,
    args: dict[str, Any],
    timeout_seconds: float,
) -> None:
    """Background task: stream MCP output to the pub/sub channel and persist the result.

    Opens a FRESH database session (independent of the request session) so it can
    safely commit after the request has returned 202.

    On StreamDone → updates the DB row to completed/timed_out status, broadcasts
    a 'done' chunk, and returns.

    On any exception → updates the DB row to 'failed', broadcasts an 'error' chunk.

    In all cases the channel is discarded in the finally block so late WebSocket
    subscribers fall back to the persisted row.
    """
    async with get_sessionmaker()() as session:
        try:
            async for ev in subprocess_manager.stream_tool_call(
                server_name=server_name,
                tool_name=tool_name,
                args=args,
                timeout_seconds=timeout_seconds,
            ):
                if isinstance(ev, StreamChunk):
                    broadcast_tool_run_output(
                        tool_run_id,
                        WebSocketOutputChunk(type=ev.type, data=ev.data),
                    )
                elif isinstance(ev, StreamDone):
                    now = datetime.now(tz=UTC)
                    final_status: ToolRunStatus = (
                        "timed_out" if ev.exit_code == 124 else "completed"
                    )
                    await mcp_repo.update_tool_run_result(
                        session,
                        tool_run_id,
                        exit_code=ev.exit_code,
                        stdout=ev.stdout,
                        stderr=ev.stderr,
                        finished_at=now,
                        status=final_status,
                    )
                    await session.commit()
                    broadcast_tool_run_output(
                        tool_run_id,
                        WebSocketOutputChunk(
                            type="done",
                            exit_code=ev.exit_code,
                            finished_at=now,
                        ),
                    )
                    break

        except Exception as exc:  # noqa: BLE001 — catch all; detached task must not raise
            logger.exception(
                "Background stream task for tool_run_id=%s failed: %s", tool_run_id, exc
            )
            now = datetime.now(tz=UTC)
            try:
                await mcp_repo.update_tool_run_result(
                    session,
                    tool_run_id,
                    exit_code=1,
                    stdout="",
                    stderr=str(exc),
                    finished_at=now,
                    status="failed",
                )
                await session.commit()
            except Exception:  # noqa: BLE001 — DB may also be down; best-effort
                logger.exception(
                    "Failed to persist 'failed' status for tool_run_id=%s", tool_run_id
                )
            broadcast_tool_run_output(
                tool_run_id,
                WebSocketOutputChunk(type="error", message=str(exc)),
            )

        finally:
            _discard_channel(tool_run_id)


# ---------------------------------------------------------------------------
# list_tool_runs
# ---------------------------------------------------------------------------


async def list_tool_runs(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    user_id: UUID,
    limit: int,
    cursor: str | None,
) -> ToolRunPage:
    """Return a paginated page of ToolRunResult for an engagement.

    Flow:
      1. Fused existence + membership check — EngagementNotFound (404) if the
         engagement is missing OR the caller is not an explicit member (§4/§17.1).
      2. Decode the opaque cursor string if provided; a malformed non-empty cursor
         raises BadRequestError (400).
      3. Call the repo with keyset pagination parameters.
      4. Map each ToolRun row → ToolRunResult and encode next_cursor.

    Args:
        db:            Async database session.
        engagement_id: UUID of the engagement.
        user_id:       ID of the requesting user.
        limit:         Maximum number of rows per page (1–100).
        cursor:        Opaque keyset cursor from a previous response, or None for
                       the first page.

    Returns:
        ToolRunPage with items and optional next_cursor.

    Raises:
        EngagementNotFound: engagement_id does not exist OR user_id is not a member.
        BadRequestError:    cursor string is non-empty but malformed.
    """
    # Step 1: fused membership check (§17.1 isolation chokepoint).
    if await eng_repo.get_engagement_for_member(db, engagement_id, user_id) is None:
        raise EngagementNotFound(f"Engagement {engagement_id} not found")

    # Step 2: decode cursor if provided.
    decoded_cursor: tuple[datetime, UUID] | None = None
    if cursor:
        try:
            decoded_cursor = _decode_cursor(cursor)
        except ValueError as exc:
            raise BadRequestError(f"Invalid cursor: {exc}") from exc

    # Step 3: query the repo.
    rows, next_cursor_raw = await mcp_repo.list_tool_runs_for_engagement(
        db, engagement_id, limit=limit, cursor=decoded_cursor
    )

    # Step 4: map rows → ToolRunResult and encode next_cursor.
    items = [_row_to_result(row) for row in rows]
    next_cursor_str: str | None = None
    if next_cursor_raw is not None:
        nc_started, nc_id = next_cursor_raw
        next_cursor_str = _encode_cursor(nc_started, nc_id)

    return ToolRunPage(items=items, next_cursor=next_cursor_str)


# ---------------------------------------------------------------------------
# get_tool_run
# ---------------------------------------------------------------------------


async def get_tool_run(
    db: AsyncSession,
    *,
    tool_run_id: UUID,
    user_id: UUID,
) -> ToolRunResult:
    """Return a single ToolRunResult, enforcing engagement membership.

    Flow:
      1. Fetch the run by id; if missing → EngagementNotFound (404) — collapses
         missing-run and non-member into the same 404 to avoid existence disclosure.
      2. Membership check on run.engagement_id — same chokepoint as execute_tool_run.
      3. Map and return.

    Args:
        db:          Async database session.
        tool_run_id: UUID of the tool run.
        user_id:     ID of the requesting user.

    Returns:
        ToolRunResult with all fields populated.

    Raises:
        EngagementNotFound: tool_run_id does not exist OR user_id is not a member
                            of the run's engagement (404, no existence disclosure).
    """
    # Step 1: fetch the run.
    run = await mcp_repo.get_tool_run_by_id(db, tool_run_id)
    if run is None:
        raise EngagementNotFound("Tool run not found")

    # Step 2: membership check.
    if await eng_repo.get_engagement_for_member(db, cast(UUID, run.engagement_id), user_id) is None:
        raise EngagementNotFound("Tool run not found")

    # Step 3: map and return.
    return _row_to_result(run)


# ---------------------------------------------------------------------------
# authenticate_ws_tool_run
# ---------------------------------------------------------------------------


async def authenticate_ws_tool_run(
    db: AsyncSession,
    *,
    session_id: str | None,
    tool_run_id: UUID,
) -> ToolRun | None:
    """Authenticate + authorize a WebSocket subscription to a tool run.

    Mirrors ``auth.deps.get_current_session`` but WITHOUT sliding the session
    expiry or emitting a ``Set-Cookie`` header — both are inappropriate on a
    WebSocket upgrade. The cookie value is extracted by the router (a transport
    concern) and passed in as *session_id*.

    Returns the ``ToolRun`` row when the caller holds a valid, unexpired session
    AND is an explicit member of the engagement that owns the run. Returns
    ``None`` on ANY failure — missing/invalid/expired cookie, unknown user,
    missing run, or non-member — so the caller can collapse every failure into a
    single close code (no existence disclosure; §4 no-admin-bypass, §17.1).

    Args:
        db:          Async database session.
        session_id:  Opaque session id from the upgrade request cookie, if any.
        tool_run_id: UUID of the tool run being subscribed to.

    Returns:
        The owning ``ToolRun`` row on success, else ``None``.
    """
    if session_id is None:
        return None

    db_session = await auth_repo.get_session(db, session_id)
    if db_session is None:
        return None

    now = datetime.now(UTC)
    exp = db_session.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    if exp <= now:
        return None

    user = await auth_repo.get_user_by_id(db, cast(UUID, db_session.user_id))
    if user is None:
        return None

    run = await mcp_repo.get_tool_run_by_id(db, tool_run_id)
    if run is None:
        return None

    membership = await eng_repo.get_engagement_for_member(
        db, cast(UUID, run.engagement_id), cast(UUID, user.id)
    )
    if membership is None:
        return None

    return run


async def fetch_tool_run_row(db: AsyncSession, tool_run_id: UUID) -> ToolRun | None:
    """Re-read the current ToolRun row.

    No authorization is performed — the caller (the WS handler) must have already
    authorized via ``authenticate_ws_tool_run``. This exists so the WS
    completed-run path can serve the *final* persisted stdout/stderr for a run
    that finished during the auth→subscribe window, rather than the auth-time
    snapshot (which is still empty for a row that was ``running`` at auth time).
    """
    return await mcp_repo.get_tool_run_by_id(db, tool_run_id)


# ---------------------------------------------------------------------------
# Private helper
# ---------------------------------------------------------------------------


def _row_to_result(row: ToolRun) -> ToolRunResult:
    """Map a ToolRun ORM row to a ToolRunResult schema."""
    return ToolRunResult(
        tool_run_id=cast(UUID, row.id),
        engagement_id=cast(UUID, row.engagement_id),
        server_name=row.server_name,
        tool_name=row.tool_name,
        exit_code=row.exit_code,
        stdout=row.stdout or "",
        stderr=row.stderr or "",
        started_at=row.started_at,
        finished_at=row.finished_at,
        status=cast(ToolRunStatus, row.status),
        preset_name=row.preset_name,
    )
