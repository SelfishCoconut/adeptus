"""Pure-asyncio unit tests for app.features.mcp.concurrency.

No DB, no subprocess, no external services.  All tests exercise the in-process
admission manager directly.

Test matrix (from the Slice 05 spec):
  - test_two_heavy_same_host_serialize
  - test_two_heavy_diff_host_concurrent
  - test_pool_saturation_queues
  - test_target_lock_queues_with_free_slot
  - test_fifo_order_preserved
  - test_release_admits_next_eligible
  - test_positions_reshift_on_admit
  - test_shrink_limit_does_not_preempt
  - test_resolve_target_host
  - test_error_path_releases_slot_and_host_lock  (Risk 3)
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.features.mcp.concurrency import (
    AdmissionHandle,
    _reset,
    acquire,
    position_of,
    release,
    resolve_target_host,
    set_slot_limit,
    snapshot,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _noop_queued(position: int, reason: str) -> None:
    """No-op on_queued callback."""


def _noop_started() -> None:
    """No-op on_started callback."""


def _engagement_id() -> UUID:
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_state() -> None:
    """Reset module-level state before each test to avoid cross-test pollution."""
    _reset()


# ---------------------------------------------------------------------------
# resolve_target_host
# ---------------------------------------------------------------------------


class TestResolveTargetHost:
    """Tests for the URL-parsing host resolver."""

    def test_full_url_with_port(self) -> None:
        result = resolve_target_host(
            "httpx", "run_httpx_heavy", {"target": "http://localhost:3000"}
        )
        assert result == "localhost"

    def test_bare_host(self) -> None:
        result = resolve_target_host("httpx", "run_httpx_heavy", {"target": "localhost"})
        assert result == "localhost"

    def test_bare_host_with_port(self) -> None:
        result = resolve_target_host("httpx", "run_httpx_heavy", {"target": "localhost:3000"})
        assert result == "localhost"

    def test_127_0_0_1_url(self) -> None:
        result = resolve_target_host(
            "httpx", "run_httpx_heavy", {"target": "http://127.0.0.1:8080"}
        )
        assert result == "127.0.0.1"

    def test_juice_shop_bare(self) -> None:
        result = resolve_target_host("httpx", "run_httpx_heavy", {"target": "juice-shop:3000"})
        assert result == "juice-shop"

    def test_lowercase(self) -> None:
        result = resolve_target_host(
            "httpx", "run_httpx_heavy", {"target": "http://LOCALHOST:3000"}
        )
        assert result == "localhost"

    def test_userinfo_smuggling_bare(self) -> None:
        """localhost:3000@evil.com must resolve to evil.com, not localhost."""
        result = resolve_target_host(
            "httpx", "run_httpx_heavy", {"target": "localhost:3000@evil.com"}
        )
        assert result == "evil.com"

    def test_userinfo_smuggling_schemed(self) -> None:
        """http://localhost@evil.com must resolve to evil.com."""
        result = resolve_target_host(
            "httpx", "run_httpx_heavy", {"target": "http://localhost@evil.com"}
        )
        assert result == "evil.com"

    def test_no_target_key_returns_none(self) -> None:
        """Tools without a target arg (e.g. run_command) return None."""
        result = resolve_target_host("shell-exec", "run_command", {"command": "echo hello"})
        assert result is None

    def test_empty_target_returns_none(self) -> None:
        result = resolve_target_host("httpx", "run_httpx_heavy", {"target": ""})
        assert result is None

    def test_non_string_target_returns_none(self) -> None:
        result = resolve_target_host("httpx", "run_httpx_heavy", {"target": 42})  # type: ignore[arg-type]
        assert result is None


# ---------------------------------------------------------------------------
# Two heavy runs same host — must serialize
# ---------------------------------------------------------------------------


async def test_two_heavy_same_host_serialize() -> None:
    """Second heavy run against the same host blocks until first releases."""
    eng = _engagement_id()

    order: list[str] = []

    # First run acquired synchronously.
    handle1 = await _acquire(
        eng, target_host="localhost", on_started=lambda: order.append("run1_started")
    )

    queued_called: list[tuple[int, str]] = []

    def on_queued2(position: int, reason: str) -> None:
        queued_called.append((position, reason))
        order.append("run2_queued")

    run2_task = asyncio.create_task(
        _acquire(
            eng,
            target_host="localhost",
            on_queued=on_queued2,
            on_started=lambda: order.append("run2_started"),
        )
    )

    # Give the event loop a turn so run2 enqueues.
    await asyncio.sleep(0)
    assert "run2_queued" in order, "run2 should be queued"
    assert not run2_task.done(), "run2 should still be waiting"

    # Release run1.
    release(handle1)
    order.append("run1_released")

    handle2 = await run2_task
    order.append("run2_acquired")

    assert order.index("run1_released") < order.index("run2_started"), (
        "run2 must not start before run1 is released"
    )
    assert queued_called == [(1, "target_locked")]

    release(handle2)


