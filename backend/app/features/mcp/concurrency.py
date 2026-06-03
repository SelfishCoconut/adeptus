"""In-process admission manager for the heavy-tool concurrency pool (Slice 05).

PURPOSE
-------
Serialize heavy tool runs through a bounded per-engagement slot pool plus a
per-(engagement, target-host) exclusive lock so two heavy tools against the same
host never overlap, while extra runs queue FIFO and surface their queue position.

Slice 06 adds: per-run cancellation registry, kill-run, engagement-wide pause,
and the timeout slot-release / re-acquire rendezvous.  All new state is kept in
this module because it manipulates the same ``_states`` queue and slot pool (lock,
in_use counter, host-lock set).  Splitting into a sibling ``killswitch.py`` would
require cross-module mutation of the same in-process records — a cohesion loss with
no benefit.  A NOTE in each section marks the Slice 06 additions.

LIGHT-RUN CONTRACT
------------------
Light tool runs do NOT call ``acquire`` at all.  The caller (``service.py``)
branches on ``weight == "heavy"`` before reaching this module.  Light runs bypass
the pool, the host lock, and the FIFO queue entirely — this is intentional and is
the central user-visible promise of this slice.

Note for Slice 06: the engagement-wide pause flag is checked by ``is_paused`` /
``acquire`` (fast and slow paths).  The caller (``service.py``) must also check
``is_paused`` before starting a *light* run (task 4) because light runs never reach
``acquire``.  This module exposes ``is_paused`` as a public helper for that purpose.

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

MULTI-WORKER SAFETY (Risk 2 / Slice 06 Risk 4)
-----------------------------------------------
This module uses module-level asyncio structures — the same posture as the Slice 04
pub/sub in ``service.py``.  It is NOT multi-worker safe: across multiple uvicorn
workers the same engagement can over-admit, and a kill/pause on worker A cannot
reach a task on worker B.  This is acceptable for the single-process Compose
deployment.  A multi-worker deployment would need a Postgres advisory-lock or
Redis-based gate — do NOT add either now.

LOCK KEYSPACE ISOLATION
-----------------------
Every per-engagement record is keyed by ``engagement_id`` (a UUID), and the host
lock set is nested inside that per-engagement record.  The per-target lock cannot
couple two different engagements — ``("eng-A", "localhost")`` and
``("eng-B", "localhost")`` are independent.

SLOT ACCOUNTING INVARIANT (Slice 06 Risk 7)
-------------------------------------------
Exactly one admission handle is outstanding per run at any instant.  The slot is
released in ``release_for_decision`` BEFORE the task awaits the human decision, and
re-acquired only after the decision resolves to extend/wait.  There is never a
window where the run holds two slots, and a released slot is never double-counted
on the way back.  A ``kill`` / pause decision resolves the parked task WITHOUT
re-acquiring.  The streaming task (service.py) is responsible for tracking exactly
one outstanding handle and ensuring the ``finally`` block only releases the CURRENT
handle (not the one already released by ``release_for_decision``).
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
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


# ---------------------------------------------------------------------------
# Slice 06 domain exceptions
# ---------------------------------------------------------------------------


class RunKilled(AdeptusError):
    """Raised inside ``acquire`` when a queued run's ticket is killed before admission.

    Callers of ``acquire`` in service.py must catch this exception and persist
    ``status='killed'`` for the run (without ever having called the subprocess).
    This exception is a domain exception — it is NOT translated to HTTP here.
    The HTTP translation happens in router.py (task 6).
    """

    def __init__(self, message: str = "Run was killed before it could start") -> None:
        super().__init__(message)


class EngagementPaused(AdeptusError):
    """Raised by ``acquire`` when the engagement is paused at the time of admission.

    Both fast and slow paths of ``acquire`` check the pause flag and raise this
    exception immediately so the caller (service.py) can return without creating
    a ``tool_runs`` row or spawning any task.  Translated to HTTP 409 in
    router.py (task 4).
    """

    def __init__(self, message: str = "Engagement is currently paused") -> None:
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
    """A single waiter in the FIFO queue.

    Slice 06 addition: ``killed`` sentinel field.  When ``kill_run`` or
    ``set_paused`` removes a ticket, it sets ``killed = True`` and then fires
    ``admitted`` so the waiting ``acquire`` coroutine wakes, checks the
    sentinel, and raises ``RunKilled``.
    """

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
    # Slice 06: set True by kill_run / set_paused before firing admitted so acquire
    # raises RunKilled.
    killed: bool = False


@dataclass
class _EngagementState:
    """All concurrency state for a single engagement."""

    slot_limit: int
    in_use: int = 0
    locked_hosts: set[str] = field(default_factory=set)
    # Ordered dict preserves insertion order (arrival order = FIFO).
    # Key: tool_run_id (UUID), Value: _Ticket
    queue: OrderedDict[UUID, _Ticket] = field(default_factory=OrderedDict)
    # Slice 06: engagement-wide pause flag.  When True, acquire raises EngagementPaused.
    paused: bool = False


# ---------------------------------------------------------------------------
# Slice 06 — module-level kill/pause/rendezvous maps
# ---------------------------------------------------------------------------


@dataclass
class _RunEntry:
    """Registry entry for a live run (running or awaiting-decision).

    ``holds_slot`` is True when the run currently holds a concurrency slot
    (i.e. it is running, not parked in awaiting-decision).  This is needed so
    pause / kill_run accounting knows whether to cancel the task (running) or
    just resolve the rendezvous (awaiting-decision).

    ``awaiting_since`` is set by ``release_for_decision`` to the UTC timestamp
    at which the run entered the awaiting-decision state.  It is surfaced by
    ``_row_to_result`` in service.py when the row's status is
    ``'awaiting_decision'``, satisfying the OpenAPI contract that documents
    ``awaiting_since`` as non-null while awaiting.  The value is in-process
    (not persisted as a DB column — per the slice Data-model section).
    """

    engagement_id: UUID
    task: asyncio.Task[None]
    holds_slot: bool = True
    awaiting_since: datetime | None = None


@dataclass
class _DecisionRendezvous:
    """Pending timeout-decision slot for one awaiting-decision run.

    ``event`` is set when a decision arrives; ``decision`` holds the value.
    ``resolved`` guards against a concurrent double-submit (first writer wins).
    ``extend_seconds`` is the additional time granted when decision == 'extend';
    ignored for 'kill' / 'wait' decisions.
    """

    event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: Literal["kill", "extend", "wait"] | None = None
    extend_seconds: int = 30
    resolved: bool = False


# Per-run cancellation registry: tool_run_id → _RunEntry
_registry: dict[UUID, _RunEntry] = {}

# Per-run timeout-decision rendezvous: tool_run_id → _DecisionRendezvous
_decisions: dict[UUID, _DecisionRendezvous] = {}


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

    Raises
    ------
    EngagementPaused
        If the engagement is paused when ``acquire`` is called (fast or slow path).
    RunKilled
        If the queued ticket is killed (by ``kill_run`` or ``set_paused``) before
        the run is admitted.
    ToolQueueFullError
        If the per-engagement queue is at ``MAX_QUEUE_DEPTH``.
    """
    state = _get_state(engagement_id)
    # Always apply the caller-supplied slot_limit (ensures fresh config is used).
    state.slot_limit = slot_limit

    # Slice 06: pause gate — fast path.
    if state.paused:
        raise EngagementPaused(f"Engagement {engagement_id} is currently paused")

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
    # Use try/finally so that if the waiting task is cancelled (e.g. by kill_run
    # via task.cancel()), the ticket is removed from the queue before re-raising.
    # Without this, a cancelled task would leave a ghost ticket in the queue,
    # blocking the FIFO forever (Risk 3 / Slice 06 Task 5).
    try:
        await ticket.admitted.wait()
    except asyncio.CancelledError:
        # Remove our ticket from the queue (if still there) so we don't block it.
        state.queue.pop(tool_run_id, None)
        raise  # Re-raise so the caller's CancelledError handling runs.

    # Slice 06: if the ticket was killed before admission, raise RunKilled.
    if ticket.killed:
        raise RunKilled(f"Run {tool_run_id} was killed before it could start")

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
# Slice 06 — Cancellation registry
# ---------------------------------------------------------------------------


