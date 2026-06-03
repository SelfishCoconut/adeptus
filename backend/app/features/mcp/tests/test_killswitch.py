"""Pure-asyncio unit tests for Slice 06 kill-switch + timeout-confirm primitives.

No DB, no subprocess, no external services.  All tests exercise the in-process
admission manager directly via the new Slice 06 API added to concurrency.py.

Test matrix (from the Slice 06 spec task 3):
  - test_kill_running_task_cancels_it_and_releases_slot
  - test_kill_queued_run_dequeues_and_raises_run_killed
  - test_kill_absent_run_returns_absent
  - test_release_for_decision_frees_slot_so_waiter_admits
  - test_await_timeout_decision_blocks_until_submit
  - test_extend_decision_allows_reacquire
  - test_wait_decision_allows_reacquire
  - test_kill_awaiting_decision_run_returns_awaiting
  - test_set_paused_true_kills_running_and_dequeues
  - test_set_paused_false_re_allows_acquire
  - test_pause_keyspace_isolation
  - test_submit_timeout_decision_non_waiting_returns_false
  - test_double_submit_second_returns_false
  - test_slot_accounting_invariant_park_extend_complete
  - test_pause_kills_awaiting_decision_run
  - test_kill_run_returns_absent_for_terminal_run
  - test_set_paused_true_idempotent_when_already_paused
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.features.mcp.concurrency import (
    AdmissionHandle,
    EngagementPaused,
    RunKilled,
    _reset,
    acquire,
    await_timeout_decision,
    cleanup_decision,
    is_paused,
    kill_run,
    mark_slot_reacquired,
    register_run,
    release,
    release_for_decision,
    set_paused,
    snapshot,
    submit_timeout_decision,
    unregister_run,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _noop_queued(position: int, reason: str) -> None:
    """No-op on_queued callback."""


def _noop_started() -> None:
    """No-op on_started callback."""


def _eng() -> UUID:
    return uuid4()


def _run_id() -> UUID:
    return uuid4()


async def _acquire(
    engagement_id: UUID,
    *,
    slot_limit: int = 3,
    tool_run_id: UUID | None = None,
    target_host: str | None = "localhost",
    server_name: str = "httpx",
    tool_name: str = "run_httpx_heavy",
    on_queued: Any = None,
    on_started: Any = None,
) -> AdmissionHandle:
    """Thin wrapper around ``acquire`` with sane defaults for tests."""
    if tool_run_id is None:
        tool_run_id = _run_id()
    return await acquire(
        engagement_id=engagement_id,
        slot_limit=slot_limit,
        tool_run_id=tool_run_id,
        target_host=target_host,
        server_name=server_name,
        tool_name=tool_name,
        on_queued=on_queued or _noop_queued,
        on_started=on_started or _noop_started,
    )


def _make_dummy_task() -> asyncio.Task[None]:
    """Create a real asyncio Task wrapping a never-ending coroutine for registry use."""

    async def _forever() -> None:
        await asyncio.sleep(3600)

    return asyncio.create_task(_forever())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_state() -> None:
    """Reset module-level state before each test."""
    _reset()


# ---------------------------------------------------------------------------
# kill_run — running task
# ---------------------------------------------------------------------------


async def test_kill_running_task_cancels_it_and_releases_slot() -> None:
    """Killing a running task cancels it; the freed slot lets the next waiter in."""
    eng = _eng()
    run_id = _run_id()

    # A real task that simulates a running tool.
    done_event = asyncio.Event()
    cancelled_event = asyncio.Event()

    async def _fake_run() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled_event.set()
            raise
        finally:
            done_event.set()

    task: asyncio.Task[None] = asyncio.create_task(_fake_run())

    # Acquire the slot, then register the task.
    handle = await _acquire(eng, slot_limit=1, tool_run_id=run_id)
    register_run(eng, run_id, task)

    s = snapshot(eng)
    assert s.running_count == 1

    # A second run queues.
    waiter_admitted: list[bool] = []
    waiter_task = asyncio.create_task(
        _acquire(
            eng,
            slot_limit=1,
            target_host="otherhost",
            on_started=lambda: waiter_admitted.append(True),
        )
    )
    await asyncio.sleep(0)
    assert snapshot(eng).queued_count == 1

    # Kill the running task.
    result = kill_run(run_id)
    assert result == "cancelled"

    # Let the cancellation propagate.
    await asyncio.sleep(0)
    assert cancelled_event.is_set(), "Task should have received CancelledError"

    # Simulate the task's finally: release the slot and unregister.
    release(handle)
    unregister_run(run_id)
    await asyncio.sleep(0)

    # The waiter should now be admitted.
    waiter_handle = await asyncio.wait_for(waiter_task, timeout=1.0)
    assert waiter_admitted, "Waiter must be admitted after slot is released"
    assert snapshot(eng).running_count == 1
    assert snapshot(eng).queued_count == 0

    # Clean up.
    release(waiter_handle)
    try:
        await asyncio.wait_for(task, timeout=0.1)
    except (TimeoutError, asyncio.CancelledError):
        pass


# ---------------------------------------------------------------------------
# kill_run — queued run
# ---------------------------------------------------------------------------


async def test_kill_queued_run_dequeues_and_raises_run_killed() -> None:
    """Killing a queued run removes its ticket and wakes acquire with RunKilled."""
    eng = _eng()
    run_id = _run_id()

    # Hold the only slot so run_id must queue.
    handle = await _acquire(eng, slot_limit=1, target_host="a-host")

    killed_exception: list[RunKilled] = []

    async def _queued_acquire() -> None:
        try:
            await acquire(
                engagement_id=eng,
                slot_limit=1,
                tool_run_id=run_id,
                target_host="other",
                server_name="httpx",
                tool_name="run_httpx_heavy",
                on_queued=_noop_queued,
                on_started=_noop_started,
            )
        except RunKilled as exc:
            killed_exception.append(exc)

    task = asyncio.create_task(_queued_acquire())
    await asyncio.sleep(0)  # Let it enqueue.

    assert snapshot(eng).queued_count == 1

    result = kill_run(run_id)
    assert result == "dequeued"

    await asyncio.sleep(0)
    assert task.done(), "Queued acquire task should have completed after kill"
    assert len(killed_exception) == 1, "RunKilled must be raised inside acquire"
    assert snapshot(eng).queued_count == 0

    release(handle)


# ---------------------------------------------------------------------------
# kill_run — absent/terminal run
# ---------------------------------------------------------------------------


def test_kill_absent_run_returns_absent() -> None:
    """Killing an unknown/terminal run returns 'absent'."""
    result = kill_run(_run_id())
    assert result == "absent"


async def test_kill_run_returns_absent_for_terminal_run() -> None:
    """A run that completed and was unregistered returns 'absent'."""
    eng = _eng()
    run_id = _run_id()

    handle = await _acquire(eng, slot_limit=1, tool_run_id=run_id)
    task = _make_dummy_task()
    register_run(eng, run_id, task)
    release(handle)
    unregister_run(run_id)
    task.cancel()

    result = kill_run(run_id)
    assert result == "absent"


# ---------------------------------------------------------------------------
# release_for_decision — slot freed so waiter can admit
# ---------------------------------------------------------------------------


async def test_release_for_decision_frees_slot_so_waiter_admits() -> None:
    """release_for_decision returns the slot; a same-host waiter can now admit."""
    eng = _eng()
    run_id = _run_id()

    # Acquire the only slot.
    handle = await _acquire(eng, slot_limit=1, tool_run_id=run_id, target_host="host1")

    task = _make_dummy_task()
    register_run(eng, run_id, task)

    # A second run queues (same engagement, different host so only slot is the blocker).
    waiter_admitted: list[bool] = []
    waiter_task = asyncio.create_task(
        _acquire(
            eng,
            slot_limit=1,
            target_host="host2",
            on_started=lambda: waiter_admitted.append(True),
        )
    )
    await asyncio.sleep(0)
    assert snapshot(eng).queued_count == 1

    # Timeout fires on run_id: release the slot for the decision.
    release_for_decision(eng, run_id, handle)

    # The handle is released; slot count should drop back to 0 (handle.released=True).
    # The waiter should now be admitted.
    await asyncio.sleep(0)

    waiter_handle = await asyncio.wait_for(waiter_task, timeout=1.0)
    assert waiter_admitted, "Waiter must admit after release_for_decision"
    assert snapshot(eng).queued_count == 0
    assert snapshot(eng).running_count == 1  # The waiter holds the slot.

    # run_id is in the registry but holds_slot=False.
    from app.features.mcp.concurrency import _registry

    assert run_id in _registry
    assert not _registry[run_id].holds_slot

    release(waiter_handle)
    task.cancel()
    cleanup_decision(run_id)
    unregister_run(run_id)


# ---------------------------------------------------------------------------
# await_timeout_decision / submit_timeout_decision
# ---------------------------------------------------------------------------


async def test_await_timeout_decision_blocks_until_submit() -> None:
    """await_timeout_decision blocks indefinitely; submit_timeout_decision resolves it."""
    eng = _eng()
    run_id = _run_id()

    handle = await _acquire(eng, slot_limit=1, tool_run_id=run_id)
    task = _make_dummy_task()
    register_run(eng, run_id, task)

    # Park the run.
    release_for_decision(eng, run_id, handle)

    decision_received: list[str] = []

    async def _await_decision() -> None:
        d, _ext = await await_timeout_decision(run_id)
        decision_received.append(d)

    decision_task = asyncio.create_task(_await_decision())

    # Give the event loop a turn — the awaiter should be blocked.
    await asyncio.sleep(0)
    assert not decision_task.done(), "await_timeout_decision must block until a decision arrives"
    assert not decision_received

    # Now submit the decision.
    ok = submit_timeout_decision(run_id, "kill")
    assert ok is True

    await asyncio.sleep(0)
    assert decision_task.done()
    assert decision_received == ["kill"]

    task.cancel()
    cleanup_decision(run_id)
    unregister_run(run_id)


async def test_extend_decision_arrives() -> None:
    """submit_timeout_decision with 'extend' resolves await_timeout_decision."""
    eng = _eng()
    run_id = _run_id()

    handle = await _acquire(eng, slot_limit=1, tool_run_id=run_id)
    task = _make_dummy_task()
    register_run(eng, run_id, task)
    release_for_decision(eng, run_id, handle)

    decision_task = asyncio.create_task(await_timeout_decision(run_id))
    await asyncio.sleep(0)
    assert not decision_task.done()

    submit_timeout_decision(run_id, "extend")
    d, _ext = await asyncio.wait_for(decision_task, timeout=1.0)
    assert d == "extend"

    task.cancel()
    cleanup_decision(run_id)
    unregister_run(run_id)


async def test_extend_decision_carries_extend_seconds() -> None:
    """submit_timeout_decision passes extend_seconds through to await_timeout_decision."""
    eng = _eng()
    run_id = _run_id()

    handle = await _acquire(eng, slot_limit=1, tool_run_id=run_id)
    task = _make_dummy_task()
    register_run(eng, run_id, task)
    release_for_decision(eng, run_id, handle)

    decision_task = asyncio.create_task(await_timeout_decision(run_id))
    await asyncio.sleep(0)

    submit_timeout_decision(run_id, "extend", extend_seconds=90)
    d, ext = await asyncio.wait_for(decision_task, timeout=1.0)
    assert d == "extend"
    assert ext == 90

    task.cancel()
    cleanup_decision(run_id)
    unregister_run(run_id)


async def test_wait_decision_arrives() -> None:
    """submit_timeout_decision with 'wait' resolves await_timeout_decision."""
    eng = _eng()
    run_id = _run_id()

    handle = await _acquire(eng, slot_limit=1, tool_run_id=run_id)
    task = _make_dummy_task()
    register_run(eng, run_id, task)
    release_for_decision(eng, run_id, handle)

    decision_task = asyncio.create_task(await_timeout_decision(run_id))
    await asyncio.sleep(0)

    submit_timeout_decision(run_id, "wait")
    d, _ext = await asyncio.wait_for(decision_task, timeout=1.0)
    assert d == "wait"

    task.cancel()
    cleanup_decision(run_id)
    unregister_run(run_id)


# ---------------------------------------------------------------------------
# extend/wait decisions allow re-acquire
# ---------------------------------------------------------------------------


async def test_extend_decision_lets_task_reacquire() -> None:
    """After an 'extend' decision the task can re-acquire a slot through the normal path.

    C-1 regression guard: mark_slot_reacquired must be called (as service.py does)
    after the re-acquire so that a subsequent kill_run sees holds_slot=True and
    correctly cancels the live task.  Without mark_slot_reacquired, kill_run takes
    the awaiting-decision branch and calls _submit_decision_internal against a
    cleaned-up rendezvous → returns 'awaiting' silently (no-op kill).
    """
    eng = _eng()
    run_id = _run_id()

    handle = await _acquire(eng, slot_limit=1, tool_run_id=run_id, target_host="host1")
    task = _make_dummy_task()
    register_run(eng, run_id, task)

    # Park.
    release_for_decision(eng, run_id, handle)
    assert snapshot(eng).running_count == 0

    # Submit extend decision.
    submit_timeout_decision(run_id, "extend")
    d, _ext = await await_timeout_decision(run_id)
    assert d == "extend"
    cleanup_decision(run_id)

    # Re-acquire a fresh slot (as the streaming task would do on extend).
    new_handle = await _acquire(eng, slot_limit=1, tool_run_id=run_id, target_host="host1")
    # C-1: call mark_slot_reacquired so kill_run routes correctly (Risk 7).
    # Without this call kill_run sees holds_slot=False → "awaiting" branch → silent no-op.
    mark_slot_reacquired(run_id)

    assert snapshot(eng).running_count == 1

    # C-1 regression check: after re-acquire + mark_slot_reacquired, kill_run must
    # return "cancelled" (not "awaiting"), proving the task would actually be stopped.
    result = kill_run(run_id)
    assert result == "cancelled", (
        "kill_run must return 'cancelled' for a resumed (extend/wait) run — "
        "holds_slot must be True after mark_slot_reacquired"
    )

    release(new_handle)
    unregister_run(run_id)
    task.cancel()


async def test_wait_decision_lets_task_reacquire() -> None:
    """After a 'wait' decision the task can re-acquire a slot.

    C-1 regression guard: same as the extend variant — mark_slot_reacquired must
    be called so kill_run can cancel the resumed task.
    """
    eng = _eng()
    run_id = _run_id()

    handle = await _acquire(eng, slot_limit=1, tool_run_id=run_id, target_host="hostX")
    task = _make_dummy_task()
    register_run(eng, run_id, task)

    release_for_decision(eng, run_id, handle)
    submit_timeout_decision(run_id, "wait")
    d, _ext = await await_timeout_decision(run_id)
    assert d == "wait"
    cleanup_decision(run_id)

    new_handle = await _acquire(eng, slot_limit=1, tool_run_id=run_id, target_host="hostX")
    # C-1: restore holds_slot so kill_run routes to the "cancel task" branch.
    mark_slot_reacquired(run_id)
    assert snapshot(eng).running_count == 1

    # Verify kill_run correctly cancels the resumed task.
    result = kill_run(run_id)
    assert result == "cancelled", "kill_run must return 'cancelled' for a resumed (wait) run"

    release(new_handle)
    unregister_run(run_id)
    task.cancel()


# ---------------------------------------------------------------------------
# Re-acquire respects FIFO queue and pause flag
# ---------------------------------------------------------------------------


async def test_reacquire_respects_fifo_queue() -> None:
    """When re-acquiring on extend, the run must respect the FIFO queue (may wait)."""
    eng = _eng()
    run_id = _run_id()
    other_id = _run_id()

    # slot_limit=1. run_id acquires the slot.
    handle = await _acquire(eng, slot_limit=1, tool_run_id=run_id, target_host="h1")
    task = _make_dummy_task()
    register_run(eng, run_id, task)

    # Park run_id (timeout) — slot freed.
    release_for_decision(eng, run_id, handle)
    assert snapshot(eng).running_count == 0

    # Another run grabs the freed slot immediately.
    other_handle = await _acquire(eng, slot_limit=1, tool_run_id=other_id, target_host="h1")
    assert snapshot(eng).running_count == 1

    # run_id tries to re-acquire but the slot is taken — it must queue.
    submit_timeout_decision(run_id, "extend")
    await await_timeout_decision(run_id)
    cleanup_decision(run_id)

    reacquire_admitted: list[bool] = []
    reacquire_task = asyncio.create_task(
        _acquire(
            eng,
            slot_limit=1,
            tool_run_id=run_id,
            target_host="h1",
            on_started=lambda: reacquire_admitted.append(True),
        )
    )
    await asyncio.sleep(0)
    assert not reacquire_task.done(), "Re-acquire must block while slot is held"

    # Free the other slot — run_id can now re-admit.
    release(other_handle)
    new_handle = await asyncio.wait_for(reacquire_task, timeout=1.0)
    assert reacquire_admitted

    release(new_handle)
    unregister_run(run_id)
    task.cancel()


async def test_reacquire_respects_pause_flag() -> None:
    """Re-acquiring on extend while paused raises EngagementPaused."""
    eng = _eng()
    run_id = _run_id()

    handle = await _acquire(eng, slot_limit=1, tool_run_id=run_id, target_host="h1")
    task = _make_dummy_task()
    register_run(eng, run_id, task)

    release_for_decision(eng, run_id, handle)
    submit_timeout_decision(run_id, "extend")
    await await_timeout_decision(run_id)
    cleanup_decision(run_id)

    # Pause the engagement before the re-acquire.
    set_paused(eng, True)

    with pytest.raises(EngagementPaused):
        await _acquire(eng, slot_limit=1, tool_run_id=run_id, target_host="h1")

    unregister_run(run_id)
    task.cancel()


# ---------------------------------------------------------------------------
# kill_run — awaiting-decision run
# ---------------------------------------------------------------------------


async def test_kill_awaiting_decision_run_returns_awaiting_and_resolves() -> None:
    """Killing an awaiting-decision run returns 'awaiting' and resolves the rendezvous."""
    eng = _eng()
    run_id = _run_id()

    handle = await _acquire(eng, slot_limit=1, tool_run_id=run_id)
    task = _make_dummy_task()
    register_run(eng, run_id, task)

    release_for_decision(eng, run_id, handle)

    # Start awaiting.
    decision_task = asyncio.create_task(await_timeout_decision(run_id))
    await asyncio.sleep(0)
    assert not decision_task.done()

    # Kill the awaiting run.
    result = kill_run(run_id)
    assert result == "awaiting"

    d, _ext = await asyncio.wait_for(decision_task, timeout=1.0)
    assert d == "kill"

    task.cancel()
    cleanup_decision(run_id)
    unregister_run(run_id)


# ---------------------------------------------------------------------------
# set_paused(True)
# ---------------------------------------------------------------------------


async def test_set_paused_true_kills_running_and_dequeues() -> None:
    """Pausing kills N running tasks + M queued, returns correct counts, blocks acquire."""
    eng = _eng()
    slot_limit = 2

    # Two running tasks.
    run1_id, run2_id = _run_id(), _run_id()
    handle1 = await _acquire(eng, slot_limit=slot_limit, tool_run_id=run1_id, target_host="h1")
    handle2 = await _acquire(eng, slot_limit=slot_limit, tool_run_id=run2_id, target_host="h2")

    cancelled1 = asyncio.Event()
    cancelled2 = asyncio.Event()

    async def _run1() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled1.set()
            raise

    async def _run2() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled2.set()
            raise

    task1: asyncio.Task[None] = asyncio.create_task(_run1())
    task2: asyncio.Task[None] = asyncio.create_task(_run2())
    register_run(eng, run1_id, task1)
    register_run(eng, run2_id, task2)

    # Queue two more runs.
    queued_killed: list[RunKilled] = []

    async def _queued(i: int) -> None:
        try:
            await acquire(
                engagement_id=eng,
                slot_limit=slot_limit,
                tool_run_id=_run_id(),
                target_host=f"qhost{i}",
                server_name="httpx",
                tool_name="run_httpx_heavy",
                on_queued=_noop_queued,
                on_started=_noop_started,
            )
        except RunKilled as exc:
            queued_killed.append(exc)

    q1_task = asyncio.create_task(_queued(1))
    q2_task = asyncio.create_task(_queued(2))
    await asyncio.sleep(0)
    assert snapshot(eng).queued_count == 2

    # Pause.
    killed_running, dequeued = set_paused(eng, True)
    assert killed_running == 2, f"Expected 2 running killed, got {killed_running}"
    assert dequeued == 2, f"Expected 2 dequeued, got {dequeued}"
    assert is_paused(eng)

    await asyncio.sleep(0)
    assert cancelled1.is_set()
    assert cancelled2.is_set()

    # Queued acquire tasks should have raised RunKilled.
    await asyncio.sleep(0)
    assert q1_task.done()
    assert q2_task.done()
    assert len(queued_killed) == 2

    # Attempting a new acquire must raise EngagementPaused.
    with pytest.raises(EngagementPaused):
        await _acquire(eng, slot_limit=slot_limit, target_host="newhost")

    # Clean up tasks.
    release(handle1)
    release(handle2)
    unregister_run(run1_id)
    unregister_run(run2_id)
    for t in [task1, task2]:
        try:
            await asyncio.wait_for(t, timeout=0.1)
        except (TimeoutError, asyncio.CancelledError):
            pass


async def test_set_paused_true_kills_awaiting_decision_runs() -> None:
    """Pausing kills awaiting-decision runs (not just running ones)."""
    eng = _eng()
    run_id = _run_id()

    handle = await _acquire(eng, slot_limit=1, tool_run_id=run_id)
    task = _make_dummy_task()
    register_run(eng, run_id, task)

    # Park the run — now awaiting-decision (slot released).
    release_for_decision(eng, run_id, handle)

    decision_task = asyncio.create_task(await_timeout_decision(run_id))
    await asyncio.sleep(0)

    # Pause should kill the awaiting-decision run.
    killed_running, dequeued = set_paused(eng, True)
    assert killed_running == 1
    assert dequeued == 0

    d, _ext = await asyncio.wait_for(decision_task, timeout=1.0)
    assert d == "kill"

    task.cancel()
    cleanup_decision(run_id)
    unregister_run(run_id)


# ---------------------------------------------------------------------------
# set_paused(False)
# ---------------------------------------------------------------------------


async def test_set_paused_false_re_allows_acquire() -> None:
    """Resuming the engagement clears the pause flag so acquire works again."""
    eng = _eng()

    set_paused(eng, True)
    assert is_paused(eng)

    with pytest.raises(EngagementPaused):
        await _acquire(eng, slot_limit=1)

    killed, dequeued = set_paused(eng, False)
    assert killed == 0
    assert dequeued == 0
    assert not is_paused(eng)

    handle = await _acquire(eng, slot_limit=1)
    assert snapshot(eng).running_count == 1
    release(handle)


async def test_set_paused_true_idempotent_when_already_paused() -> None:
    """Calling set_paused(True) on an already-paused engagement is a no-op success."""
    eng = _eng()

    set_paused(eng, True)
    # No running tasks, no queue — second pause is a no-op.
    killed, dequeued = set_paused(eng, True)
    assert killed == 0
    assert dequeued == 0
    assert is_paused(eng)


# ---------------------------------------------------------------------------
# Pause keyspace isolation
# ---------------------------------------------------------------------------


async def test_pause_keyspace_isolation() -> None:
    """Pausing engagement A must not affect engagement B's runs."""
    eng_a = _eng()
    eng_b = _eng()

    run_a = _run_id()
    run_b = _run_id()

    handle_a = await _acquire(eng_a, slot_limit=1, tool_run_id=run_a, target_host="ha")
    handle_b = await _acquire(eng_b, slot_limit=1, tool_run_id=run_b, target_host="hb")

    cancelled_b = asyncio.Event()

    async def _fake_b() -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled_b.set()
            raise

    task_a = _make_dummy_task()
    task_b: asyncio.Task[None] = asyncio.create_task(_fake_b())

    register_run(eng_a, run_a, task_a)
    register_run(eng_b, run_b, task_b)

    # Pause engagement A only.
    killed, dequeued = set_paused(eng_a, True)
    assert killed == 1  # Only run_a was killed.
    assert dequeued == 0

    await asyncio.sleep(0)

    # B must NOT have been cancelled.
    assert not cancelled_b.is_set(), "Pausing A must not cancel B's tasks"
    assert not is_paused(eng_b), "Pausing A must not affect B's pause state"

    # B can still acquire new runs.
    new_b_id = _run_id()
    new_handle_b = await _acquire(
        eng_b,
        slot_limit=2,
        tool_run_id=new_b_id,
        target_host="hb2",
    )
    assert snapshot(eng_b).running_count == 2

    release(handle_a)
    release(handle_b)
    release(new_handle_b)
    unregister_run(run_a)
    unregister_run(run_b)
    task_a.cancel()
    task_b.cancel()
    for t in [task_a, task_b]:
        try:
            await asyncio.wait_for(t, timeout=0.1)
        except (TimeoutError, asyncio.CancelledError):
            pass


