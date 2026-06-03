"""In-process admission manager for the heavy-tool concurrency pool (Slice 05).

PURPOSE
-------
Serialize heavy tool runs through a bounded per-engagement slot pool plus a
per-(engagement, target-host) exclusive lock so two heavy tools against the same
host never overlap, while extra runs queue FIFO and surface their queue position.

LIGHT-RUN CONTRACT
------------------
Light tool runs do NOT call ``acquire`` at all.  The caller (``service.py``)
branches on ``weight == "heavy"`` before reaching this module.  Light runs bypass
the pool, the host lock, and the FIFO queue entirely — this is intentional and is
the central user-visible promise of this slice.

FIFO / ELIGIBILITY POLICY (Decision 5 — Risk 1)
------------------------------------------------
The FIFO queue stores admission tickets in *arrival order*.  When ``release`` is
called, it runs an eligibility scan that walks the queue front-to-back and admits
the **first ticket that is currently eligible**:

    eligible = (a free slot is available) AND (target_host is None OR host is unlocked)

Two cases to get exactly right:

1. **Lock-blocked front, free slot, eligible later waiter** — the front waiter's
   host is currently held by another run, but a slot is free and a later waiter's
   host is free.  Policy: admit the later eligible waiter.  The front waiter is
   *not* starved — it will be admitted on the next release that frees its host.
   The scan continues past the front waiter until it finds the first eligible one.

2. **Slot-blocked front, free host** — no slots are free, so even if a later
   waiter's host is also free it cannot be admitted.  Policy: do NOT skip the
   front waiter.  The slot-blocked front waiter is NOT overtaken by any later run
   just because the later run's host happens to be unlocked; it must wait until a
   slot frees, then it gets the slot first (assuming its host is also free then).

The critical difference: a lock-blocked run may be skipped (host lock is the
obstacle); a slot-blocked run may never be skipped (the pool is empty regardless
of host).  This policy is pinned by ``test_fifo_order_preserved`` and
``test_target_lock_queues_with_free_slot``.

NO-PREEMPTION ON SHRINK (Risk 6)
---------------------------------
The gate tracks available permits as ``limit - in_use``.  Shrinking below the
current ``in_use`` count makes ``available`` go negative, which simply prevents
new admissions until releases catch up.  Running slots are never killed.

MULTI-WORKER SAFETY (Risk 2)
-----------------------------
This module uses module-level asyncio structures — the same posture as the Slice 04
pub/sub in ``service.py``.  It is NOT multi-worker safe: across multiple uvicorn
workers the same engagement can over-admit.  This is acceptable for the single-
process Compose deployment.  A multi-worker deployment would need a Postgres
advisory-lock or Redis-based gate — do NOT add either now.

LOCK KEYSPACE ISOLATION
-----------------------
Every per-engagement record is keyed by ``engagement_id`` (a UUID), and the host
lock set is nested inside that per-engagement record.  The per-target lock cannot
couple two different engagements — ``("eng-A", "localhost")`` and
``("eng-B", "localhost")`` are independent.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from app.core.errors import AdeptusError
from app.features.mcp.schemas import QueuedRun, QueueReason, ToolQueueSnapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Queue depth cap (Security Medium-1)
# ---------------------------------------------------------------------------

# Maximum number of tickets that may be waiting in a single engagement's FIFO
# queue at any time.  A generous but finite ceiling that prevents a single
# engagement member from growing in-process memory without bound by submitting
# thousands of queued heavy runs at a locked host.
#
# §6.2 states that the concurrency model bounds parallelism; this constant
# extends that bound to the admission queue.  256 waiters × (slot_limit=16
# max concurrent) = up to 272 heavy runs in flight or queued per engagement —
# more than any realistic workload.  Chosen to be large enough that legitimate
# use never hits it while being small enough to bound runaway memory growth.
MAX_QUEUE_DEPTH: int = 256

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

# Callback types for acquire() and for per-ticket re-broadcast (Task 4).
OnQueuedCallback = Callable[[int, QueueReason], Awaitable[None] | None]
OnStartedCallback = Callable[[], Awaitable[None] | None]


class ToolQueueFullError(AdeptusError):
    """Raised when an engagement's admission queue is at MAX_QUEUE_DEPTH.

    A single engagement member cannot grow the in-process queue without bound
    by submitting thousands of heavy runs at a locked host.  When the per-
    engagement queue depth reaches MAX_QUEUE_DEPTH, ``acquire`` raises this
    exception synchronously so the caller can surface it as HTTP 429 (Too Many
    Requests) before the run is persisted.

    This is a domain exception subclassing ``AdeptusError`` so it participates
    in the core domain-exception → HTTP handler pattern.  Mapped to HTTP 429
    in ``router.py``.
    """

    def __init__(self, message: str = "Tool queue is full for this engagement") -> None:
        super().__init__(message)


@dataclass
class AdmissionHandle:
    """Opaque handle returned by ``acquire``; pass to ``release`` when done.

    ``released`` is set to ``True`` by ``release`` so that double-release is a
    safe no-op (idempotency required by Risk 3 — the caller puts release in a
    ``finally`` block).
    """

    engagement_id: UUID
    tool_run_id: UUID
    target_host: str | None
    released: bool = field(default=False, init=False)


# ---------------------------------------------------------------------------
# Internal structures
# ---------------------------------------------------------------------------


@dataclass
class _Ticket:
    """A single waiter in the FIFO queue."""

    tool_run_id: UUID
    server_name: str
    tool_name: str
    target_host: str | None
    enqueued_at: datetime
    # Callback stored so the admission scan can re-broadcast updated queue positions
    # to still-waiting tickets after a release-driven admission (Task 4).
    on_queued: OnQueuedCallback
    # asyncio.Event set by the admission scan to wake this waiter.
    admitted: asyncio.Event = field(default_factory=asyncio.Event)
    # Reason why the run is waiting (updated when enqueued; may change but we
    # record the first reason and keep it until admitted).
    reason: QueueReason = "slot_full"


@dataclass
class _EngagementState:
    """All concurrency state for a single engagement."""

    slot_limit: int
    in_use: int = 0
    locked_hosts: set[str] = field(default_factory=set)
    # Ordered dict preserves insertion order (arrival order = FIFO).
    # Key: tool_run_id (UUID), Value: _Ticket
    queue: OrderedDict[UUID, _Ticket] = field(default_factory=OrderedDict)


# Module-level state.  One entry per engagement that has had at least one acquire.
# Same in-process-only posture as _channels in service.py.
_states: dict[UUID, _EngagementState] = {}


def _get_state(engagement_id: UUID) -> _EngagementState:
    """Return the existing state record or create a default one (limit=3)."""
    if engagement_id not in _states:
        _states[engagement_id] = _EngagementState(slot_limit=3)
    return _states[engagement_id]


# ---------------------------------------------------------------------------
# Host resolution (Risk 5: must match _enforce_sandbox_guard exactly)
# ---------------------------------------------------------------------------


def parse_host(raw: str) -> str:
    """Extract the lowercase hostname from a raw target string.

    Handles:
    - Full URLs: ``http://localhost:3000`` → ``localhost``
    - Bare host[:port]: ``localhost:3000`` → ``localhost``
    - Userinfo smuggling: ``localhost:3000@evil.com`` → ``evil.com``

    Public so ``service._enforce_sandbox_guard`` can share the exact same logic,
    keeping the lock host and the sandbox-guard host always identical (Risk 5).
    """
    parsed = urlparse(raw)
    if parsed.netloc:
        # Full URL with scheme: parsed.hostname strips port AND userinfo.
        host = parsed.hostname or ""
    else:
        # Bare host[:port][/path] — synthesise ``//`` so urlparse parses the
        # authority correctly, defeating userinfo smuggling.
        host = urlparse(f"//{raw}").hostname or ""
    return host.lower()


def resolve_target_host(
    server_name: str,  # noqa: ARG001 — reserved for future per-server overrides
    tool_name: str,  # noqa: ARG001 — reserved for future per-tool overrides
    args: dict[str, Any],
) -> str | None:
    """Derive the lockable host from the tool's args.

    Returns the lowercase hostname (without port) extracted from ``args["target"]``,
    or ``None`` if the tool has no ``target`` argument (e.g. ``run_command``).
    Tools that return ``None`` acquire only a concurrency slot, not a host lock.

    The parsing logic is identical to ``service._enforce_sandbox_guard`` so the
    lock key and the sandbox guard key are always in agreement (Risk 5).
    """
    target = args.get("target")
    if not isinstance(target, str) or not target:
        return None
    return parse_host(target)


# ---------------------------------------------------------------------------
# Admission scan (internal)
# ---------------------------------------------------------------------------


def _scan_and_admit(state: _EngagementState) -> bool:
    """Walk the FIFO queue and admit the front-most eligible waiter(s).

    Called by ``release`` (and by ``acquire`` itself for the fast path).
    Re-entrant-safe: if the event loop is already processing a scan triggered
    by a concurrent release, this call will simply find nothing new to admit
    (either the slot is now taken again or the host is still locked).

    Eligibility for admission:
        - At least one slot is free (``state.slot_limit - state.in_use > 0``).
        - ``target_host`` is ``None`` OR ``target_host`` is not in ``locked_hosts``.

    FIFO policy: we walk tickets in insertion order.  If the front ticket is
    blocked by a host lock but a slot is free, we continue scanning for the next
    eligible ticket.  If the front ticket is blocked because no slots are free,
    we stop immediately — no later ticket can be admitted either (they would also
    need a slot).

    Returns ``True`` if at least one ticket was admitted (so the caller can
    schedule position-update re-broadcasts for the still-waiting tickets).
    """
    admitted_any = False
    for ticket in list(state.queue.values()):
        available = state.slot_limit - state.in_use
        if available <= 0:
            # No slots free — nothing can be admitted regardless of host.
            # FIFO invariant: do NOT skip this slot-blocked front waiter.
            break

        # A slot is free.  Check host eligibility.
        if ticket.target_host is not None and ticket.target_host in state.locked_hosts:
            # This ticket is blocked by a host lock.  A slot is free so we may
            # skip this ticket and look for a later eligible one (Decision 5).
            continue

        # Ticket is eligible — admit it.
        state.queue.pop(ticket.tool_run_id)
        state.in_use += 1
        if ticket.target_host is not None:
            state.locked_hosts.add(ticket.target_host)
        ticket.admitted.set()
        admitted_any = True
        # After admitting one ticket, loop again to see if more can be admitted
        # (e.g. two slots free, two waiters with different hosts).
        # The next iteration re-checks ``available`` with the updated in_use.
    return admitted_any


def _compute_reason(state: _EngagementState, ticket: _Ticket) -> QueueReason:
    """Recompute the current queue reason for a still-waiting ticket.

    Called after a release-driven admission to determine the fresh reason for
    each still-waiting ticket.  A ticket is slot_full when no slots are free
    (regardless of host), or when the ticket has no target host (a host-less
    run can only ever wait on a slot, never on a per-host lock).  A ticket is
    target_locked when a slot IS free AND the ticket has a target host that is
    currently held by another run.
    """
    available = state.slot_limit - state.in_use
    if available <= 0 or ticket.target_host is None:
        return "slot_full"
    # Slot is free and the ticket has a target host — the only reason this
    # ticket is still waiting is that its host is locked.  (If its host were
    # free AND a slot were available the scan would have admitted it.)
    return "target_locked"


async def _rebroadcast_positions(state: _EngagementState) -> None:
    """Re-invoke on_queued for every ticket still waiting in *state.queue*.

    Called as a background task after a release-driven admission so each waiting
    run receives an updated 1-based queue position and a freshly computed reason.
    Also updates ``ticket.reason`` in-place so that ``snapshot()`` returns the
    current reason rather than the stale initial value.

    Error isolation: a failing broadcast to one channel is caught, logged, and
    swallowed so that one dead WebSocket client cannot wedge the queue or block
    admission notifications for other waiters.
    """
    for position, ticket in enumerate(state.queue.values(), start=1):
        reason = _compute_reason(state, ticket)
        # Keep the stored reason current so snapshot() returns the live value.
        ticket.reason = reason
        try:
            result = ticket.on_queued(position, reason)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 — isolate per-channel errors
            logger.exception(
                "Failed to re-broadcast queue position to tool_run_id=%s (position=%d)",
                ticket.tool_run_id,
                position,
            )


def _schedule_rebroadcast(state: _EngagementState) -> None:
    """Schedule _rebroadcast_positions as a background task if the event loop is running.

    Uses asyncio.get_running_loop() so this is safe to call from synchronous code
    that is running inside an async context (e.g. ``release`` called from a
    ``finally`` block in an async function).  If no loop is running (e.g. a sync
    unit test that calls ``release`` directly), the re-broadcast is a silent no-op —
    correctness is preserved because the waiter's original on_queued was already
    called at enqueue time.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running event loop — we are in a sync context (e.g. sync test).
        # Re-broadcasts are not possible; skip silently.
        return
    task = loop.create_task(_rebroadcast_positions(state))
    # Keep a strong reference so the GC does not collect the task before it runs.
    _rebroadcast_tasks.add(task)
    task.add_done_callback(_rebroadcast_tasks.discard)


