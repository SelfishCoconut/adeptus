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
import time as _time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, cast
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.core.errors import AdeptusError, BadRequestError, ForbiddenError, NotFoundError
from app.features.auth import repository as auth_repo
from app.features.engagements import repository as eng_repo
from app.features.mcp import concurrency, subprocess_manager
from app.features.mcp import repository as mcp_repo
from app.features.mcp.concurrency import AdmissionHandle, EngagementPaused
from app.features.mcp.models import ToolRun
from app.features.mcp.registry import get_registry
from app.features.mcp.schemas import (
    McpServerInfo,
    McpToolDeclaration,
    QueueReason,
    ToolDescriptor,
    ToolPreset,
    ToolQueueSnapshot,
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


class TimeoutDecisionConflict(AdeptusError):
    """Raised by submit_timeout_decision when no run is awaiting a timeout decision.

    Covers: the run already resolved, was killed, or was resolved by another member's
    concurrent decision.  Translated to HTTP 409 inline in the router (same pattern
    as ToolQueueFullError → 429 and McpServerDown → 503 — no core error type for 409
    would be added without an ADR, so the translation lives in the router).
    """

    def __init__(self, message: str = "Run is not awaiting a timeout decision") -> None:
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
    - Otherwise extract the hostname from ``target`` using ``concurrency.parse_host``
      (the same URL-parsing logic used by the per-target lock so that the guard host
      and the lock host can never drift — Risk 5 deduplication).

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

    # Delegate host extraction to the canonical concurrency.parse_host so that
    # the guard host and the per-target lock host are always identical (Risk 5).
    host = concurrency.parse_host(target)

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
        EngagementPaused:    The engagement is currently paused (§6.3).  No DB row
                             is created and no task is spawned (→ HTTP 409 in router,
                             task 6).  Checked before the membership gate so the
                             response is fast and free of DB round-trips (Slice 06 Task 4).
        EngagementNotFound:  engagement_id does not exist OR user_id is not an
                             explicit member (even if admin — §4/§17.1).
        McpServerNotFound:   server_name not in the registry.
        McpServerDown:       Subprocess not running / timed out (sync path only;
                             async path surfaces failures via WS error chunk).
    """
    # Step 0 (§17.1 isolation chokepoint): fused existence + membership check.
    # This MUST run first — before the pause gate — so a non-member submitting to
    # a paused engagement gets 404, not 409.  Returning 409 to a non-member would
    # reveal that the engagement exists AND is paused (existence + state disclosure,
    # §17.1).  Membership is the outer gate; pause is the inner gate.
    # get_engagement_for_member returns None for a missing engagement OR a caller
    # with no explicit member row; both collapse to 404. Role is never consulted
    # (§4 no-bypass).
    member_pair = await eng_repo.get_engagement_for_member(db, engagement_id, user_id)
    if member_pair is None:
        raise EngagementNotFound(f"Engagement {engagement_id} not found")
    engagement_obj, _ = member_pair
    slot_limit: int = cast(int, engagement_obj.concurrency_slot_limit)

    # Step 1 (Slice 06, Task 4): engagement-wide pause gate.
    # Runs AFTER the membership gate (above) so non-members get 404, not 409 (§17.1).
    # Runs BEFORE any DB row creation / task spawn and BEFORE the heavy/light branch
    # so the pause blocks ALL new runs (light, heavy-sync, heavy-async) — Risk 5.
    # Mirrors the placement of check_queue_capacity: pure pre-flight, no side-effects.
    # EngagementPaused is translated to HTTP 409 in the router (task 6).
    if concurrency.is_paused(engagement_id):
        raise EngagementPaused(f"Engagement {engagement_id} is currently paused")

    # Step 2: server name validation.
    registry = get_registry()
    if server_name not in registry:
        raise McpServerNotFound(f"MCP server {server_name!r} is not in the registry")

    # Step 2b: sandbox guard — applies in dev/test for any tool whose args carry a
    # non-empty ``target`` key.  Must run BEFORE any DB row is created so a guarded
    # run never produces a tool_runs row.  Applies to both async and sync paths.
    # See ``_enforce_sandbox_guard`` docstring for the Risk-5 reconciliation note.
    _enforce_sandbox_guard(args)

    # Step 2c: resolve tool weight.  Admission control is weight-gated: light tools
    # bypass the concurrency pool entirely; heavy tools acquire a slot + host lock.
    # Branch on weight BEFORE any DB write so a light run is never charged a slot.
    server_config = registry[server_name]
    tool_config = next((t for t in server_config.tools if t.name == tool_name), None)
    weight: str = tool_config.weight if tool_config is not None else "light"
    is_heavy: bool = weight == "heavy"
    target_host: str | None = (
        concurrency.resolve_target_host(server_name, tool_name, args) if is_heavy else None
    )

    if async_mode:
        # Async path: insert row, commit, launch background task, return 202.
        # Heavy runs begin as 'queued'; the background task flips to 'running' on
        # admission (Decision 6: started_at = admission time, not insert time).
        # Light runs insert as 'running' — no admission step needed.

        # SECURITY: pre-flight capacity check for heavy runs.  Must run BEFORE
        # create_tool_run / db.commit() / asyncio.create_task() so that a full
        # queue surfaces as HTTP 429 with NO row created and NO task spawned.
        # The existing cap inside concurrency.acquire() is kept as defence-in-depth
        # for the residual TOCTOU window, but the gross amplification is eliminated
        # here.  Light runs are never queued so no check is needed for them.
        if is_heavy:
            concurrency.check_queue_capacity(engagement_id)

        initial_status: ToolRunStatus = "queued" if is_heavy else "running"
        tool_run = await mcp_repo.create_tool_run(
            db,
            engagement_id=engagement_id,
            server_name=server_name,
            tool_name=tool_name,
            args=args,
            status=initial_status,
            preset_name=preset_name,
        )
        # Commit so the row is durable before we return 202 and hand off to the task.
        # The background task opens its own session and must see the committed row.
        await db.commit()

        tool_run_id: UUID = cast(UUID, tool_run.id)
        task = asyncio.create_task(
            _stream_to_channel(
                tool_run_id=tool_run_id,
                engagement_id=engagement_id,
                server_name=server_name,
                tool_name=tool_name,
                args=args,
                timeout_seconds=float(timeout_seconds),
                is_heavy=is_heavy,
                slot_limit=slot_limit,
                target_host=target_host,
            )
        )
        # Slice 06 Task 5: the cancellation registry holds a strong reference to
        # the task (preventing GC from collecting it before it finishes), replacing
        # the former _background_tasks set.  The task is unregistered in the
        # _stream_to_channel finally block.
        concurrency.register_run(engagement_id, tool_run_id, task)

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
            status=initial_status,
            preset_name=preset_name,
        )

    # Step 3: insert in-flight ToolRun row (sync path).
    # Pre-flight capacity check for heavy sync runs (same defence as async path).
    if is_heavy:
        concurrency.check_queue_capacity(engagement_id)
    # Insert as 'running', not the default 'completed': a heavy sync run can
    # block in concurrency.acquire() for a long time before it executes, and a
    # crash/restart during that window must leave a phantom that startup
    # reconciliation can fail (it only targets 'queued'/'running').  The terminal
    # status is set unconditionally by update_tool_run_result below (Finding W3).
    tool_run = await mcp_repo.create_tool_run(
        db,
        engagement_id=engagement_id,
        server_name=server_name,
        tool_name=tool_name,
        args=args,
        status="running",
        preset_name=preset_name,
    )

    # Step 4: call the MCP subprocess.
    # For heavy sync runs: wrap in acquire/release so concurrent sync heavy runs
    # against the same engagement also serialize (Decision 4).  The HTTP request
    # simply blocks until admitted; no queue-position payload is returned (the
    # response only ever reflects the terminal state).
    # McpServerDown propagates to the router; the row is left with exit_code NULL.
    if is_heavy:
        handle: AdmissionHandle | None = None
        try:
            # Sync callbacks are no-ops: the HTTP response only shows the final state.
            def _sync_on_queued(position: int, reason: QueueReason) -> None:
                pass

            def _sync_on_started() -> None:
                pass

            handle = await concurrency.acquire(
                engagement_id=engagement_id,
                slot_limit=slot_limit,
                tool_run_id=cast(UUID, tool_run.id),
                target_host=target_host,
                server_name=server_name,
                tool_name=tool_name,
                on_queued=_sync_on_queued,
                on_started=_sync_on_started,
            )
            raw = await subprocess_manager.send_tool_call(
                server_name=server_name,
                tool_name=tool_name,
                args=args,
                timeout_seconds=float(timeout_seconds),
            )
        finally:
            if handle is not None:
                concurrency.release(handle)
    else:
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


async def _stream_to_channel(  # noqa: C901 — intentionally complex; split would obscure invariants
    *,
    tool_run_id: UUID,
    engagement_id: UUID,
    server_name: str,
    tool_name: str,
    args: dict[str, Any],
    timeout_seconds: float,
    is_heavy: bool = False,
    slot_limit: int = 3,
    target_host: str | None = None,
) -> None:
    """Background task: stream MCP output to the pub/sub channel and persist the result.

    Opens a FRESH database session (independent of the request session) so it can
    safely commit after the request has returned 202.

    DEADLINE MECHANISM (Decision 1 / Risk 3)
    -----------------------------------------
    The user-facing deadline is enforced at this layer, NOT in subprocess_manager.
    Each event is fetched by iterating the generator manually and wrapping the
    ``__anext__`` call in ``asyncio.wait_for(gen.__anext__(), timeout=remaining_seconds)``.
    The remaining budget is tracked as a monotonic wall-clock deadline so the budget
    survives across awaits (e.g. DB commits).

    To prevent subprocess_manager's own ``outer_timeout`` from pre-empting this layer's
    prompt, ``stream_tool_call`` is called with a transport timeout of
    ``_LARGE_TRANSPORT_TIMEOUT`` (1 hour) — effectively unbounded for any real workload.
    The actual wall-clock limit comes from the service-layer wait_for wrapper.

    KILL WHILE RUNNING (Risk 1 / Risk 2)
    --------------------------------------
    ``task.cancel()`` raises ``asyncio.CancelledError`` inside the
    ``asyncio.wait_for(gen.__anext__(), ...)`` call.  Closing the async generator
    (which happens on CancelledError propagation through the ``async for`` /
    manual iteration) triggers the generator's ``finally`` block and exits
    ``async with handle.lock`` — the per-server subprocess lock is released.
    The underlying MCP subprocess itself is NOT killed (Decision 2 / Risk 2):
    we stop reading its output and release our locks, but the scan process may
    continue running inside the MCP server.  This is documented and accepted.

    A ``CancelledError`` is caught, the row is persisted as ``killed``, a ``killed``
    WS chunk is broadcast, and the exception is SWALLOWED (not re-raised).
    Rationale: ``_stream_to_channel`` is a detached background task.  Re-raising
    would propagate the CancelledError to asyncio's task machinery, which logs an
    unhandled exception and discards it anyway.  Swallowing after persisting is
    cleaner, avoids spurious log noise, and matches the ``except Exception`` pattern
    already used for all other failures in this function.  This is safe because the
    task is already being abandoned by its canceller — there is no caller awaiting
    the return value.

    KILL WHILE QUEUED
    ------------------
    ``concurrency.acquire`` raises ``RunKilled`` when the run's FIFO ticket is
    cancelled before admission.  This is caught before streaming starts; the row
    is persisted as ``killed`` and no subprocess is ever touched.

    TIMEOUT-CONFIRM WITH SLOT RELEASE (Decision 1 / Decision 6 / Risk 3 / Risk 7)
    -------------------------------------------------------------------------------
    When the service-layer deadline fires (``asyncio.TimeoutError`` from ``wait_for``):
    1. The generator is closed (abandons the current subprocess call, releases
       the per-server lock — same mechanism as kill).
    2. ``concurrency.release_for_decision`` releases the admission slot + host lock
       so waiting runs can be admitted immediately.  The original handle is marked
       ``released=True`` by the underlying ``concurrency.release`` call.
    3. The row is persisted as ``awaiting_decision``; a ``timeout`` WS chunk is
       broadcast.  ``concurrency.await_timeout_decision`` is called with NO timeout —
       the prompt stays open indefinitely (Decision 6 / Risk 8).
    4. On the decision:
       - ``kill`` → persist ``killed``, broadcast ``killed``, return (no re-acquire).
       - ``extend`` / ``wait`` → re-``acquire(...)`` through the normal FIFO admission
         path (respects the queue, host lock, and pause flag); emit a fresh ``started``
         chunk once admitted; reset the deadline (extend) or disable it (wait);
         open a fresh ``stream_tool_call`` and resume reading.
    5. A ``kill``/pause arriving while parked resolves the rendezvous as ``kill``
       (handled by the concurrency module — task 3).

    SLOT ACCOUNTING INVARIANT (Risk 7)
    -----------------------------------
    ``_current_handle`` is a mutable list cell so the ``finally`` block always
    releases the CURRENTLY HELD handle, not the one already released by
    ``release_for_decision``.  At any instant there is exactly one outstanding handle
    (or None).  The flow is:
    - After ``acquire``: ``_current_handle[0]`` = new handle (``released=False``).
    - After ``release_for_decision``: original handle is marked ``released=True``
      (idempotent guard), ``_current_handle[0]`` = ``None``.
    - After re-``acquire`` on extend/wait: ``_current_handle[0]`` = new handle.
    - ``finally`` calls ``concurrency.release(_current_handle[0])`` only when it is
      not None — the ``released`` guard makes it a no-op if already released.

    RE-OPEN SEMANTICS ON EXTEND/WAIT
    ----------------------------------
    Because the original generator was closed, resuming means starting a fresh
    ``stream_tool_call`` for the same tool args.  For the demo ``sleep_probe`` /
    ``run_httpx_heavy`` tool this restarts the call (acceptable — Decision 1 /
    Risk 3).  True checkpoint/resume is out of scope.

    CANCELLATION SAFETY ON RE-ACQUIRE
    -----------------------------------
    A ``kill``/pause arriving during the re-``acquire`` (which may block on the host
    lock / FIFO queue) raises either ``CancelledError`` or ``RunKilled`` /
    ``EngagementPaused``.  All of these are caught and the slot is NOT acquired —
    no dangling slot is left behind (Risk 7 hazard b).
    """
    # Very large transport timeout so subprocess_manager's outer_timeout never
    # pre-empts the service-layer deadline prompt (Decision 1 / Risk 3).
    _LARGE_TRANSPORT_TIMEOUT: float = 3600.0

    async with get_sessionmaker()() as session:
        # Mutable cell for the currently-held admission handle.  One entry only.
        # ``None`` when the tool is light (no slot held), or after release_for_decision
        # has been called (the original handle is already released and a new one has
        # not yet been acquired).  Updated on every acquire / release_for_decision.
        _current_handle: list[AdmissionHandle | None] = [None]

        # Flag: True when the run has entered awaiting_decision and concurrency
        # cleanup_decision must be called in the finally block.
        _has_decision_rendezvous: bool = False

        try:
            # ---------------------------------------------------------------
            # KILL WHILE QUEUED path: RunKilled from acquire (step 1 of heavy path)
            # ---------------------------------------------------------------
            if is_heavy:
                # --- Async-path admission callbacks ---

                async def _on_queued(position: int, reason: QueueReason) -> None:
                    """Called when the run is enqueued and cannot be admitted yet."""
                    broadcast_tool_run_output(
                        tool_run_id,
                        WebSocketOutputChunk(
                            type="queued",
                            queue_position=position,
                            reason=reason,
                        ),
                    )
                    try:
                        await mcp_repo.update_tool_run_status(
                            session,
                            tool_run_id,
                            status="queued",
                        )
                        await session.commit()
                    except Exception:  # noqa: BLE001 — best-effort; don't break the waiter
                        logger.exception(
                            "Failed to persist 'queued' status for tool_run_id=%s",
                            tool_run_id,
                        )

                async def _on_started() -> None:
                    """Called when the run is admitted (slot + host lock acquired)."""
                    now = datetime.now(tz=UTC)
                    broadcast_tool_run_output(
                        tool_run_id,
                        WebSocketOutputChunk(type="started"),
                    )
                    try:
                        await mcp_repo.update_tool_run_status(
                            session,
                            tool_run_id,
                            status="running",
                            started_at=now,
                        )
                        await session.commit()
                    except Exception:  # noqa: BLE001 — best-effort; streaming still proceeds
                        logger.exception(
                            "Failed to persist 'running' status for tool_run_id=%s",
                            tool_run_id,
                        )

                try:
                    _current_handle[0] = await concurrency.acquire(
                        engagement_id=engagement_id,
                        slot_limit=slot_limit,
                        tool_run_id=tool_run_id,
                        target_host=target_host,
                        server_name=server_name,
                        tool_name=tool_name,
                        on_queued=_on_queued,
                        on_started=_on_started,
                    )
                except concurrency.RunKilled:
                    # The run's FIFO ticket was killed before it was ever admitted.
                    # No subprocess was touched.  Persist killed status and return.
                    logger.info(
                        "tool_run_id=%s killed while queued (before admission)", tool_run_id
                    )
                    now = datetime.now(tz=UTC)
                    try:
                        await mcp_repo.update_tool_run_result(
                            session,
                            tool_run_id,
                            exit_code=1,
                            stdout="",
                            stderr="killed before start",
                            finished_at=now,
                            status="killed",
                        )
                        await session.commit()
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "Failed to persist 'killed' (queued) for tool_run_id=%s",
                            tool_run_id,
                        )
                    broadcast_tool_run_output(
                        tool_run_id,
                        WebSocketOutputChunk(type="killed", message="killed by user"),
                    )
                    return  # No subprocess was called; finally still runs for cleanup.
                except concurrency.EngagementPaused:
                    # Engagement was paused before the run could be admitted.
                    logger.info(
                        "tool_run_id=%s rejected: engagement %s paused", tool_run_id, engagement_id
                    )
                    now = datetime.now(tz=UTC)
                    try:
                        await mcp_repo.update_tool_run_result(
                            session,
                            tool_run_id,
                            exit_code=1,
                            stdout="",
                            stderr="engagement paused",
                            finished_at=now,
                            status="killed",
                        )
                        await session.commit()
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "Failed to persist 'killed' (paused) for tool_run_id=%s",
                            tool_run_id,
                        )
                    broadcast_tool_run_output(
                        tool_run_id,
                        WebSocketOutputChunk(type="killed", message="engagement paused"),
                    )
                    return

            # ---------------------------------------------------------------
            # Compute the service-layer deadline (mutable wall-clock deadline).
            # ``None`` means "no deadline" (used after a 'wait' decision).
            # ---------------------------------------------------------------
            deadline: float | None = (
                _time.monotonic() + timeout_seconds if timeout_seconds > 0 else None
            )

            # ---------------------------------------------------------------
            # Streaming loop — may loop multiple times on extend/wait.
            # ---------------------------------------------------------------
            while True:
                # Open a fresh generator.  Transport timeout is effectively unbounded
                # so the subprocess_manager's outer_timeout never fires before the
                # service-layer deadline (Decision 1 / Risk 3).
                # Cast to AsyncGenerator so mypy knows aclose() is available
                # (stream_tool_call is annotated AsyncIterator but is really an
                # async generator; AsyncGenerator is a subtype of AsyncIterator).
                gen = cast(
                    AsyncGenerator[StreamChunk | StreamDone, None],
                    subprocess_manager.stream_tool_call(
                        server_name=server_name,
                        tool_name=tool_name,
                        args=args,
                        timeout_seconds=_LARGE_TRANSPORT_TIMEOUT,
                    ),
                )

                timed_out = False
                try:
                    while True:
                        # Compute remaining budget.
                        if deadline is not None:
                            remaining = deadline - _time.monotonic()
                            if remaining <= 0:
                                timed_out = True
                                break
                        else:
                            remaining = None  # no deadline

                        # Fetch next event with service-layer deadline.
                        try:
                            if remaining is not None:
                                ev = await asyncio.wait_for(gen.__anext__(), timeout=remaining)
                            else:
                                ev = await gen.__anext__()
                        except TimeoutError:
                            # Service-layer deadline fired.
                            timed_out = True
                            break
                        except StopAsyncIteration:
                            # Generator exhausted normally.
                            break

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
                            return  # Completed normally; finally block does cleanup.
                finally:
                    # Always close the generator so the per-server subprocess lock
                    # is released (Risk 1 / Risk 2).  aclose() is idempotent.
                    await gen.aclose()

                # ---------------------------------------------------------------
                # TIMEOUT path — service-layer deadline fired.
                # ---------------------------------------------------------------
                if timed_out:
                    # Step 1: Generator is already closed (aclose() called above).
                    #         Per-server lock is released.

                    # Step 2: Release the admission slot + host lock back to the
                    #         queue so the FIFO can advance (Decision 6).
                    # For light tools (_current_handle[0] is None), release_for_decision
                    # still creates the rendezvous and marks the registry entry as
                    # slotless — just without releasing a (non-existent) slot.
                    concurrency.release_for_decision(engagement_id, tool_run_id, _current_handle[0])
                    _current_handle[0] = None  # slot is now released; guard against double-release

                    _has_decision_rendezvous = True

                    # Step 3: Persist awaiting_decision and broadcast the timeout prompt.
                    try:
                        await mcp_repo.update_tool_run_status(
                            session,
                            tool_run_id,
                            status="awaiting_decision",
                        )
                        await session.commit()
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "Failed to persist 'awaiting_decision' for tool_run_id=%s",
                            tool_run_id,
                        )
                    broadcast_tool_run_output(
                        tool_run_id,
                        WebSocketOutputChunk(
                            type="timeout",
                            message=(
                                "Timeout reached. Concurrency slot released — queued runs "
                                "can now advance. Waiting for your decision: kill / extend / wait."
                            ),
                        ),
                    )

                    # Step 4: Wait indefinitely for the human's decision (Risk 8).
                    # await_timeout_decision returns (decision, extend_seconds) so the
                    # caller-supplied extend window reaches this task directly — the
                    # rendezvous carries the value set by submit_timeout_decision (REST
                    # caller passes TimeoutDecision.extend_seconds through the service).
                    decision, _extend_secs = await concurrency.await_timeout_decision(tool_run_id)
                    concurrency.cleanup_decision(tool_run_id)
                    _has_decision_rendezvous = False

                    if decision == "kill":
                        # Human chose kill — persist and broadcast, then return.
                        logger.info("tool_run_id=%s timeout decision: kill", tool_run_id)
                        now = datetime.now(tz=UTC)
                        try:
                            await mcp_repo.update_tool_run_result(
                                session,
                                tool_run_id,
                                exit_code=1,
                                stdout="",
                                stderr="killed after timeout",
                                finished_at=now,
                                status="killed",
                            )
                            await session.commit()
                        except Exception:  # noqa: BLE001
                            logger.exception(
                                "Failed to persist 'killed' (timeout-kill) for tool_run_id=%s",
                                tool_run_id,
                            )
                        broadcast_tool_run_output(
                            tool_run_id,
                            WebSocketOutputChunk(
                                type="killed",
                                message="killed by user",
                            ),
                        )
                        return  # finally does cleanup.

                    # extend or wait — re-acquire a slot through the normal path.
                    # (This re-acquire respects FIFO, host lock, and pause flag.)
                    if decision == "extend":
                        # Use the caller-supplied extend_seconds from the rendezvous
                        # (set by the REST handler from TimeoutDecision.extend_seconds).
                        extend_seconds: float = float(_extend_secs)
                        new_deadline: float | None = _time.monotonic() + extend_seconds
                    else:
                        # decision == "wait" — disable the deadline entirely.
                        new_deadline = None

                    logger.info(
                        "tool_run_id=%s timeout decision: %s, re-acquiring slot",
                        tool_run_id,
                        decision,
                    )

                    # Callbacks for re-acquire.  on_queued re-uses the heavy callback
                    # (if defined) so queue-position updates are still broadcast.
                    # on_started is a no-op here; we broadcast the 'started' chunk
                    # inline immediately after acquire returns (so we can close over
                    # session and tool_run_id correctly).
                    # cast: pre-commit mypy (no backend config) needs explicit typing.
                    from app.features.mcp.concurrency import OnQueuedCallback

                    _reacquire_on_queued: OnQueuedCallback = (
                        cast(OnQueuedCallback, _on_queued)
                        if is_heavy
                        else cast(OnQueuedCallback, lambda p, r: None)
                    )

                    # Re-acquire — cancellation-aware (Risk 7 hazard b).
                    _reacquire_killed_msg: str = "killed by user"
                    try:
                        _current_handle[0] = await concurrency.acquire(
                            engagement_id=engagement_id,
                            slot_limit=slot_limit,
                            tool_run_id=tool_run_id,
                            target_host=target_host,
                            server_name=server_name,
                            tool_name=tool_name,
                            on_queued=_reacquire_on_queued,
                            on_started=lambda: None,
                        )
                    except concurrency.EngagementPaused:
                        _reacquire_killed_msg = "engagement paused"
                        _current_handle[0] = None
                        logger.info(
                            "tool_run_id=%s killed during re-acquire (engagement paused)",
                            tool_run_id,
                        )
                        now = datetime.now(tz=UTC)
                        try:
                            await mcp_repo.update_tool_run_result(
                                session,
                                tool_run_id,
                                exit_code=1,
                                stdout="",
                                stderr="killed during re-acquire",
                                finished_at=now,
                                status="killed",
                            )
                            await session.commit()
                        except Exception:  # noqa: BLE001
                            logger.exception(
                                "Failed to persist 'killed' (re-acquire paused) for tool_run_id=%s",
                                tool_run_id,
                            )
                        broadcast_tool_run_output(
                            tool_run_id,
                            WebSocketOutputChunk(type="killed", message=_reacquire_killed_msg),
                        )
                        return  # finally does cleanup.
                    except concurrency.RunKilled:
                        _current_handle[0] = None
                        logger.info(
                            "tool_run_id=%s killed during re-acquire (run killed)", tool_run_id
                        )
                        now = datetime.now(tz=UTC)
                        try:
                            await mcp_repo.update_tool_run_result(
                                session,
                                tool_run_id,
                                exit_code=1,
                                stdout="",
                                stderr="killed during re-acquire",
                                finished_at=now,
                                status="killed",
                            )
                            await session.commit()
                        except Exception:  # noqa: BLE001
                            logger.exception(
                                "Failed to persist 'killed' (re-acquire killed) for tool_run_id=%s",
                                tool_run_id,
                            )
                        broadcast_tool_run_output(
                            tool_run_id,
                            WebSocketOutputChunk(type="killed", message="killed by user"),
                        )
                        return  # finally does cleanup.

                    # Re-acquire succeeded.
                    # Risk 7: restore holds_slot=True so that a subsequent kill_run
                    # correctly sees this run as slot-holding and cancels the task,
                    # rather than falling through to the awaiting-decision branch and
                    # calling _submit_decision_internal against a cleaned-up rendezvous
                    # (which returns "awaiting" silently — a no-op kill).
                    concurrency.mark_slot_reacquired(tool_run_id)

                    # Broadcast a fresh 'started' chunk and update the DB row to
                    # 'running' with a fresh started_at so the UI shows the run resuming
                    # (Decision 6).
                    reacquire_now = datetime.now(tz=UTC)
                    broadcast_tool_run_output(
                        tool_run_id,
                        WebSocketOutputChunk(type="started"),
                    )
                    try:
                        await mcp_repo.update_tool_run_status(
                            session,
                            tool_run_id,
                            status="running",
                            started_at=reacquire_now,
                        )
                        await session.commit()
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "Failed to persist 'running' (re-acquire) for tool_run_id=%s",
                            tool_run_id,
                        )

                    # Update deadline and loop back to start a fresh stream.
                    deadline = new_deadline
                    continue  # Back to top of while True — opens a fresh generator.

                # Should not be reached (timed_out=False and no return above means
                # the generator exhausted without a StreamDone — treat as error).
                break  # Falls through to the error path below.

        except asyncio.CancelledError as _ce:
            # KILL WHILE RUNNING (or during awaiting-decision's await_timeout_decision).
            # The task was cancelled by kill_run (per-tool) or set_paused (engagement pause).
            # Per-server lock is released by the generator's aclose() in the inner
            # finally block above (which ran before the CancelledError propagated here).
            # Persist killed status and broadcast; SWALLOW the CancelledError.
            # (See docstring for the rationale for swallowing vs re-raising.)
            #
            # Distinguish the two cancel causes (S-3):
            #   - set_paused calls task.cancel(msg="engagement paused") so the
            #     CancelledError.args[0] == "engagement paused".
            #   - kill_run / other callers use task.cancel() (default: no msg),
            #     so args is empty → fall back to "killed by user".
            _cancel_msg: str = (
                _ce.args[0] if _ce.args and isinstance(_ce.args[0], str) else "killed by user"
            )
            logger.info("tool_run_id=%s cancelled: %s", tool_run_id, _cancel_msg)
            now = datetime.now(tz=UTC)
            try:
                await mcp_repo.update_tool_run_result(
                    session,
                    tool_run_id,
                    exit_code=1,
                    stdout="",
                    stderr=_cancel_msg,
                    finished_at=now,
                    status="killed",
                )
                await session.commit()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Failed to persist 'killed' (cancelled) for tool_run_id=%s", tool_run_id
                )
            broadcast_tool_run_output(
                tool_run_id,
                WebSocketOutputChunk(type="killed", message=_cancel_msg),
            )
            # CancelledError is swallowed — detached task must not crash the loop.

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
            # Release the CURRENTLY HELD admission handle (Risk 3 / Risk 7).
            # The ``released`` idempotency guard on AdmissionHandle ensures that a
            # handle already released by ``release_for_decision`` is a no-op here.
            # ``_current_handle[0]`` is None when:
            #   (a) the tool is light (no slot was ever acquired), or
            #   (b) the slot was already released by ``release_for_decision`` and
            #       no re-acquire followed (e.g. decision=kill, or we are in the
            #       awaiting-decision state when the task is cancelled).
            h = _current_handle[0]
            if h is not None:
                concurrency.release(h)

            # Clean up the decision rendezvous if we never finished resolving it
            # (e.g. CancelledError arrived while awaiting the decision).
            if _has_decision_rendezvous:
                concurrency.cleanup_decision(tool_run_id)

            # Unregister from the cancellation registry so the map does not grow.
            concurrency.unregister_run(tool_run_id)
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


async def get_tool_queue_snapshot(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    user_id: UUID,
) -> ToolQueueSnapshot:
    """Return the in-process concurrency snapshot for an engagement.

    Membership check (§4/§17.1):
        Calls the same ``get_engagement_for_member`` chokepoint used by
        ``execute_tool_run``.  Both "engagement does not exist" and "caller is
        not a member" raise ``EngagementNotFound`` (→ 404), so a non-member
        cannot infer that the engagement exists (no existence disclosure).

    ``slot_limit`` is read from the engagement row fetched during the membership
    check — no second DB round-trip.  If the in-process state has diverged
    (e.g. the limit was just patched), the DB value is authoritative for this
    read (the concurrency module is updated lazily on the next ``acquire`` call).

    Returns:
        ``ToolQueueSnapshot`` — all fields populated from the in-process state,
        with ``slot_limit`` overridden by the persisted engagement setting.

    Raises:
        EngagementNotFound: engagement_id does not exist OR user_id is not a
                            member (404, no existence disclosure).
    """
    member_pair = await eng_repo.get_engagement_for_member(db, engagement_id, user_id)
    if member_pair is None:
        raise EngagementNotFound(f"Engagement {engagement_id} not found")
    engagement_obj, _ = member_pair

    snap = concurrency.snapshot(engagement_id)
    # Override slot_limit with the persisted value so the response always
    # reflects the canonical DB configuration, not the stale in-process default.
    snap = snap.model_copy(update={"slot_limit": int(engagement_obj.concurrency_slot_limit)})
    return snap


async def kill_tool_run(
    db: AsyncSession,
    *,
    tool_run_id: UUID,
    user_id: UUID,
) -> ToolRunResult:
    """Stop a single tool run, enforcing engagement membership.

    Flow:
      1. Fetch the run by id; if missing → EngagementNotFound (404).
      2. Membership check — same chokepoint as get_tool_run (§17.1/§4).
      3. Call concurrency.kill_run(tool_run_id) to determine the run's current state.
         - "cancelled": the running task's finally will persist 'killed'.  Return the
           current row immediately (the task may still be 'running' briefly; spec says
           return current state — do NOT block waiting for the task).
         - "dequeued": the queued run's acquire raises RunKilled and its task persists
           'killed' IF the task is still alive.  The service also writes 'killed' here
           to guarantee convergence (the task may have already finished).  Writes are
           safe because update_tool_run_result is idempotent on the same terminal value.
         - "awaiting": the parked task resolves itself killed via the rendezvous.
           Return the current row.
         - "absent": already terminal (or never existed beyond membership gate) —
           idempotent success; return the current row.
      4. Re-read and return the current row as ToolRunResult.

    Returns:
        ToolRunResult — current state of the run.

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

    # Step 3: attempt to kill via the concurrency registry.
    outcome = concurrency.kill_run(tool_run_id)
    logger.info("kill_tool_run tool_run_id=%s outcome=%s", tool_run_id, outcome)

    if outcome == "dequeued":
        # The run's ticket was removed from the FIFO queue; its task (if alive) will
        # also persist 'killed' via RunKilled handling.  Write here too to guarantee
        # the row converges to 'killed' (spec: "service must converge to a killed row").
        now = datetime.now(tz=UTC)
        current_status = getattr(run, "status", None)
        if current_status not in ("killed", "completed", "failed", "timed_out"):
            try:
                await mcp_repo.update_tool_run_result(
                    db,
                    tool_run_id,
                    exit_code=1,
                    stdout="",
                    stderr="killed by user",
                    finished_at=now,
                    status="killed",
                )
                await db.commit()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "kill_tool_run: failed to persist 'killed' (dequeued) for tool_run_id=%s",
                    tool_run_id,
                )

    # Step 4: re-read the current row and return it.
    latest = await mcp_repo.get_tool_run_by_id(db, tool_run_id)
    if latest is not None:
        run = latest
    return _row_to_result(run)