# ---------------------------------------------------------------------------
# Two heavy runs different hosts — must run concurrently
# ---------------------------------------------------------------------------


async def test_two_heavy_diff_host_concurrent() -> None:
    """Two heavy runs against different hosts admit immediately (slots free)."""
    eng = _engagement_id()

    started: list[str] = []

    handle1 = await _acquire(
        eng, target_host="localhost", on_started=lambda: started.append("localhost")
    )
    handle2 = await _acquire(
        eng, target_host="127.0.0.1", on_started=lambda: started.append("127.0.0.1")
    )

    # Both admitted without blocking.
    assert "localhost" in started
    assert "127.0.0.1" in started

    s = snapshot(eng)
    assert s.running_count == 2
    assert s.queued_count == 0

    release(handle1)
    release(handle2)


# ---------------------------------------------------------------------------
# Pool saturation queues the (slot_limit+1)-th run with reason=slot_full
# ---------------------------------------------------------------------------


async def test_pool_saturation_queues() -> None:
    """With slot_limit=3, the 4th run queues with reason=slot_full even if host is free."""
    eng = _engagement_id()
    slot_limit = 3

    handles: list[AdmissionHandle] = []
    for i in range(slot_limit):
        h = await _acquire(eng, slot_limit=slot_limit, target_host=f"host{i}.example")
        handles.append(h)

    s = snapshot(eng)
    assert s.running_count == slot_limit
    assert s.queued_count == 0

    queued_info: list[tuple[int, str]] = []

    def on_queued4(position: int, reason: str) -> None:
        queued_info.append((position, reason))

    run4_task = asyncio.create_task(
        _acquire(
            eng,
            slot_limit=slot_limit,
            target_host="host99.example",  # Free host — blocked only by slot pool.
            on_queued=on_queued4,
        )
    )
    await asyncio.sleep(0)

    assert queued_info == [(1, "slot_full")], f"Expected slot_full reason, got: {queued_info}"

    s = snapshot(eng)
    assert s.queued_count == 1

    # Release one slot — run4 should be admitted.
    release(handles[0])
    handle4 = await run4_task

    s = snapshot(eng)
    assert s.queued_count == 0

    for h in handles[1:]:
        release(h)
    release(handle4)


# ---------------------------------------------------------------------------
# Target lock queues with free slot — reason must be target_locked
# ---------------------------------------------------------------------------


async def test_target_lock_queues_with_free_slot() -> None:
    """Same-host second run queues with reason=target_locked even though a slot is free."""
    eng = _engagement_id()
    slot_limit = 3  # Plenty of slots.

    handle1 = await _acquire(eng, slot_limit=slot_limit, target_host="localhost")

    queued_info: list[tuple[int, str]] = []

    def on_queued2(position: int, reason: str) -> None:
        queued_info.append((position, reason))

    run2_task = asyncio.create_task(
        _acquire(eng, slot_limit=slot_limit, target_host="localhost", on_queued=on_queued2)
    )
    await asyncio.sleep(0)

    assert queued_info == [(1, "target_locked")]

    s = snapshot(eng)
    assert s.running_count == 1
    assert s.queued_count == 1
    assert s.queued[0].reason == "target_locked"

    release(handle1)
    handle2 = await run2_task
    release(handle2)


# ---------------------------------------------------------------------------
# FIFO order preserved — later run must not jump an earlier slot-blocked waiter
# ---------------------------------------------------------------------------