# Strong-reference set for background rebroadcast tasks (mirrors _background_tasks
# in service.py — asyncio.create_task returns a weakly-referenced task).
_rebroadcast_tasks: set[asyncio.Task[None]] = set()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_queue_capacity(engagement_id: UUID) -> None:
    """Synchronous pre-flight capacity check — raise ToolQueueFullError if the queue is full.

    Call this BEFORE creating any database rows or spawning any tasks for a heavy
    run on the async path.  This eliminates the gross unbounded amplification that
    would otherwise occur: without this check a caller could commit thousands of
    ``tool_runs`` rows and spawn thousands of background tasks before the depth cap
    in ``acquire`` fires (inside the background task, after the request has already
    returned 202).

    Reads the SAME ``_get_state(...).queue`` length that ``acquire``'s depth guard
    uses.  Does NOT mutate state — it is purely a read.

    Why keep the cap inside ``acquire`` too?
        The cap inside ``acquire`` is retained as defence-in-depth: a small
        bounded race exists between this pre-check and the background task's
        ``acquire`` call (other coroutines can enqueue in the DB-commit await
        window).  A minor overshoot is acceptable; the gross unbounded case is
        eliminated by this pre-check.

    Only used for HEAVY runs.  Light runs never enter the queue so this function
    must not be called for them.
    """
    state = _get_state(engagement_id)
    if len(state.queue) >= MAX_QUEUE_DEPTH:
        raise ToolQueueFullError(
            f"Engagement {engagement_id} has reached the maximum queue depth "
            f"({MAX_QUEUE_DEPTH}). Retry when a queued run completes."
        )


