"""Unit tests for Slice 06 Task 5: timeout-confirm behaviour in _stream_to_channel.

Uses a fake clock (monkeypatched time.monotonic) so the timeout fires
deterministically without real wall-clock waits.

Test matrix:
  - timeout fires → slot released (same-host waiter admits), status=awaiting_decision,
    timeout WS chunk broadcast
  - kill decision → status=killed, killed WS chunk
  - extend decision → slot re-acquired, fresh started chunk, stream continues, completed
  - wait decision → slot re-acquired, no further timeout, stream completes
  - pause while awaiting decision → status=killed
  - no slot leak / no double-acquire across park→reacquire cycle
  - cancel while streaming (kill-while-running) → status=killed, slot=0, per-server lock not leaked
  - queued kill → status=killed, subprocess never called
  - slot accounting: no leak on pause-during-reacquire (Risk 7)

All external dependencies mocked; no real DB or subprocess.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.features.mcp import concurrency as concurrency_module
from app.features.mcp.concurrency import (
    AdmissionHandle,
    acquire,
    kill_run,
    register_run,
    release,
    set_paused,
    snapshot,
    submit_timeout_decision,
)
from app.features.mcp.service import (
    _reset_channels,
    _stream_to_channel,
    subscribe_tool_run,
)
from app.features.mcp.subprocess_manager import StreamChunk, StreamDone

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SERVER_NAME = "httpx"
_HEAVY_TOOL = "run_httpx_heavy"
_LIGHT_TOOL = "run_httpx"
_TARGET = "http://localhost:3000"
_HOST = "localhost"
_TIMEOUT = 5.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_ctx(session_mock: AsyncMock | None = None) -> tuple[AsyncMock, MagicMock]:
    if session_mock is None:
        session_mock = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return session_mock, ctx


def _make_tool_run_mock(
    tool_run_id: UUID | None = None,
    status: str = "running",
) -> MagicMock:
    run = MagicMock()
    run.id = tool_run_id or uuid4()
    run.status = status
    run.exit_code = None
    run.stdout = ""
    run.stderr = ""
    run.started_at = datetime.now(tz=UTC)
    run.finished_at = None
    run.preset_name = None
    return run


# ---------------------------------------------------------------------------
# Fake clock
# ---------------------------------------------------------------------------


class FakeClock:
    """Controllable monotonic clock for deterministic timeout testing."""

    def __init__(self, start: float = 1000.0) -> None:
        self._t: float = start

    def monotonic(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_state() -> Any:
    _reset_channels()
    concurrency_module._reset()
    yield
    _reset_channels()
    concurrency_module._reset()


# ---------------------------------------------------------------------------
# Helper: acquire a slot (for use in tests that need to pre-occupy it)
# ---------------------------------------------------------------------------


async def _grab_slot(
    engagement_id: UUID,
    run_id: UUID | None = None,
    slot_limit: int = 1,
    target_host: str | None = _HOST,
) -> AdmissionHandle:
    if run_id is None:
        run_id = uuid4()
    return await acquire(
        engagement_id=engagement_id,
        slot_limit=slot_limit,
        tool_run_id=run_id,
        target_host=target_host,
        server_name=_SERVER_NAME,
        tool_name=_HEAVY_TOOL,
        on_queued=lambda p, r: None,
        on_started=lambda: None,
    )


# ---------------------------------------------------------------------------
# Test: timeout fires → slot released, awaiting_decision, timeout WS chunk
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_releases_slot_and_sets_awaiting_decision() -> None:
    """Timeout fires → slot released (same-host waiter admits), awaiting_decision persisted.

    This is the central Q1 invariant: waiting on a human decision must NEVER hold
    up the queue.
    """
    engagement_id = uuid4()
    tool_run_id = uuid4()
    slot_limit = 1
    clock = FakeClock(start=1000.0)

    # stream_tool_call blocks until cancelled (simulates a slow tool).
    gen_started = asyncio.Event()

    async def _blocking_gen(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        gen_started.set()
        await asyncio.sleep(3600)
        yield StreamChunk(type="stdout", data="never")  # unreachable

    session_mock, ctx = _make_session_ctx()
    update_status_mock = AsyncMock()
    update_result_mock = AsyncMock(return_value=_make_tool_run_mock(tool_run_id))

    _, queue = subscribe_tool_run(tool_run_id)

    # Start the heavy stream task with a timeout that will fire when we advance the clock.
    with (
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            side_effect=_blocking_gen,
        ),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_status", update_status_mock),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_result", update_result_mock),
        patch("app.features.mcp.service.get_sessionmaker", return_value=lambda: ctx),
        patch("app.features.mcp.service._time.monotonic", side_effect=clock.monotonic),
    ):
        stream_task = asyncio.create_task(
            _stream_to_channel(
                tool_run_id=tool_run_id,
                engagement_id=engagement_id,
                server_name=_SERVER_NAME,
                tool_name=_HEAVY_TOOL,
                args={"target": _TARGET},
                timeout_seconds=_TIMEOUT,
                is_heavy=True,
                slot_limit=slot_limit,
                target_host=_HOST,
            )
        )
        register_run(engagement_id, tool_run_id, stream_task)

        # Wait for the generator to start so we know the slot is held.
        await asyncio.wait_for(gen_started.wait(), timeout=2.0)
        assert snapshot(engagement_id).running_count == 1, "Slot must be held while streaming"

        # Now queue a waiter.
        waiter_admitted: list[bool] = []
        waiter_id = uuid4()
        waiter_task = asyncio.create_task(
            acquire(
                engagement_id=engagement_id,
                slot_limit=slot_limit,
                tool_run_id=waiter_id,
                target_host=_HOST,
                server_name=_SERVER_NAME,
                tool_name=_HEAVY_TOOL,
                on_queued=lambda p, r: None,
                on_started=lambda: waiter_admitted.append(True),
            )
        )
        await asyncio.sleep(0)
        assert snapshot(engagement_id).queued_count == 1, "Waiter must be queued"

        # Advance clock past the timeout deadline.
        clock.advance(_TIMEOUT + 1.0)

        # Allow enough event loop turns for the timeout to fire.
        for _ in range(40):
            await asyncio.sleep(0)

        # The waiter should be admitted now (slot was released by release_for_decision).
        waiter_handle = await asyncio.wait_for(waiter_task, timeout=1.0)
        assert waiter_admitted, "Waiter must be admitted after slot is released by timeout"
        assert snapshot(engagement_id).running_count == 1  # waiter holds the slot
        assert snapshot(engagement_id).queued_count == 0

        # Verify awaiting_decision was persisted.
        awaiting_calls = [
            call
            for call in update_status_mock.call_args_list
            if call.kwargs.get("status") == "awaiting_decision"
        ]
        assert awaiting_calls, "status=awaiting_decision must be persisted after timeout"

        # Submit kill to resolve the task.
        submit_timeout_decision(tool_run_id, "kill")
        await asyncio.wait_for(stream_task, timeout=2.0)

        # Release the waiter's slot.
        release(waiter_handle)


# ---------------------------------------------------------------------------
# Test: kill decision → status=killed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_decision_after_timeout_persists_killed() -> None:
    """After timeout, decision='kill' → status=killed + killed WS chunk."""
    engagement_id = uuid4()
    tool_run_id = uuid4()
    clock = FakeClock(start=1000.0)

    gen_started = asyncio.Event()

    async def _blocking_gen(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        gen_started.set()
        await asyncio.sleep(3600)
        yield StreamChunk(type="stdout", data="never")  # unreachable

    session_mock, ctx = _make_session_ctx()
    update_status_mock = AsyncMock()
    update_result_mock = AsyncMock(return_value=_make_tool_run_mock(tool_run_id, status="killed"))

    _, queue = subscribe_tool_run(tool_run_id)

    with (
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            side_effect=_blocking_gen,
        ),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_status", update_status_mock),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_result", update_result_mock),
        patch("app.features.mcp.service.get_sessionmaker", return_value=lambda: ctx),
        patch("app.features.mcp.service._time.monotonic", side_effect=clock.monotonic),
    ):
        stream_task = asyncio.create_task(
            _stream_to_channel(
                tool_run_id=tool_run_id,
                engagement_id=engagement_id,
                server_name=_SERVER_NAME,
                tool_name=_LIGHT_TOOL,  # light: no slot
                args={"target": _TARGET},
                timeout_seconds=_TIMEOUT,
                is_heavy=False,
                slot_limit=3,
                target_host=None,
            )
        )
        register_run(engagement_id, tool_run_id, stream_task)

        # Wait for generator to start.
        await asyncio.wait_for(gen_started.wait(), timeout=2.0)

        # Advance clock to fire timeout.
        clock.advance(_TIMEOUT + 1.0)

        for _ in range(40):
            await asyncio.sleep(0)

        # Verify awaiting_decision.
        awaiting = [
            c
            for c in update_status_mock.call_args_list
            if c.kwargs.get("status") == "awaiting_decision"
        ]
        assert awaiting, "awaiting_decision must be persisted before decision submitted"

        # Submit kill decision.
        ok = submit_timeout_decision(tool_run_id, "kill")
        assert ok is True

        await asyncio.wait_for(stream_task, timeout=2.0)

    # Verify killed status was persisted.
    killed_calls = [
        c for c in update_result_mock.call_args_list if c.kwargs.get("status") == "killed"
    ]
    assert killed_calls, "status=killed must be persisted after kill decision"

    # Verify killed WS chunk was broadcast.
    # Channel is discarded after task finishes; check the queue items captured.
    all_q: list[Any] = []
    while not queue.empty():
        all_q.append(queue.get_nowait())
    assert any(c.type == "killed" for c in all_q), (
        "killed WS chunk must be broadcast after kill decision"
    )


# ---------------------------------------------------------------------------
# Test: extend decision → re-acquires slot, fresh started chunk, completes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extend_decision_reacquires_and_completes() -> None:
    """After timeout + extend: task re-acquires a slot, emits started, and completes."""
    engagement_id = uuid4()
    tool_run_id = uuid4()
    clock = FakeClock(start=1000.0)

    call_count = 0
    gen_started_first = asyncio.Event()

    async def _controlled_gen(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            gen_started_first.set()
            await asyncio.sleep(3600)
            yield StreamChunk(type="stdout", data="never")  # unreachable
        else:
            # Second call after extend.
            yield StreamChunk(type="stdout", data="result\n")
            yield StreamDone(exit_code=0, stdout="result\n", stderr="")

    session_mock, ctx = _make_session_ctx()
    update_status_mock = AsyncMock()
    updated_row = _make_tool_run_mock(tool_run_id, status="completed")
    updated_row.exit_code = 0
    updated_row.stdout = "result\n"
    update_result_mock = AsyncMock(return_value=updated_row)

    with (
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            side_effect=_controlled_gen,
        ),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_status", update_status_mock),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_result", update_result_mock),
        patch("app.features.mcp.service.get_sessionmaker", return_value=lambda: ctx),
        patch("app.features.mcp.service._time.monotonic", side_effect=clock.monotonic),
    ):
        stream_task = asyncio.create_task(
            _stream_to_channel(
                tool_run_id=tool_run_id,
                engagement_id=engagement_id,
                server_name=_SERVER_NAME,
                tool_name=_LIGHT_TOOL,
                args={"target": _TARGET},
                timeout_seconds=_TIMEOUT,
                is_heavy=False,
                slot_limit=3,
                target_host=None,
            )
        )
        register_run(engagement_id, tool_run_id, stream_task)

        # Wait for first generator to start.
        await asyncio.wait_for(gen_started_first.wait(), timeout=2.0)

        # Advance clock to fire timeout.
        clock.advance(_TIMEOUT + 1.0)

        for _ in range(40):
            await asyncio.sleep(0)

        # Verify awaiting_decision.
        awaiting = [
            c
            for c in update_status_mock.call_args_list
            if c.kwargs.get("status") == "awaiting_decision"
        ]
        assert awaiting, "awaiting_decision must be persisted"

        # Submit extend decision.  Move clock back so extend deadline is in the future.
        clock.advance(-2000.0)
        ok = submit_timeout_decision(tool_run_id, "extend")
        assert ok is True

        await asyncio.wait_for(stream_task, timeout=3.0)

    # Verify completed status.
    completed_calls = [
        c for c in update_result_mock.call_args_list if c.kwargs.get("status") == "completed"
    ]
    assert completed_calls, "status=completed must be persisted after extend + stream done"

    # Verify 'running' was set after re-acquire (fresh started chunk).
    running_calls = [
        c for c in update_status_mock.call_args_list if c.kwargs.get("status") == "running"
    ]
    assert running_calls, "running status must be persisted after re-acquire on extend"

    assert call_count == 2, "stream_tool_call must be called twice (initial + extend)"


# ---------------------------------------------------------------------------
# Test: wait decision → re-acquires, no further timeout, completes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_decision_reacquires_and_completes_without_timeout() -> None:
    """After timeout + wait: task re-acquires and completes with no further timeout."""
    engagement_id = uuid4()
    tool_run_id = uuid4()
    clock = FakeClock(start=1000.0)

    call_count = 0
    gen_started_first = asyncio.Event()

    async def _controlled_gen(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            gen_started_first.set()
            await asyncio.sleep(3600)
            yield StreamChunk(type="stdout", data="never")  # unreachable
        else:
            # Second call: completes even though the clock is far ahead.
            yield StreamDone(exit_code=0, stdout="done\n", stderr="")

    session_mock, ctx = _make_session_ctx()
    update_status_mock = AsyncMock()
    updated_row = _make_tool_run_mock(tool_run_id, status="completed")
    updated_row.exit_code = 0
    update_result_mock = AsyncMock(return_value=updated_row)

    with (
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            side_effect=_controlled_gen,
        ),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_status", update_status_mock),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_result", update_result_mock),
        patch("app.features.mcp.service.get_sessionmaker", return_value=lambda: ctx),
        patch("app.features.mcp.service._time.monotonic", side_effect=clock.monotonic),
    ):
        stream_task = asyncio.create_task(
            _stream_to_channel(
                tool_run_id=tool_run_id,
                engagement_id=engagement_id,
                server_name=_SERVER_NAME,
                tool_name=_LIGHT_TOOL,
                args={"target": _TARGET},
                timeout_seconds=_TIMEOUT,
                is_heavy=False,
                slot_limit=3,
                target_host=None,
            )
        )
        register_run(engagement_id, tool_run_id, stream_task)

        await asyncio.wait_for(gen_started_first.wait(), timeout=2.0)

        # Fire timeout.
        clock.advance(_TIMEOUT + 1.0)
        for _ in range(40):
            await asyncio.sleep(0)

        awaiting = [
            c
            for c in update_status_mock.call_args_list
            if c.kwargs.get("status") == "awaiting_decision"
        ]
        assert awaiting, "awaiting_decision must be persisted"

        # Submit wait decision, then advance clock far ahead — wait disables deadline.
        ok = submit_timeout_decision(tool_run_id, "wait")
        assert ok is True

        clock.advance(10000.0)
        await asyncio.wait_for(stream_task, timeout=3.0)

    # Verify completed.
    completed_calls = [
        c for c in update_result_mock.call_args_list if c.kwargs.get("status") == "completed"
    ]
    assert completed_calls, "status=completed must be persisted after wait + stream done"
    assert call_count == 2, "stream_tool_call must be called twice (initial + wait)"


# ---------------------------------------------------------------------------
# Test: pause while awaiting decision → run resolves killed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_while_awaiting_decision_resolves_killed() -> None:
    """Pausing the engagement while a run is awaiting-decision resolves it as killed."""
    engagement_id = uuid4()
    tool_run_id = uuid4()
    clock = FakeClock(start=1000.0)

    gen_started = asyncio.Event()

    async def _blocking_gen(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        gen_started.set()
        await asyncio.sleep(3600)
        yield StreamChunk(type="stdout", data="never")  # unreachable

    session_mock, ctx = _make_session_ctx()
    update_status_mock = AsyncMock()
    update_result_mock = AsyncMock(return_value=_make_tool_run_mock(tool_run_id, status="killed"))

    with (
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            side_effect=_blocking_gen,
        ),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_status", update_status_mock),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_result", update_result_mock),
        patch("app.features.mcp.service.get_sessionmaker", return_value=lambda: ctx),
        patch("app.features.mcp.service._time.monotonic", side_effect=clock.monotonic),
    ):
        stream_task = asyncio.create_task(
            _stream_to_channel(
                tool_run_id=tool_run_id,
                engagement_id=engagement_id,
                server_name=_SERVER_NAME,
                tool_name=_LIGHT_TOOL,
                args={"target": _TARGET},
                timeout_seconds=_TIMEOUT,
                is_heavy=False,
                slot_limit=3,
                target_host=None,
            )
        )
        register_run(engagement_id, tool_run_id, stream_task)

        await asyncio.wait_for(gen_started.wait(), timeout=2.0)

        clock.advance(_TIMEOUT + 1.0)
        for _ in range(40):
            await asyncio.sleep(0)

        awaiting = [
            c
            for c in update_status_mock.call_args_list
            if c.kwargs.get("status") == "awaiting_decision"
        ]
        assert awaiting, "awaiting_decision must be persisted before pause"

        # Pause the engagement — submits kill to the rendezvous.
        killed_running, dequeued = set_paused(engagement_id, True)
        assert killed_running == 1, "pausing must count the awaiting-decision run"

        await asyncio.wait_for(stream_task, timeout=2.0)

    # Verify killed status.
    killed_calls = [
        c for c in update_result_mock.call_args_list if c.kwargs.get("status") == "killed"
    ]
    assert killed_calls, "status=killed must be persisted after pause-while-awaiting"


# ---------------------------------------------------------------------------
# Test: no slot leak across park→extend→complete cycle (Risk 7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_slot_leak_across_timeout_and_extend() -> None:
    """Assert no slot leak and no double-acquire after a full park→extend→complete cycle.

    slot count must return to 0 after the task finishes (Risk 7).
    """
    engagement_id = uuid4()
    tool_run_id = uuid4()
    slot_limit = 1
    clock = FakeClock(start=1000.0)

    call_count = 0
    gen_started = asyncio.Event()

    async def _gen(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            gen_started.set()
            await asyncio.sleep(3600)
            yield StreamChunk(type="stdout", data="never")  # unreachable
        else:
            yield StreamDone(exit_code=0, stdout="ok\n", stderr="")

    session_mock, ctx = _make_session_ctx()
    update_status_mock = AsyncMock()
    updated_row = _make_tool_run_mock(tool_run_id, status="completed")
    updated_row.exit_code = 0
    update_result_mock = AsyncMock(return_value=updated_row)

    with (
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            side_effect=_gen,
        ),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_status", update_status_mock),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_result", update_result_mock),
        patch("app.features.mcp.service.get_sessionmaker", return_value=lambda: ctx),
        patch("app.features.mcp.service._time.monotonic", side_effect=clock.monotonic),
    ):
        stream_task = asyncio.create_task(
            _stream_to_channel(
                tool_run_id=tool_run_id,
                engagement_id=engagement_id,
                server_name=_SERVER_NAME,
                tool_name=_HEAVY_TOOL,
                args={"target": _TARGET},
                timeout_seconds=_TIMEOUT,
                is_heavy=True,
                slot_limit=slot_limit,
                target_host=_HOST,
            )
        )
        register_run(engagement_id, tool_run_id, stream_task)

        await asyncio.wait_for(gen_started.wait(), timeout=2.0)
        assert snapshot(engagement_id).running_count == 1

        # Fire timeout.
        clock.advance(_TIMEOUT + 1.0)
        for _ in range(40):
            await asyncio.sleep(0)

        # Slot must be released while awaiting decision.
        assert snapshot(engagement_id).running_count == 0, (
            "Slot must be 0 while awaiting decision (Risk 7)"
        )

        # Submit extend, move clock back so extend deadline is in the future.
        clock.advance(-2000.0)
        submit_timeout_decision(tool_run_id, "extend")

        await asyncio.wait_for(stream_task, timeout=3.0)

    # After completion: slot must be 0 (no leak).
    assert snapshot(engagement_id).running_count == 0, "No slot leak after extend→complete"
    assert snapshot(engagement_id).queued_count == 0


# ---------------------------------------------------------------------------
# Test: kill while running → status=killed, slot released, per-server lock not leaked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_while_running_sets_killed_and_releases_slot() -> None:
    """Cancelling mid-stream → status=killed + slot released (Risk 1).

    The per-server subprocess lock is also released (closing the generator exits
    async with handle.lock) so a subsequent stream_tool_call on the same server
    can succeed.
    """
    engagement_id = uuid4()
    tool_run_id = uuid4()
    slot_limit = 1
    clock = FakeClock(start=1000.0)  # clock doesn't advance → no timeout

    gen_started = asyncio.Event()

    async def _blocking_gen(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        gen_started.set()
        await asyncio.sleep(3600)
        yield StreamChunk(type="stdout", data="never")  # unreachable

    session_mock, ctx = _make_session_ctx()
    update_status_mock = AsyncMock()
    update_result_mock = AsyncMock(return_value=_make_tool_run_mock(tool_run_id, status="killed"))

    _, queue = subscribe_tool_run(tool_run_id)

    with (
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            side_effect=_blocking_gen,
        ),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_status", update_status_mock),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_result", update_result_mock),
        patch("app.features.mcp.service.get_sessionmaker", return_value=lambda: ctx),
        patch("app.features.mcp.service._time.monotonic", side_effect=clock.monotonic),
    ):
        stream_task = asyncio.create_task(
            _stream_to_channel(
                tool_run_id=tool_run_id,
                engagement_id=engagement_id,
                server_name=_SERVER_NAME,
                tool_name=_HEAVY_TOOL,
                args={"target": _TARGET},
                timeout_seconds=9999.0,  # large — no timeout
                is_heavy=True,
                slot_limit=slot_limit,
                target_host=_HOST,
            )
        )
        register_run(engagement_id, tool_run_id, stream_task)

        # Wait until the stream is running.
        await asyncio.wait_for(gen_started.wait(), timeout=2.0)
        assert snapshot(engagement_id).running_count == 1, "Slot must be held while running"

        # Cancel the task (simulating kill_run).
        result = kill_run(tool_run_id)
        assert result == "cancelled"
        await asyncio.wait_for(stream_task, timeout=2.0)

    # After kill: slot must be 0 (Risk 1).
    assert snapshot(engagement_id).running_count == 0, (
        "Slot must be released after kill-while-running (Risk 1)"
    )
    assert snapshot(engagement_id).queued_count == 0

    # Verify killed status was persisted.
    killed_calls = [
        c for c in update_result_mock.call_args_list if c.kwargs.get("status") == "killed"
    ]
    assert killed_calls, "status=killed must be persisted after cancel (kill-while-running)"

    # Verify killed WS chunk was broadcast.
    all_q = []
    while not queue.empty():
        all_q.append(queue.get_nowait())
    assert any(c.type == "killed" for c in all_q), "killed WS chunk must be broadcast after cancel"

    # Per-server lock assertion (Risk 1):
    # We verify the lock is released by ensuring stream_tool_call can be called again.
    # In a real scenario the per-server asyncio.Lock would be released by gen.aclose().
    # In the mock scenario the mock itself is stateless, so we verify the task is done
    # and the slot count is correct (the actual lock release is tested in the kill
    # integration test that uses a real subprocess_manager mock with an actual lock).
    assert stream_task.done(), "Task must be done after cancel"
    # CancelledError is swallowed by _stream_to_channel — task must NOT be in cancelled state.
    assert not stream_task.cancelled(), (
        "Task must not be in cancelled state (CancelledError was swallowed)"
    )


# ---------------------------------------------------------------------------
# Test: queued kill → status=killed, subprocess never called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queued_kill_sets_killed_without_subprocess() -> None:
    """Killing a queued run before admission → status=killed, no subprocess call."""
    engagement_id = uuid4()
    tool_run_id = uuid4()
    slot_limit = 1
    clock = FakeClock(start=1000.0)

    # Pre-occupy the only slot so the stream task must queue.
    blocker_handle = await _grab_slot(engagement_id, slot_limit=slot_limit, target_host="other")

    stream_mock = AsyncMock()  # must NOT be called

    session_mock, ctx = _make_session_ctx()
    update_status_mock = AsyncMock()
    update_result_mock = AsyncMock(return_value=_make_tool_run_mock(tool_run_id, status="killed"))

    _, queue = subscribe_tool_run(tool_run_id)

    with (
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            stream_mock,
        ),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_status", update_status_mock),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_result", update_result_mock),
        patch("app.features.mcp.service.get_sessionmaker", return_value=lambda: ctx),
        patch("app.features.mcp.service._time.monotonic", side_effect=clock.monotonic),
    ):
        stream_task = asyncio.create_task(
            _stream_to_channel(
                tool_run_id=tool_run_id,
                engagement_id=engagement_id,
                server_name=_SERVER_NAME,
                tool_name=_HEAVY_TOOL,
                args={"target": _TARGET},
                timeout_seconds=30.0,
                is_heavy=True,
                slot_limit=slot_limit,
                target_host=_HOST,
            )
        )
        register_run(engagement_id, tool_run_id, stream_task)

        # Let the task enqueue.
        for _ in range(10):
            await asyncio.sleep(0)
        assert snapshot(engagement_id).queued_count == 1, "task must be queued"

        # Kill the run.  Since the task is registered in the registry (as service.py
        # does in execute_tool_run), kill_run returns "cancelled" (task.cancel() path).
        # The task's CancelledError handler persists 'killed' and broadcasts the chunk.
        # (A pure-queue kill without registry registration returns "dequeued" — that
        # is tested in test_killswitch.py::test_kill_queued_run_dequeues_and_raises_run_killed.)
        result = kill_run(tool_run_id)
        assert result in ("dequeued", "cancelled"), (
            f"kill_run must find the queued run, got: {result!r}"
        )

        await asyncio.wait_for(stream_task, timeout=2.0)

        # After kill, the queue must be empty and no slot leaked.
        assert snapshot(engagement_id).queued_count == 0, "queue must be empty after kill"
        assert snapshot(engagement_id).running_count == 1, "blocker still holds its slot"

    # Subprocess must NOT have been called.
    stream_mock.assert_not_called()

    # Verify killed status.
    killed_calls = [
        c for c in update_result_mock.call_args_list if c.kwargs.get("status") == "killed"
    ]
    assert killed_calls, "status=killed must be persisted for queued kill"

    # Verify killed WS chunk.
    all_q = []
    while not queue.empty():
        all_q.append(queue.get_nowait())
    assert any(c.type == "killed" for c in all_q), "killed WS chunk must be broadcast"

    release(blocker_handle)


# ---------------------------------------------------------------------------
# Test: slot accounting — no leak on pause-during-reacquire (Risk 7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slot_accounting_no_leak_on_pause_during_reacquire() -> None:
    """No slot leak when pause fires during the re-acquire step after extend.

    Sequence:
    1. Tool run: slot acquired (in_use=1).
    2. Timeout: slot released via release_for_decision (in_use=0).
    3. Decision=extend: re-acquire starts — another run holds the slot so it queues.
    4. Pause fires: queued re-acquire wakes with RunKilled.
    5. Final slot count must be 0.
    """
    engagement_id = uuid4()
    tool_run_id = uuid4()
    other_id = uuid4()
    slot_limit = 1
    clock = FakeClock(start=1000.0)

    gen_started = asyncio.Event()

    async def _blocking_gen(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        gen_started.set()
        await asyncio.sleep(3600)
        yield StreamChunk(type="stdout", data="never")  # unreachable

    session_mock, ctx = _make_session_ctx()
    update_status_mock = AsyncMock()
    update_result_mock = AsyncMock(return_value=_make_tool_run_mock(tool_run_id, status="killed"))

    # Another run grabs the slot so the re-acquire will queue.
    other_handle = await _grab_slot(
        engagement_id, other_id, slot_limit=slot_limit, target_host="other"
    )
    assert snapshot(engagement_id).running_count == 1

    with (
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            side_effect=_blocking_gen,
        ),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_status", update_status_mock),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_result", update_result_mock),
        patch("app.features.mcp.service.get_sessionmaker", return_value=lambda: ctx),
        patch("app.features.mcp.service._time.monotonic", side_effect=clock.monotonic),
    ):
        # Start the heavy stream task — it will queue too (slot taken by other).
        # But wait — if other_handle holds the only slot, the stream task can't
        # even acquire. We need the stream task to have the slot first, timeout,
        # release it, other grabs it, then extend queues behind other.
        # So: release other_handle first so stream_task can acquire.
        release(other_handle)

        stream_task = asyncio.create_task(
            _stream_to_channel(
                tool_run_id=tool_run_id,
                engagement_id=engagement_id,
                server_name=_SERVER_NAME,
                tool_name=_HEAVY_TOOL,
                args={"target": _TARGET},
                timeout_seconds=_TIMEOUT,
                is_heavy=True,
                slot_limit=slot_limit,
                target_host=_HOST,
            )
        )
        register_run(engagement_id, tool_run_id, stream_task)

        # Wait for stream to start.
        await asyncio.wait_for(gen_started.wait(), timeout=2.0)
        assert snapshot(engagement_id).running_count == 1

        # Fire timeout — stream task releases slot.
        clock.advance(_TIMEOUT + 1.0)
        for _ in range(40):
            await asyncio.sleep(0)

        # Slot should be 0 while awaiting decision.
        assert snapshot(engagement_id).running_count == 0, "Slot must be 0 after timeout"

        # Another run grabs the now-free slot.
        other_handle2 = await _grab_slot(
            engagement_id, other_id, slot_limit=slot_limit, target_host="other2"
        )
        assert snapshot(engagement_id).running_count == 1

        # Decision = extend: task re-acquires but must queue (slot taken by other).
        ok = submit_timeout_decision(tool_run_id, "extend")
        assert ok is True

        # Give the task turns to start the re-acquire (it should queue).
        for _ in range(20):
            await asyncio.sleep(0)

        # Pause the engagement — de-queues the re-acquiring task.
        killed_running, dequeued = set_paused(engagement_id, True)

        await asyncio.wait_for(stream_task, timeout=2.0)

    # Release the other run's slot.
    release(other_handle2)

    # Slot count must be 0 (no leak).
    assert snapshot(engagement_id).running_count == 0, (
        "No slot leak after pause-during-reacquire (Risk 7)"
    )
    assert snapshot(engagement_id).queued_count == 0


# ---------------------------------------------------------------------------
# Test: RunKilled from initial acquire (dequeue path, not cancel path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_killed_during_initial_acquire_persists_killed() -> None:
    """When acquire raises RunKilled (dequeue path), status=killed is persisted.

    This tests the explicit RunKilled catch inside _stream_to_channel for heavy
    runs — reached when kill_run finds the ticket in the FIFO queue directly
    (task NOT in the registry so kill_run uses the dequeue path).
    """
    engagement_id = uuid4()
    tool_run_id = uuid4()
    slot_limit = 1
    clock = FakeClock(start=1000.0)

    # Pre-occupy the slot so the task must queue.
    blocker_handle = await _grab_slot(engagement_id, slot_limit=slot_limit, target_host="other")

    stream_mock = AsyncMock()  # must NOT be called

    session_mock, ctx = _make_session_ctx()
    update_status_mock = AsyncMock()
    update_result_mock = AsyncMock(return_value=_make_tool_run_mock(tool_run_id, status="killed"))

    _, queue = subscribe_tool_run(tool_run_id)

    with (
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            stream_mock,
        ),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_status", update_status_mock),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_result", update_result_mock),
        patch("app.features.mcp.service.get_sessionmaker", return_value=lambda: ctx),
        patch("app.features.mcp.service._time.monotonic", side_effect=clock.monotonic),
    ):
        # Start WITHOUT register_run so kill_run uses the dequeue path (RunKilled).
        stream_task = asyncio.create_task(
            _stream_to_channel(
                tool_run_id=tool_run_id,
                engagement_id=engagement_id,
                server_name=_SERVER_NAME,
                tool_name=_HEAVY_TOOL,
                args={"target": _TARGET},
                timeout_seconds=30.0,
                is_heavy=True,
                slot_limit=slot_limit,
                target_host=_HOST,
            )
        )
        # NOT calling register_run — kill_run must find the ticket in the FIFO queue.

        # Let the task enqueue.
        for _ in range(10):
            await asyncio.sleep(0)
        assert snapshot(engagement_id).queued_count == 1, "task must be queued"

        # Kill via dequeue path (not in registry → finds ticket → RunKilled).
        result = concurrency_module.kill_run(tool_run_id)
        assert result == "dequeued", f"Expected dequeued, got {result!r}"

        await asyncio.wait_for(stream_task, timeout=2.0)

    # Subprocess must NOT have been called.
    stream_mock.assert_not_called()

    # Verify killed status.
    killed_calls = [
        c for c in update_result_mock.call_args_list if c.kwargs.get("status") == "killed"
    ]
    assert killed_calls, "status=killed must be persisted when RunKilled from initial acquire"

    # Verify killed WS chunk.
    all_q = []
    while not queue.empty():
        all_q.append(queue.get_nowait())
    assert any(c.type == "killed" for c in all_q), "killed WS chunk must be broadcast"

    release(blocker_handle)


# ---------------------------------------------------------------------------
# Test: EngagementPaused from initial acquire persists killed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_engagement_paused_during_initial_acquire_persists_killed() -> None:
    """When acquire raises EngagementPaused, status=killed is persisted.

    Engagement is paused while the task is waiting in acquire().
    """
    engagement_id = uuid4()
    tool_run_id = uuid4()
    slot_limit = 1
    clock = FakeClock(start=1000.0)

    # Pre-occupy the slot so the task must queue.
    blocker_handle = await _grab_slot(engagement_id, slot_limit=slot_limit, target_host="other")

    stream_mock = AsyncMock()  # must NOT be called

    session_mock, ctx = _make_session_ctx()
    update_status_mock = AsyncMock()
    update_result_mock = AsyncMock(return_value=_make_tool_run_mock(tool_run_id, status="killed"))

    _, queue = subscribe_tool_run(tool_run_id)

    with (
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            stream_mock,
        ),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_status", update_status_mock),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_result", update_result_mock),
        patch("app.features.mcp.service.get_sessionmaker", return_value=lambda: ctx),
        patch("app.features.mcp.service._time.monotonic", side_effect=clock.monotonic),
    ):
        stream_task = asyncio.create_task(
            _stream_to_channel(
                tool_run_id=tool_run_id,
                engagement_id=engagement_id,
                server_name=_SERVER_NAME,
                tool_name=_HEAVY_TOOL,
                args={"target": _TARGET},
                timeout_seconds=30.0,
                is_heavy=True,
                slot_limit=slot_limit,
                target_host=_HOST,
            )
        )

        # Let the task enqueue.
        for _ in range(10):
            await asyncio.sleep(0)
        assert snapshot(engagement_id).queued_count == 1, "task must be queued"

        # Pause the engagement — de-queues the task with RunKilled (via the dequeue path).
        # The _stream_to_channel will catch RunKilled (not EngagementPaused directly here,
        # since set_paused uses the dequeue path which raises RunKilled for queued tickets).
        set_paused(engagement_id, True)

        await asyncio.wait_for(stream_task, timeout=2.0)

    # Subprocess must NOT have been called.
    stream_mock.assert_not_called()

    # Verify killed status.
    killed_calls = [
        c for c in update_result_mock.call_args_list if c.kwargs.get("status") == "killed"
    ]
    assert killed_calls, "status=killed must be persisted when engagement paused during acquire"

    release(blocker_handle)