# ---------------------------------------------------------------------------
# submit_timeout_decision — non-waiting run → False
# ---------------------------------------------------------------------------


def test_submit_timeout_decision_non_waiting_returns_false() -> None:
    """submit_timeout_decision for a run with no rendezvous returns False."""
    result = submit_timeout_decision(_run_id(), "kill")
    assert result is False


async def test_double_submit_second_returns_false() -> None:
    """A second concurrent submit_timeout_decision returns False (first writer wins)."""
    eng = _eng()
    run_id = _run_id()

    handle = await _acquire(eng, slot_limit=1, tool_run_id=run_id)
    task = _make_dummy_task()
    register_run(eng, run_id, task)
    release_for_decision(eng, run_id, handle)

    # First submit wins.
    first = submit_timeout_decision(run_id, "kill")
    assert first is True

    # Second submit loses.
    second = submit_timeout_decision(run_id, "extend")
    assert second is False

    # The resolved decision is the first one.
    d, _ext = await await_timeout_decision(run_id)
    assert d == "kill"

    task.cancel()
    cleanup_decision(run_id)
    unregister_run(run_id)


# ---------------------------------------------------------------------------
# Slot-accounting invariant: park → extend → complete cycle (Risk 7)
# ---------------------------------------------------------------------------


async def test_slot_accounting_invariant_park_extend_complete() -> None:
    """Assert no slot leak and no double-acquire after a full park→extend→complete cycle.

    Sequence:
    1. Run A acquires a slot (in_use=1).
    2. Timeout fires: release_for_decision releases A's slot (in_use=0).
    3. Decision = 'extend': A re-acquires the slot (in_use=1).
    4. A completes: release the new handle (in_use=0).

    At every step the slot count must be consistent (no leak, no double-acquire).
    """
    eng = _eng()
    run_id = _run_id()
    slot_limit = 1

    # Step 1: acquire.
    handle1 = await _acquire(eng, slot_limit=slot_limit, tool_run_id=run_id, target_host="h1")
    task = _make_dummy_task()
    register_run(eng, run_id, task)

    assert snapshot(eng).running_count == 1, "After acquire: 1 slot in use"
    assert snapshot(eng).queued_count == 0

    # Step 2: timeout fires → release_for_decision.
    release_for_decision(eng, run_id, handle1)

    assert snapshot(eng).running_count == 0, "After release_for_decision: 0 slots in use"
    assert handle1.released, "handle1 must be marked released"

    # Step 3: decision = extend → re-acquire.
    submit_timeout_decision(run_id, "extend")
    d, _ext = await await_timeout_decision(run_id)
    assert d == "extend"
    cleanup_decision(run_id)

    handle2 = await _acquire(eng, slot_limit=slot_limit, tool_run_id=run_id, target_host="h1")

    assert snapshot(eng).running_count == 1, "After re-acquire: 1 slot in use"

    # Step 4: complete → release handle2.
    release(handle2)
    unregister_run(run_id)
    task.cancel()

    assert snapshot(eng).running_count == 0, "After complete: 0 slots in use (no leak)"

    # Additional assertion: handle1 is already released; calling release again is a no-op.
    release(handle1)  # Must not decrement below 0.
    assert snapshot(eng).running_count == 0, "Double-release via handle1 must be a no-op"