async def acquire(
    engagement_id: UUID,
    slot_limit: int,
    tool_run_id: UUID,
    target_host: str | None,
    server_name: str,
    tool_name: str,
    *,
    on_queued: OnQueuedCallback,
    on_started: OnStartedCallback,
) -> AdmissionHandle:
    """Admit a heavy tool run, blocking until a slot + host lock are available.

    Parameters
    ----------
    engagement_id:
        The engagement this run belongs to (determines the pool to use).
    slot_limit:
        Current configured slot limit for the engagement.  Passed in so the
        manager always operates on the most recently configured value without
        needing a DB round-trip (``set_slot_limit`` can also update it).
    tool_run_id:
        Unique identifier for this run (used as the queue key and in the handle).
    target_host:
        Lowercase hostname to lock, or ``None`` for tools with no target.
    server_name / tool_name:
        Metadata stored in the ticket for ``snapshot()``.
    on_queued(position, reason):
        Called (possibly async) when the run cannot be admitted immediately.
        Receives the 1-based queue position and the reason string.
    on_started():
        Called (possibly async) once the run is admitted.

    Returns
    -------
    AdmissionHandle
        Pass to ``release()`` when the run finishes (in a ``finally`` block).
    """
    state = _get_state(engagement_id)
    # Always apply the caller-supplied slot_limit (ensures fresh config is used).
    state.slot_limit = slot_limit

    available = state.slot_limit - state.in_use
    host_free = target_host is None or target_host not in state.locked_hosts

    if available > 0 and host_free and not state.queue:
        # Fast path: admit immediately (no queue, slot free, host free).
        state.in_use += 1
        if target_host is not None:
            state.locked_hosts.add(target_host)
        result = on_started()
        if asyncio.iscoroutine(result):
            await result
        return AdmissionHandle(
            engagement_id=engagement_id,
            tool_run_id=tool_run_id,
            target_host=target_host,
        )

    # Slow path: enqueue the ticket.
    # Guard the per-engagement queue depth before adding (Security Medium-1).
    if len(state.queue) >= MAX_QUEUE_DEPTH:
        raise ToolQueueFullError(
            f"Engagement {engagement_id} has reached the maximum queue depth "
            f"({MAX_QUEUE_DEPTH}). Retry when a queued run completes."
        )

    reason: QueueReason = (
        "slot_full" if (available <= 0 or target_host is None) else "target_locked"
    )
    ticket = _Ticket(
        tool_run_id=tool_run_id,
        server_name=server_name,
        tool_name=tool_name,
        target_host=target_host,
        enqueued_at=datetime.now(tz=UTC),
        on_queued=on_queued,
        reason=reason,
    )
    state.queue[tool_run_id] = ticket

    # Compute 1-based position.
    position = _position_in_queue(state, tool_run_id)
    result = on_queued(position, reason)
    if asyncio.iscoroutine(result):
        await result

    # Wait until the admission scan wakes us.
    await ticket.admitted.wait()

    # Admitted — call on_started.
    result = on_started()
    if asyncio.iscoroutine(result):
        await result

    return AdmissionHandle(
        engagement_id=engagement_id,
        tool_run_id=tool_run_id,
        target_host=target_host,
    )