def register_run(engagement_id: UUID, tool_run_id: UUID, task: asyncio.Task[None]) -> None:
    """Register a live run's asyncio.Task in the cancellation registry.

    Must be called by service.py right after ``asyncio.create_task(...)``.  Replaces
    the anonymous ``_background_tasks`` set with a keyed map so any run can be found
    and cancelled by ID.  The map holds a strong reference to the task (preventing GC
    from collecting it before it finishes).

    ``holds_slot=True`` on registration: the run has just been admitted and holds a
    concurrency slot.  ``release_for_decision`` sets ``holds_slot=False`` when the slot
    is returned to the pool (awaiting-decision state).
    """
    _registry[tool_run_id] = _RunEntry(
        engagement_id=engagement_id,
        task=task,
        holds_slot=True,
    )


def unregister_run(tool_run_id: UUID) -> None:
    """Remove a run from the cancellation registry.

    Must be called in the streaming task's ``finally`` block so the map does not
    grow without bound.  Safe to call if the run was never registered (no-op).
    """
    _registry.pop(tool_run_id, None)


def mark_slot_reacquired(tool_run_id: UUID) -> None:
    """Restore holds_slot=True after a successful re-acquire on extend/wait.

    Called by the streaming task (service.py) IMMEDIATELY AFTER each successful
    ``concurrency.acquire(...)`` on the extend AND wait paths, BEFORE resuming
    the stream.  This is the counterpart of ``release_for_decision`` which sets
    ``holds_slot=False`` when the run parks as awaiting-decision.

    Without this call ``kill_run`` sees ``holds_slot=False`` for a resumed run
    and routes to the awaiting-decision branch (_submit_decision_internal against
    an already-cleaned-up rendezvous), returning ``"awaiting"`` silently instead
    of cancelling the live task — a silent no-op (Risk 7).

    Safe to call if the entry is absent (no-op guard — defensive; the entry
    should always be present at this point in the streaming task lifecycle).
    """
    entry = _registry.get(tool_run_id)
    if entry is not None:
        entry.holds_slot = True