async def test_slot_accounting_no_double_acquire_on_kill_during_reacquire() -> None:
    """If a kill/pause arrives while the task is re-acquiring, no slot is leaked.

    Setup:
    - Slot released via release_for_decision.
    - Decision = 'extend'.
    - Another task holds the slot.
    - Re-acquire starts (queues).
    - Pause the engagement → the queued re-acquire wakes with RunKilled.
    - Verify in_use returns to baseline (0 running, 0 queued).
    """
    eng = _eng()
    run_id = _run_id()
    other_id = _run_id()
    slot_limit = 1

    handle1 = await _acquire(eng, slot_limit=slot_limit, tool_run_id=run_id, target_host="h1")
    task = _make_dummy_task()
    register_run(eng, run_id, task)

    # Release for decision.
    release_for_decision(eng, run_id, handle1)
    assert snapshot(eng).running_count == 0

    # Another run grabs the slot.
    other_handle = await _acquire(
        eng, slot_limit=slot_limit, tool_run_id=other_id, target_host="h2"
    )
    assert snapshot(eng).running_count == 1

    # Decision = extend; task tries to re-acquire but must queue.
    submit_timeout_decision(run_id, "extend")
    await await_timeout_decision(run_id)
    cleanup_decision(run_id)

    reacquire_killed: list[RunKilled] = []

    async def _try_reacquire() -> None:
        try:
            await acquire(
                engagement_id=eng,
                slot_limit=slot_limit,
                tool_run_id=run_id,
                target_host="h1",
                server_name="httpx",
                tool_name="run_httpx_heavy",
                on_queued=_noop_queued,
                on_started=_noop_started,
            )
        except (RunKilled, EngagementPaused) as exc:
            if isinstance(exc, RunKilled):
                reacquire_killed.append(exc)

    reacquire_task = asyncio.create_task(_try_reacquire())
    await asyncio.sleep(0)
    assert snapshot(eng).queued_count == 1, "Re-acquire should be queued"

    # Pause the engagement — should de-queue the re-acquire.
    set_paused(eng, True)
    await asyncio.sleep(0)

    assert reacquire_task.done(), "Re-acquire task should be unblocked by pause"
    assert len(reacquire_killed) == 1, "Re-acquire must raise RunKilled when pause de-queues it"

    # Baseline: the other run still holds its slot (pausing doesn't touch it since
    # it's not in the _registry — we didn't register it).
    # Release it manually to restore the clean baseline.
    release(other_handle)
    assert snapshot(eng).running_count == 0, "No slot leak after kill-during-reacquire"
    assert snapshot(eng).queued_count == 0

    unregister_run(run_id)
    task.cancel()


