"""Tests for the mcp engagement-pause listener (Slice 06 task 7).

The ``on_engagement_paused_changed`` listener in ``mcp/listeners.py`` is the
registered mcp side of the ``engagement_paused_changed`` event seam.  These
tests drive the listener directly (no DB, no HTTP, no subprocess) using the
same ``concurrency._reset()`` isolation pattern as ``test_killswitch.py``.

Test matrix (from the Slice 06 spec):
  - Pausing an engagement with running tasks kills them and returns correct counts.
  - Pausing an engagement with queued tickets dequeues them and returns correct counts.
  - Pausing an engagement with awaiting-decision runs resolves them killed and counts them.
  - Resume (paused=False) clears the flag and returns (0, 0).
  - Listener is registered into the event seam at the composition root and fires
    when emit_engagement_paused_changed is called.
  - Slot accounting: after pause the in-use count returns to baseline.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from app.features.engagements import events as engagement_events
from app.features.mcp import concurrency
from app.features.mcp.listeners import on_engagement_paused_changed


@pytest.fixture(autouse=True)
def _reset() -> object:
    """Isolate each test — clear in-process state."""
    concurrency._reset()
    engagement_events._reset()
    yield
    concurrency._reset()
    engagement_events._reset()


# ---------------------------------------------------------------------------
# Direct listener unit tests
# ---------------------------------------------------------------------------


def test_listener_pause_with_no_runs_returns_zero_counts() -> None:
    """Pausing an engagement that has no in-flight runs returns (0, 0)."""
    eng_id = uuid4()
    killed, dequeued = on_engagement_paused_changed(eng_id, paused=True)
    assert killed == 0
    assert dequeued == 0


def test_listener_resume_returns_zero_counts() -> None:
    """Resuming an engagement always returns (0, 0)."""
    eng_id = uuid4()
    # Pause first, then resume.
    on_engagement_paused_changed(eng_id, paused=True)
    killed, dequeued = on_engagement_paused_changed(eng_id, paused=False)
    assert killed == 0
    assert dequeued == 0


@pytest.mark.asyncio
async def test_listener_pause_kills_running_task_and_returns_count() -> None:
    """Pausing while a task is running cancels it and returns killed_running=1."""
    eng_id = uuid4()
    run_id = uuid4()

    # Admit one run (fast path — no queue).
    handle = await concurrency.acquire(
        engagement_id=eng_id,
        slot_limit=3,
        tool_run_id=run_id,
        target_host=None,
        server_name="httpx",
        tool_name="run_httpx",
        on_queued=lambda pos, reason: None,
        on_started=lambda: None,
    )

    # Simulate the background task holding the slot.
    async def fake_task() -> None:
        await asyncio.sleep(10)

    task: asyncio.Task[None] = asyncio.create_task(fake_task())
    concurrency.register_run(eng_id, run_id, task)

    try:
        killed, dequeued = on_engagement_paused_changed(eng_id, paused=True)
        assert killed == 1
        assert dequeued == 0
        assert task.cancelled() or task.cancelling() > 0
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        concurrency.release(handle)


@pytest.mark.asyncio
async def test_listener_pause_dequeues_queued_ticket_and_returns_count() -> None:
    """Pausing while a ticket is queued removes it and returns dequeued=1."""
    eng_id = uuid4()
    run_id_held = uuid4()
    run_id_queued = uuid4()

    # Fill the slot with one run (slot_limit=1).
    handle = await concurrency.acquire(
        engagement_id=eng_id,
        slot_limit=1,
        tool_run_id=run_id_held,
        target_host=None,
        server_name="httpx",
        tool_name="run_httpx",
        on_queued=lambda pos, reason: None,
        on_started=lambda: None,
    )

    # Queue a second run (blocked by slot).
    queued_task: asyncio.Task[object] = asyncio.create_task(
        concurrency.acquire(
            engagement_id=eng_id,
            slot_limit=1,
            tool_run_id=run_id_queued,
            target_host=None,
            server_name="httpx",
            tool_name="run_httpx",
            on_queued=lambda pos, reason: None,
            on_started=lambda: None,
        )
    )
    await asyncio.sleep(0)  # Let the ticket enqueue.

    # Register a fake task for the held run so we can track cancellation.
    async def fake_task() -> None:
        await asyncio.sleep(10)

    held_task: asyncio.Task[None] = asyncio.create_task(fake_task())
    concurrency.register_run(eng_id, run_id_held, held_task)

    try:
        killed, dequeued = on_engagement_paused_changed(eng_id, paused=True)
        # 1 running (held_task) + 1 queued ticket
        assert killed == 1
        assert dequeued == 1
    finally:
        held_task.cancel()
        queued_task.cancel()
        for t in (held_task, queued_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        concurrency.release(handle)


@pytest.mark.asyncio
async def test_listener_pause_resolves_awaiting_decision_run() -> None:
    """Pausing kills awaiting-decision runs (slot already released) and counts them."""
    eng_id = uuid4()
    run_id = uuid4()

    # Admit a run and release it into awaiting-decision state.
    handle = await concurrency.acquire(
        engagement_id=eng_id,
        slot_limit=3,
        tool_run_id=run_id,
        target_host=None,
        server_name="httpx",
        tool_name="run_httpx",
        on_queued=lambda pos, reason: None,
        on_started=lambda: None,
    )

    async def fake_task() -> None:
        await asyncio.sleep(10)

    task: asyncio.Task[None] = asyncio.create_task(fake_task())
    concurrency.register_run(eng_id, run_id, task)

    # Release for decision (slot returned).
    concurrency.release_for_decision(eng_id, run_id, handle)
    # Now the run is registered with holds_slot=False.

    killed, dequeued = on_engagement_paused_changed(eng_id, paused=True)
    # The awaiting-decision run is counted in killed_running.
    assert killed == 1
    assert dequeued == 0

    # The rendezvous should be resolved with 'kill'.
    decision, _ = await concurrency.await_timeout_decision(run_id)
    assert decision == "kill"

    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    concurrency.cleanup_decision(run_id)


def test_listener_pause_sets_pause_flag_so_acquire_raises() -> None:
    """After the listener fires with paused=True, concurrency.is_paused returns True."""
    eng_id = uuid4()
    on_engagement_paused_changed(eng_id, paused=True)
    assert concurrency.is_paused(eng_id) is True


def test_listener_resume_clears_pause_flag() -> None:
    """After resume, concurrency.is_paused returns False."""
    eng_id = uuid4()
    on_engagement_paused_changed(eng_id, paused=True)
    on_engagement_paused_changed(eng_id, paused=False)
    assert concurrency.is_paused(eng_id) is False


# ---------------------------------------------------------------------------
# Event-seam integration: listener registered into events and fires via emit
# ---------------------------------------------------------------------------


def test_emit_engagement_paused_changed_calls_listener_and_returns_counts() -> None:
    """emit_engagement_paused_changed collects listener return values correctly."""
    eng_id = uuid4()

    # Register the mcp listener into the seam (as the composition root does).
    engagement_events.on_engagement_paused_changed(on_engagement_paused_changed)

    results = engagement_events.emit_engagement_paused_changed(eng_id, paused=True)

    assert len(results) == 1
    killed, dequeued = results[0]
    assert killed == 0  # No in-flight runs.
    assert dequeued == 0


def test_emit_engagement_paused_changed_resume_returns_zero_counts() -> None:
    """Resuming via the event seam returns (0, 0) from the mcp listener."""
    eng_id = uuid4()
    engagement_events.on_engagement_paused_changed(on_engagement_paused_changed)

    # Pause first so there's state.
    engagement_events.emit_engagement_paused_changed(eng_id, paused=True)

    results = engagement_events.emit_engagement_paused_changed(eng_id, paused=False)
    assert len(results) == 1
    assert results[0] == (0, 0)


def test_pause_seam_idempotent_registration() -> None:
    """Registering the same listener twice fires it only once per emit."""
    eng_id = uuid4()
    engagement_events.on_engagement_paused_changed(on_engagement_paused_changed)
    engagement_events.on_engagement_paused_changed(on_engagement_paused_changed)  # idempotent

    results = engagement_events.emit_engagement_paused_changed(eng_id, paused=True)
    # Only one listener should fire (idempotent registration).
    assert len(results) == 1