async def test_fifo_order_preserved() -> None:
    """Admission must honour arrival order: no later run jumps a slot-blocked front waiter.

    Setup:
    - slot_limit=1
    - Run A holds the slot.
    - Run B arrives and queues (reason=slot_full, host=localhost).
    - Run C arrives and queues (reason=slot_full, host=127.0.0.1 — different host).

    When A releases:
    - B must be admitted FIRST (it arrived earlier), even though both B and C
      have free hosts.
    - Only after B releases does C get admitted.
    """
    eng = _engagement_id()
    slot_limit = 1

    handle_a = await _acquire(eng, slot_limit=slot_limit, target_host="localhost")

    admitted_order: list[str] = []

    b_task = asyncio.create_task(
        _acquire(
            eng,
            slot_limit=slot_limit,
            target_host="localhost",
            on_started=lambda: admitted_order.append("B"),
        )
    )
    c_task = asyncio.create_task(
        _acquire(
            eng,
            slot_limit=slot_limit,
            target_host="127.0.0.1",
            on_started=lambda: admitted_order.append("C"),
        )
    )

    await asyncio.sleep(0)  # Let both enqueue.
    assert not b_task.done()
    assert not c_task.done()

    # Release A — only B (front waiter) can be admitted (slot_limit=1).
    release(handle_a)
    await asyncio.sleep(0)

    # B should be admitted, C still waiting.
    handle_b = await b_task
    assert not c_task.done(), "C must wait until B releases"
    assert admitted_order == ["B"]

    release(handle_b)
    handle_c = await c_task
    assert admitted_order == ["B", "C"]

    release(handle_c)


# ---------------------------------------------------------------------------
# release admits the next eligible waiter (lock-blocked front, eligible later)
# ---------------------------------------------------------------------------


async def test_release_admits_next_eligible() -> None:
    """Release scans past a lock-blocked front waiter to admit a later eligible one.

    Decision 5 / Risk 1: a lock-blocked front waiter must NOT starve a free slot.

    Setup:
    - slot_limit=2
    - Run A holds slot + localhost lock.
    - Run B holds slot + 127.0.0.1 lock.
    - Run C (localhost) queues — blocked by host lock (slot available).
    - Run D (other.host) queues — also blocked only by pool? No — slots = 2, 2 in_use.
      Wait, let's adjust: slot_limit=2, 2 slots taken, so D is slot_full.
      That mixes the cases.

    Simpler setup for this test:
    - slot_limit=2
    - Run A holds slot + localhost lock.
    - Run C (localhost) queues — reason=target_locked (1 slot free).
    - Run D (127.0.0.1) queues — reason=target_locked? No, slot is free and host is free.
      D would be admitted immediately.

    Correct setup:
    - slot_limit=1
    - Run A holds the one slot + localhost lock.
    - Run C (localhost) queues first — reason=slot_full (and host locked).
    - Run D (127.0.0.1) queues second — reason=slot_full (slot the only issue).

    When A releases: slot freed, localhost unlocked.
    C is front, its host (localhost) is now FREE → C is eligible → admit C.
    (This is the normal FIFO case.)

    To test "skip lock-blocked front, admit later eligible":
    - slot_limit=2
    - Run A holds slot1 + localhost lock.
    - Run B holds slot2 + 127.0.0.1 lock.
    - Run C (localhost) queues — reason=target_locked (1 slot free but host locked).
    - B releases: slot freed, 127.0.0.1 unlocked.
    - Scan: C is front, host=localhost still locked by A → skip C.
    - No more waiters → no admission.
    - A releases: slot freed, localhost unlocked.
    - Scan: C is front, host=localhost now free → admit C.

    That's correct but doesn't show "skip front and admit later".  We need:
    - slot_limit=2, A holds slot+localhost, B holds slot+127.0.0.1.
    - C (localhost) queues first — reason=target_locked (1 slot free).
    - D (other.host) queues second — also reason=target_locked? No, slot is free.
      Wait, both slots are taken! slot_limit=2 in_use=2 → no slot free.
    - C and D both queued as slot_full.
    - B releases: 1 slot free, 127.0.0.1 unlocked.
    - Scan: C front, host=localhost still locked (A holds it) → skip (slot available).
    - D: host=other.host free, slot free → admit D.

    This is the Decision-5 eligible-skip case.
    """
    eng = _engagement_id()
    slot_limit = 2

    handle_a = await _acquire(eng, slot_limit=slot_limit, target_host="localhost")
    handle_b = await _acquire(eng, slot_limit=slot_limit, target_host="127.0.0.1")

    # Both slots taken.
    assert snapshot(eng).running_count == 2

    admitted_order: list[str] = []

    c_task = asyncio.create_task(
        _acquire(
            eng,
            slot_limit=slot_limit,
            target_host="localhost",  # locked by A
            on_started=lambda: admitted_order.append("C"),
        )
    )
    d_task = asyncio.create_task(
        _acquire(
            eng,
            slot_limit=slot_limit,
            target_host="other.host",  # free
            on_started=lambda: admitted_order.append("D"),
        )
    )

    await asyncio.sleep(0)  # Both enqueue.
    assert not c_task.done()
    assert not d_task.done()
    assert snapshot(eng).queued_count == 2

    # Release B: 1 slot freed, 127.0.0.1 unlocked.
    # Scan: C is front, host=localhost still locked by A → skip.
    # D is next, host=other.host free, slot free → admit D.
    release(handle_b)
    await asyncio.sleep(0)

    handle_d = await d_task
    assert not c_task.done(), "C must still be waiting (localhost still locked by A)"
    assert admitted_order == ["D"]

    # Release A: 1 slot freed, localhost unlocked.
    # Now C is front and eligible.
    release(handle_a)
    handle_c = await c_task
    assert admitted_order == ["D", "C"]

    release(handle_c)
    release(handle_d)