# ---------------------------------------------------------------------------
# W-2: set_paused on idle engagement (no _states entry yet)
# ---------------------------------------------------------------------------


async def test_set_paused_true_on_idle_engagement_persists_flag() -> None:
    """W-2: Pausing an engagement with no in-process state allocates state and sets paused.

    Subsequent acquire/is_paused must see the flag even though there were no
    prior runs.  This verifies that set_paused(True) allocates _EngagementState
    via _get_state even when the engagement had no prior admits.
    """
    eng = _eng()

    # No state exists yet — is_paused must return False before the pause.
    assert not is_paused(eng)

    killed, dequeued = set_paused(eng, True)
    assert killed == 0  # No runs in flight — nothing to kill.
    assert dequeued == 0
    assert is_paused(eng), "Pause flag must persist even when no state existed prior"

    # New acquire must raise EngagementPaused.
    with pytest.raises(EngagementPaused):
        await _acquire(eng, slot_limit=1)

    # Resume — must work without error and without allocation.
    set_paused(eng, False)
    assert not is_paused(eng)

    # After resume, a new run can be admitted.
    handle = await _acquire(eng, slot_limit=1)
    assert snapshot(eng).running_count == 1
    release(handle)


async def test_set_paused_false_on_absent_state_is_noop() -> None:
    """W-2: Resuming an engagement with no _states entry is a safe no-op (no allocation).

    set_paused(False) must return (0, 0) without creating a new _EngagementState.
    A subsequent is_paused must still return False (already the default for absent state).
    """
    from app.features.mcp.concurrency import _states

    eng = _eng()
    assert eng not in _states, "Precondition: no state allocated yet"

    killed, dequeued = set_paused(eng, False)
    assert killed == 0
    assert dequeued == 0
    assert eng not in _states, "set_paused(False) on absent state must NOT allocate _states entry"
    assert not is_paused(eng)