def release(handle: AdmissionHandle) -> None:
    """Return the slot and host lock, then trigger the admission scan.

    Idempotent: safe to call multiple times or after an error (Risk 3).
    Always runs even if the engagement state has been reset (e.g. in tests).

    After the admission scan, if any waiters were admitted, schedules an async
    re-broadcast task that updates the queue positions of the still-waiting
    tickets (Task 4).  If no event loop is running (sync test context), the
    re-broadcast is skipped silently — correctness is preserved because each
    waiter already received its initial position at enqueue time.
    """
    if handle.released:
        return  # Idempotency guard.
    handle.released = True

    state = _states.get(handle.engagement_id)
    if state is None:
        # State was reset (e.g. _reset() was called).  Nothing to do.
        return

    # Return the slot.
    state.in_use = max(0, state.in_use - 1)

    # Drop the host lock.
    if handle.target_host is not None:
        state.locked_hosts.discard(handle.target_host)

    # Admit next eligible waiter(s).
    admitted = _scan_and_admit(state)

    # If any waiters were admitted, the remaining waiters' positions have shifted.
    # Schedule an async re-broadcast so each still-waiting run receives an updated
    # queued chunk.  This must happen AFTER the scan bookkeeping completes so that
    # the positions and reasons we broadcast reflect the current state.
    if admitted and state.queue:
        _schedule_rebroadcast(state)