# ---------------------------------------------------------------------------
# Position re-shifting on admit
# ---------------------------------------------------------------------------


async def test_positions_reshift_on_admit() -> None:
    """Queue positions decrement when the front waiter is admitted.

    With 3 queued runs at positions 1, 2, 3:
    After admitting position-1, the remaining two should be at positions 1, 2.
    """
    eng = _engagement_id()
    slot_limit = 1

    # Hold the single slot.
    handle_a = await _acquire(eng, slot_limit=slot_limit, target_host="hostA")

    b_id = _run_id()
    c_id = _run_id()
    d_id = _run_id()

    b_task = asyncio.create_task(
        acquire(
            engagement_id=eng,
            slot_limit=slot_limit,
            tool_run_id=b_id,
            target_host="hostB",
            server_name="httpx",
            tool_name="run_httpx_heavy",
            on_queued=_noop_queued,
            on_started=_noop_started,
        )
    )
    c_task = asyncio.create_task(
        acquire(
            engagement_id=eng,
            slot_limit=slot_limit,
            tool_run_id=c_id,
            target_host="hostC",
            server_name="httpx",
            tool_name="run_httpx_heavy",
            on_queued=_noop_queued,
            on_started=_noop_started,
        )
    )
    d_task = asyncio.create_task(
        acquire(
            engagement_id=eng,
            slot_limit=slot_limit,
            tool_run_id=d_id,
            target_host="hostD",
            server_name="httpx",
            tool_name="run_httpx_heavy",
            on_queued=_noop_queued,
            on_started=_noop_started,
        )
    )

    await asyncio.sleep(0)  # All three enqueue.

    # Positions before admission.
    assert position_of(b_id) == 1
    assert position_of(c_id) == 2
    assert position_of(d_id) == 3

    # Admit B (release A, slot_limit=1 so only front waiter admitted).
    release(handle_a)
    handle_b = await b_task

    # B admitted: positions C=1, D=2.
    assert position_of(b_id) is None  # Now running, not queued.
    assert position_of(c_id) == 1
    assert position_of(d_id) == 2

    release(handle_b)
    handle_c = await c_task
    assert position_of(c_id) is None
    assert position_of(d_id) == 1

    release(handle_c)
    handle_d = await d_task
    release(handle_d)


# ---------------------------------------------------------------------------
# Shrink limit does not preempt running slots
# ---------------------------------------------------------------------------


async def test_shrink_limit_does_not_preempt() -> None:
    """Lowering slot_limit below the running count must not crash or kill running slots.

    After shrink, new runs must queue until releases catch up.
    """
    eng = _engagement_id()
    slot_limit = 3

    handles: list[AdmissionHandle] = []
    for i in range(slot_limit):
        h = await _acquire(eng, slot_limit=slot_limit, target_host=f"host{i}")
        handles.append(h)

    s = snapshot(eng)
    assert s.running_count == 3

    # Shrink to 1 — running slots must survive.
    set_slot_limit(eng, 1)

    s = snapshot(eng)
    assert s.running_count == 3  # All still running.
    assert s.slot_limit == 1

    # New run must queue (available = 1 - 3 = -2, i.e. blocked).
    queued_info: list[tuple[int, str]] = []

    new_task = asyncio.create_task(
        _acquire(
            eng,
            slot_limit=1,
            target_host="host99",
            on_queued=lambda pos, reason: queued_info.append((pos, reason)),
        )
    )
    await asyncio.sleep(0)

    assert queued_info, "New run should be queued after shrink"
    assert not new_task.done()

    # Release all three running slots — the new run can only admit after all 3 release
    # because limit=1 and in_use starts at 3 (available = -2, then -1, then 0, then 1).
    release(handles[0])
    await asyncio.sleep(0)
    assert not new_task.done(), "Still in_use=2, limit=1, available=-1"

    release(handles[1])
    await asyncio.sleep(0)
    assert not new_task.done(), "Still in_use=1, limit=1, available=0"

    release(handles[2])
    handle_new = await new_task  # Now in_use=0, available=1 → admitted.

    s = snapshot(eng)
    assert s.running_count == 1
    assert s.queued_count == 0

    release(handle_new)