def kill_run(tool_run_id: UUID) -> Literal["cancelled", "dequeued", "awaiting", "absent"]:
    """Cancel or de-queue a single tool run.

    Returns
    -------
    ``"cancelled"``
        The run was live and holding a slot — its task was cancelled.  The task's
        ``finally`` block will release the slot/host-lock and persist
        ``status='killed'`` (that is task 5, not here).
    ``"dequeued"``
        The run had a FIFO ticket and was removed from the queue.  Its waiting
        ``acquire`` coroutine is woken with a killed sentinel and will raise
        ``RunKilled``.  The caller (service.py) must persist ``status='killed'``.
    ``"awaiting"``
        The run is in the awaiting-decision state (slot already released).  A
        ``kill`` decision is submitted to its rendezvous so the parked task
        resolves itself killed.
    ``"absent"``
        The run is not known (already terminal or never registered).
    """
    # Check live registry first (running or awaiting-decision).
    entry = _registry.get(tool_run_id)
    if entry is not None:
        if entry.holds_slot:
            # Running task — cancel it.  The task's finally releases the slot.
            entry.task.cancel()
            return "cancelled"
        else:
            # Awaiting-decision — submit a kill to the rendezvous.
            _submit_decision_internal(tool_run_id, "kill")
            return "awaiting"

    # Check the FIFO queue across all engagement states.
    for state in _states.values():
        ticket = state.queue.get(tool_run_id)
        if ticket is not None:
            # Remove the ticket and wake the waiter with the killed sentinel.
            state.queue.pop(tool_run_id)
            ticket.killed = True
            ticket.admitted.set()
            return "dequeued"

    return "absent"


# ---------------------------------------------------------------------------
# Slice 06 — Engagement pause set
# ---------------------------------------------------------------------------


def is_paused(engagement_id: UUID) -> bool:
    """Return True if the engagement is currently paused.

    Safe to call from service.py before starting a light run (which never calls
    ``acquire``, so the acquire-level guard does not protect it).
    """
    state = _states.get(engagement_id)
    return state.paused if state is not None else False