async def submit_timeout_decision(
    db: AsyncSession,
    *,
    tool_run_id: UUID,
    user_id: UUID,
    decision: Literal["kill", "extend", "wait"],
    extend_seconds: int = 30,
) -> ToolRunResult:
    """Submit a timeout decision for a run in awaiting_decision state.

    Flow:
      1. Fetch the run by id; if missing → EngagementNotFound (404).
      2. Membership check — same chokepoint as get_tool_run (§17.1/§4).
      3. Forward to concurrency.submit_timeout_decision.
         Returns False when no run is awaiting a decision (already resolved,
         wrong state, or unknown); the router translates this to 409.
      4. Re-read and return the current row.

    Returns:
        ToolRunResult — current state; the parked task may still be 'awaiting_decision'
        for a brief window while it acts on the decision.

    Raises:
        EngagementNotFound: tool_run_id does not exist OR user_id is not a member.
        TimeoutDecisionConflict: the run is not currently awaiting a decision
                                 (→ HTTP 409 in the router).
    """
    # Step 1: fetch the run.
    run = await mcp_repo.get_tool_run_by_id(db, tool_run_id)
    if run is None:
        raise EngagementNotFound("Tool run not found")

    # Step 2: membership check.
    if await eng_repo.get_engagement_for_member(db, cast(UUID, run.engagement_id), user_id) is None:
        raise EngagementNotFound("Tool run not found")

    # Step 3: forward to the concurrency rendezvous.
    accepted = concurrency.submit_timeout_decision(
        tool_run_id, decision, extend_seconds=extend_seconds
    )
    if not accepted:
        raise TimeoutDecisionConflict(
            f"Run {tool_run_id} is not currently awaiting a timeout decision"
        )

    # Step 4: re-read the current row and return it.
    latest = await mcp_repo.get_tool_run_by_id(db, tool_run_id)
    if latest is not None:
        run = latest
    return _row_to_result(run)


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
    """Map a ToolRun ORM row to a ToolRunResult schema.

    ``queue_position`` is derived from the in-process concurrency state: it is
    the 1-based FIFO position returned by ``concurrency.position_of`` when the
    row's status is ``'queued'``, and ``None`` for all other statuses (running,
    completed, failed, timed_out) and for light runs (which are never enqueued).

    The in-process queue is the authoritative source for position — there is no
    persistent ``queue_position`` column.  If the run has already been admitted
    (status flipped to ``'running'``) but ``position_of`` still returns a value
    (a transient race), we return ``None`` per the contract.
    """
    raw_status: str | None = getattr(row, "status", None)
    run_id = cast(UUID, row.id)
    queue_position: int | None = None
    if raw_status == "queued":
        queue_position = concurrency.position_of(run_id)
    # Populate awaiting_since from the in-process registry when the row is
    # awaiting a timeout decision.  The value is NOT a DB column (per the slice
    # Data-model section); it is tracked by release_for_decision and surfaced here
    # so the REST response satisfies the OpenAPI contract (non-null while awaiting).
    awaiting_since = (
        concurrency.get_awaiting_since(run_id) if raw_status == "awaiting_decision" else None
    )
    return ToolRunResult(
        tool_run_id=run_id,
        engagement_id=cast(UUID, row.engagement_id),
        server_name=row.server_name,
        tool_name=row.tool_name,
        exit_code=row.exit_code,
        stdout=row.stdout or "",
        stderr=row.stderr or "",
        started_at=row.started_at,
        finished_at=row.finished_at,
        status=cast(ToolRunStatus, raw_status) if raw_status else "completed",
        preset_name=row.preset_name,
        queue_position=queue_position,
        awaiting_since=awaiting_since,
    )