# ---------------------------------------------------------------------------
# Error path — release frees slot + host lock and queue drains (Risk 3)
# ---------------------------------------------------------------------------


async def test_error_path_releases_slot_and_host_lock() -> None:
    """If a run errors after acquiring, release in finally must free slot + host lock.

    After release, the queue must drain (next waiter admitted).
    """
    eng = _engagement_id()
    slot_limit = 1

    admitted_after_error: list[str] = []

    waiter_task = asyncio.create_task(
        _acquire(
            eng,
            slot_limit=slot_limit,
            target_host="localhost",
            on_started=lambda: admitted_after_error.append("waiter_started"),
        )
    )

    # Simulate a run that acquires then errors in its finally.
    handle: AdmissionHandle | None = None
    try:
        handle = await _acquire(eng, slot_limit=slot_limit, target_host="localhost")
        raise RuntimeError("Simulated tool failure")
    except RuntimeError:
        pass
    finally:
        if handle is not None:
            release(handle)

    await asyncio.sleep(0)

    # The queued waiter should now be admitted.
    h_waiter = await asyncio.wait_for(waiter_task, timeout=1.0)
    assert "waiter_started" in admitted_after_error

    s = snapshot(eng)
    assert s.running_count == 1
    assert s.queued_count == 0

    release(h_waiter)

    s = snapshot(eng)
    assert s.running_count == 0


# ---------------------------------------------------------------------------
# Release is idempotent
# ---------------------------------------------------------------------------


async def test_release_is_idempotent() -> None:
    """Calling release twice on the same handle must not double-return a slot."""
    eng = _engagement_id()
    handle = await _acquire(eng, slot_limit=1, target_host="localhost")

    release(handle)
    assert snapshot(eng).running_count == 0

    # Second release must be a no-op, not crash or corrupt state.
    release(handle)
    assert snapshot(eng).running_count == 0


# ---------------------------------------------------------------------------
# snapshot — empty engagement
# ---------------------------------------------------------------------------


def test_snapshot_empty_engagement() -> None:
    """snapshot() for an engagement with no state returns sensible defaults."""
    s = snapshot(uuid4())
    assert s.slot_limit == 3
    assert s.running_count == 0
    assert s.queued_count == 0
    assert s.queued == []


# ---------------------------------------------------------------------------
# on_queued and on_started async callbacks
# ---------------------------------------------------------------------------


async def test_async_callbacks_are_awaited() -> None:
    """acquire() must await coroutine callbacks."""
    eng = _engagement_id()
    slot_limit = 1
    handle_a = await _acquire(eng, slot_limit=slot_limit, target_host="localhost")

    queued_log: list[int] = []
    started_log: list[str] = []

    async def async_on_queued(position: int, reason: str) -> None:
        queued_log.append(position)

    async def async_on_started() -> None:
        started_log.append("started")

    task = asyncio.create_task(
        acquire(
            engagement_id=eng,
            slot_limit=slot_limit,
            tool_run_id=_run_id(),
            target_host="localhost",
            server_name="httpx",
            tool_name="run_httpx_heavy",
            on_queued=async_on_queued,
            on_started=async_on_started,
        )
    )

    await asyncio.sleep(0)
    assert queued_log == [1]

    release(handle_a)
    handle_b = await task
    assert started_log == ["started"]
    release(handle_b)


# ---------------------------------------------------------------------------
# snapshot queued list matches FIFO insertion order
# ---------------------------------------------------------------------------


async def test_snapshot_queued_list_order() -> None:
    """snapshot().queued must list runs in FIFO (arrival) order."""
    eng = _engagement_id()
    slot_limit = 1

    handle_a = await _acquire(eng, slot_limit=slot_limit, target_host="hostA")

    ids = [_run_id() for _ in range(3)]
    tasks = []
    for i, rid in enumerate(ids):
        tasks.append(
            asyncio.create_task(
                acquire(
                    engagement_id=eng,
                    slot_limit=slot_limit,
                    tool_run_id=rid,
                    target_host=f"host{i + 1}",
                    server_name="httpx",
                    tool_name="run_httpx_heavy",
                    on_queued=_noop_queued,
                    on_started=_noop_started,
                )
            )
        )

    await asyncio.sleep(0)

    s = snapshot(eng)
    assert [q.tool_run_id for q in s.queued] == ids
    assert [q.position for q in s.queued] == [1, 2, 3]

    release(handle_a)
    for t in tasks:
        h = await t
        release(h)