def set_paused(engagement_id: UUID, paused: bool) -> tuple[int, int]:
    """Set or clear the engagement-wide pause flag.

    Parameters
    ----------
    engagement_id:
        The engagement to pause/resume.
    paused:
        ``True`` to pause; ``False`` to resume.

    Returns
    -------
    ``(killed_running, dequeued)``
        ``killed_running`` — number of in-flight runs killed by this pause action
        (includes running tasks AND awaiting-decision runs); 0 when resuming.
        ``dequeued`` — number of queued tickets removed; 0 when resuming.

    Side effects when pausing
    -------------------------
    1. Set ``state.paused = True`` so ``acquire``'s fast and slow paths raise
       ``EngagementPaused`` on any new admission attempt.
    2. Cancel every live task for this engagement that is currently holding a slot.
    3. Submit a ``kill`` decision to every awaiting-decision run for this engagement.
    4. De-queue every ticket in this engagement's FIFO queue (wake with killed sentinel).

    Setting ``paused=False`` clears the flag only — killed runs are NOT resumed
    (kill is terminal).

    Allocation note: when setting ``paused=True`` on an engagement that has no
    in-process state yet, a new ``_EngagementState`` is created so the flag
    persists for future ``is_paused`` / ``acquire`` calls (otherwise the flag
    would be lost because ``is_paused`` returns False for absent state).  When
    setting ``paused=False`` on an absent state there is nothing to clear —
    return ``(0, 0)`` without allocating (no leak).
    """
    if not paused:
        # Resume — clear the flag if the state exists; if absent, nothing to do.
        state = _states.get(engagement_id)
        if state is not None:
            state.paused = False
        return (0, 0)

    # Pause — must ensure the flag persists for future acquire/is_paused calls.
    state = _get_state(engagement_id)
    state.paused = True

    killed_running = 0
    dequeued = 0

    # Kill every live running task for this engagement.
    # Pass msg="engagement paused" so the CancelledError handler in service.py
    # can distinguish pause-originated kills from per-tool kills and broadcast
    # the correct cause in the 'killed' WS chunk (spec: "killed by user" vs
    # "engagement paused").
    for run_id, entry in list(_registry.items()):
        if entry.engagement_id != engagement_id:
            continue
        if entry.holds_slot:
            entry.task.cancel(msg="engagement paused")
            killed_running += 1
        else:
            # Awaiting-decision run — submit kill to its rendezvous.
            _submit_decision_internal(run_id, "kill")
            killed_running += 1

    # De-queue every ticket in this engagement's FIFO queue.
    for ticket in list(state.queue.values()):
        state.queue.pop(ticket.tool_run_id)
        ticket.killed = True
        ticket.admitted.set()
        dequeued += 1

    return (killed_running, dequeued)


# ---------------------------------------------------------------------------
# Slice 06 — Timeout slot-release / re-acquire rendezvous
# ---------------------------------------------------------------------------


def release_for_decision(
    engagement_id: UUID,
    tool_run_id: UUID,
    handle: AdmissionHandle | None,
) -> None:
    """Release the admission slot + host lock immediately and park the run.

    Called by the streaming task (service.py) when the user-facing timeout fires.
    After this call:
    - The concurrency slot and host lock are returned to the FIFO queue (via the
      normal ``release`` path) so other waiters can be admitted while the human
      decides what to do with the timed-out run.  If ``handle`` is ``None`` (e.g.
      for a light tool that never acquired a slot), the slot-release step is skipped
      and only the rendezvous is created.
    - The run is marked ``holds_slot=False`` in the registry (awaiting-decision).
    - A fresh ``_DecisionRendezvous`` is created for this run.

    **Slot accounting invariant**: the caller must NOT call ``release(handle)``
    again in its ``finally`` block — it was already released here.  The streaming
    task must track which handle is currently outstanding and skip the ``finally``
    release for the already-released handle (or use a guard similar to
    ``AdmissionHandle.released`` which is set True by ``release``).

    After calling this function the streaming task should call
    ``await_timeout_decision(tool_run_id)`` to wait for the human's choice.
    """
    # Release the slot + host lock back to the queue so the FIFO can advance.
    # For light runs (handle=None) there is no slot to release.
    if handle is not None:
        release(handle)  # Idempotent — sets handle.released = True.

    # Mark the run as awaiting-decision (slotless) in the registry.
    # Record the UTC timestamp at which the run entered this state so that
    # _row_to_result in service.py can populate awaiting_since in the REST
    # response (OpenAPI contract: non-null while status == 'awaiting_decision').
    entry = _registry.get(tool_run_id)
    if entry is not None:
        entry.holds_slot = False
        entry.awaiting_since = datetime.now(UTC)

    # Create the decision rendezvous for this run.
    _decisions[tool_run_id] = _DecisionRendezvous()