# ---------------------------------------------------------------------------
# mark_slot_reacquired — the C-1 fix
# ---------------------------------------------------------------------------


async def test_mark_slot_reacquired_restores_holds_slot() -> None:
    """C-1: mark_slot_reacquired sets holds_slot=True after release_for_decision."""
    from app.features.mcp.concurrency import _registry

    eng = _eng()
    run_id = _run_id()

    handle = await _acquire(eng, slot_limit=1, tool_run_id=run_id)
    task = _make_dummy_task()
    register_run(eng, run_id, task)

    # Park — holds_slot becomes False.
    release_for_decision(eng, run_id, handle)
    assert run_id in _registry
    assert not _registry[run_id].holds_slot

    # Restore — holds_slot must become True.
    mark_slot_reacquired(run_id)
    assert _registry[run_id].holds_slot, "holds_slot must be True after mark_slot_reacquired"

    # kill_run must now return "cancelled" (task is cancellable), not "awaiting".
    result = kill_run(run_id)
    assert result == "cancelled", (
        "kill_run must route to 'cancelled' for a slot-holding run after mark_slot_reacquired"
    )

    unregister_run(run_id)
    task.cancel()


def test_mark_slot_reacquired_absent_entry_is_noop() -> None:
    """mark_slot_reacquired on an absent entry is a safe no-op (defensive guard)."""
    # Should not raise even though the run was never registered.
    mark_slot_reacquired(_run_id())