# ---------------------------------------------------------------------------
# Queue introspection
# ---------------------------------------------------------------------------


def _position_in_queue(state: _EngagementState, tool_run_id: UUID) -> int:
    """Return 1-based FIFO position of *tool_run_id* in *state.queue*.

    Returns 0 if not found (should not happen in normal flow).
    """
    for i, key in enumerate(state.queue.keys(), start=1):
        if key == tool_run_id:
            return i
    return 0


def position_of(tool_run_id: UUID) -> int | None:
    """Return the 1-based queue position of *tool_run_id* across all engagements.

    Returns ``None`` if the run is not currently queued (either it was admitted,
    it does not exist, or it is a light run).
    """
    for state in _states.values():
        pos = _position_in_queue(state, tool_run_id)
        if pos > 0:
            return pos
    return None


def snapshot(engagement_id: UUID) -> ToolQueueSnapshot:
    """Return a point-in-time snapshot of the pool for *engagement_id*.

    If no state record exists (no heavy runs have ever been acquired for this
    engagement), returns an empty snapshot with the default slot limit of 3.
    """
    state = _states.get(engagement_id)
    if state is None:
        return ToolQueueSnapshot(
            slot_limit=3,
            running_count=0,
            queued_count=0,
            queued=[],
        )

    queued_runs: list[QueuedRun] = []
    for position, ticket in enumerate(state.queue.values(), start=1):
        # Compute the live reason rather than using the frozen initial value so
        # that the polled GET /tool-queue reason matches the WS re-broadcast
        # reason (Finding W1 / snapshot stale-reason fix).
        live_reason = _compute_reason(state, ticket)
        queued_runs.append(
            QueuedRun(
                tool_run_id=ticket.tool_run_id,
                server_name=ticket.server_name,
                tool_name=ticket.tool_name,
                target_host=ticket.target_host,
                position=position,
                reason=live_reason,
                enqueued_at=ticket.enqueued_at,
            )
        )

    return ToolQueueSnapshot(
        slot_limit=state.slot_limit,
        running_count=state.in_use,
        queued_count=len(state.queue),
        queued=queued_runs,
    )


# ---------------------------------------------------------------------------
# Slot-limit management
# ---------------------------------------------------------------------------


def set_slot_limit(engagement_id: UUID, n: int) -> None:
    """Update the slot limit for *engagement_id*.

    Growing the limit may immediately admit queued waiters (via the scan).
    Shrinking below the current ``in_use`` count makes ``available`` negative,
    which simply prevents new admissions until releases catch up — running slots
    are NEVER preempted (Risk 6).
    """
    state = _get_state(engagement_id)
    state.slot_limit = n
    # Growing: run scan in case waiters can now be admitted.
    admitted = _scan_and_admit(state)
    if admitted and state.queue:
        _schedule_rebroadcast(state)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _reset() -> None:
    """Clear all admission state.  For use in tests only (mirrors service._reset_channels)."""
    _states.clear()
    _rebroadcast_tasks.clear()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _maybe_await(result: Awaitable[None] | None) -> None:  # pragma: no cover
    """No-op placeholder; coroutine results are awaited inline in acquire()."""
    # acquire() handles coroutines directly with ``if asyncio.iscoroutine``.
    # This function exists only as documentation of the dual sync/async callback
    # pattern.  It is intentionally unreachable at runtime.
    pass