async def await_timeout_decision(
    tool_run_id: UUID,
) -> tuple[Literal["kill", "extend", "wait"], int]:
    """Block until a human submits a timeout decision for *tool_run_id*.

    No deadline — the prompt stays open indefinitely (Decision 6 / Risk 8).
    Returns ``(decision, extend_seconds)`` once ``submit_timeout_decision`` fires.
    ``extend_seconds`` is only meaningful when ``decision == "extend"``; callers
    may ignore it for ``"kill"`` / ``"wait"`` decisions.

    Raises ``KeyError`` if no rendezvous exists for this run (programming error —
    call ``release_for_decision`` first).
    """
    rendezvous = _decisions[tool_run_id]
    await rendezvous.event.wait()
    # The decision must be set by submit_timeout_decision / _submit_decision_internal
    # before the event is fired.
    assert rendezvous.decision is not None  # invariant
    return rendezvous.decision, rendezvous.extend_seconds


def submit_timeout_decision(
    tool_run_id: UUID,
    decision: Literal["kill", "extend", "wait"],
    *,
    extend_seconds: int = 30,
) -> bool:
    """Submit a human timeout decision to the parked run's rendezvous.

    Returns ``True`` if the decision was accepted, ``False`` if no run is currently
    awaiting a decision for this ID (e.g. the run already resolved, was killed, or
    the ID is unknown).  The router translates ``False`` → HTTP 409.

    ``extend_seconds`` is stored in the rendezvous so the streaming task can read
    it after ``await_timeout_decision`` returns.  Only meaningful when
    ``decision == "extend"``; ignored for ``"kill"`` / ``"wait"``.

    Idempotent against concurrent double-submit: the first writer wins; subsequent
    calls return ``False`` without touching the already-resolved rendezvous.
    """
    rendezvous = _decisions.get(tool_run_id)
    if rendezvous is None:
        return False
    return _submit_decision_internal(tool_run_id, decision, extend_seconds=extend_seconds)


def _submit_decision_internal(
    tool_run_id: UUID,
    decision: Literal["kill", "extend", "wait"],
    *,
    extend_seconds: int = 30,
) -> bool:
    """Internal helper: set the decision on an existing rendezvous.

    Called by ``submit_timeout_decision``, ``kill_run`` (for awaiting-decision
    runs), and ``set_paused`` (same).  Returns False if already resolved.
    ``extend_seconds`` is stored on the rendezvous so the streaming task reads
    the caller-supplied value; defaults to 30 for internal kill/pause callers.
    """
    rendezvous = _decisions.get(tool_run_id)
    if rendezvous is None:
        return False
    if rendezvous.resolved:
        return False  # First writer wins; concurrent double-submit is a no-op.
    rendezvous.resolved = True
    rendezvous.decision = decision
    rendezvous.extend_seconds = extend_seconds
    rendezvous.event.set()
    return True


def cleanup_decision(tool_run_id: UUID) -> None:
    """Remove the decision rendezvous for a run that has finished resolving.

    Call this from the streaming task's finally block (or after the decision is
    acted upon) to prevent the map from growing without bound.
    """
    _decisions.pop(tool_run_id, None)


def get_awaiting_since(tool_run_id: UUID) -> datetime | None:
    """Return the UTC timestamp at which the run entered awaiting-decision state.

    Returns ``None`` if the run is not in the registry or is not currently
    awaiting a decision (i.e. ``awaiting_since`` was not set by
    ``release_for_decision``).

    Used by ``_row_to_result`` in service.py to populate the ``awaiting_since``
    field of ``ToolRunResult`` when ``status == 'awaiting_decision'`` (the
    OpenAPI contract documents it as non-null while awaiting).
    """
    entry = _registry.get(tool_run_id)
    if entry is None:
        return None
    return entry.awaiting_since


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _reset() -> None:
    """Clear all admission state.  For use in tests only (mirrors service._reset_channels)."""
    _states.clear()
    _rebroadcast_tasks.clear()
    # Slice 06: clear the kill/pause/rendezvous maps as well.
    _registry.clear()
    _decisions.clear()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _maybe_await(result: Awaitable[None] | None) -> None:  # pragma: no cover
    """No-op placeholder; coroutine results are awaited inline in acquire()."""
    # acquire() handles coroutines directly with ``if asyncio.iscoroutine``.
    # This function exists only as documentation of the dual sync/async callback
    # pattern.  It is intentionally unreachable at runtime.
    pass
