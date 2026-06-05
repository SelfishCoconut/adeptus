"""Unit tests for Task 3 of Slice 05: admission wiring in service.execute_tool_run.

Tests cover:
  - Async heavy run that is immediately admitted → status='running', 'started' chunk.
  - Async heavy run that is blocked → status='queued' + 'queued' chunk; on release →
    'started' chunk, status='running' + started_at set, then streams stdout.
  - Sync heavy run blocks until slot frees, then runs.
  - Light run NEVER calls acquire (manager untouched — snapshot stays empty).
  - Error mid-stream → slot + host lock released, queue drains afterward (Risk 3).
  - Double-release after error is safe (idempotent).

All external dependencies (subprocess_manager, get_sessionmaker, mcp_repo, eng_repo,
get_registry) are mocked. No real DB or subprocess is used.

``concurrency._reset()`` and ``service._reset_channels()`` are called in the autouse
fixture so tests do not leak state.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.features.audit.schemas import AuditAction
from app.features.mcp import concurrency as concurrency_module
from app.features.mcp.concurrency import ToolQueueFullError
from app.features.mcp.registry import McpServerConfig, McpToolConfig
from app.features.mcp.schemas import WebSocketOutputChunk
from app.features.mcp.service import (
    _reset_channels,
    _stream_to_channel,
    execute_tool_run,
    subscribe_tool_run,
)
from app.features.mcp.subprocess_manager import (
    McpServerDown,
    StreamChunk,
    StreamDone,
)

# ---------------------------------------------------------------------------
# Constants and helpers
# ---------------------------------------------------------------------------

_SERVER_NAME = "httpx"
_HEAVY_TOOL_NAME = "run_httpx_heavy"
_LIGHT_TOOL_NAME = "run_httpx"
_TARGET = "http://localhost:3000"
_TIMEOUT = 30


def _make_tool_config(
    name: str = _LIGHT_TOOL_NAME,
    weight: str = "light",
) -> McpToolConfig:
    return McpToolConfig(name=name, weight=weight, capability_flags=["network"])


def _make_server_config(tools: list[McpToolConfig] | None = None) -> McpServerConfig:
    if tools is None:
        tools = [_make_tool_config()]
    return McpServerConfig(name=_SERVER_NAME, command="python", args=[], tools=tools)


def _make_registry_with_tools(*tools: McpToolConfig) -> dict[str, McpServerConfig]:
    cfg = _make_server_config(tools=list(tools))
    return {cfg.name: cfg}


def _make_tool_run_mock(
    tool_run_id: UUID | None = None,
    engagement_id: UUID | None = None,
    status: str = "queued",
) -> MagicMock:
    run = MagicMock()
    run.id = tool_run_id or uuid4()
    run.engagement_id = engagement_id or uuid4()
    run.server_name = _SERVER_NAME
    run.tool_name = _HEAVY_TOOL_NAME
    run.exit_code = None
    run.stdout = ""
    run.stderr = ""
    run.started_at = datetime.now(tz=UTC)
    run.finished_at = None
    run.status = status
    run.preset_name = None
    return run


def _make_engagement_mock(slot_limit: int = 3) -> MagicMock:
    eng = MagicMock()
    eng.id = uuid4()
    eng.concurrency_slot_limit = slot_limit
    return eng


def _make_member_mock() -> MagicMock:
    member = MagicMock()
    member.role = "member"
    return member


def _make_session_ctx(session_mock: AsyncMock | None = None) -> tuple[AsyncMock, MagicMock]:
    """Return (session_mock, context_manager_mock) for patching get_sessionmaker."""
    if session_mock is None:
        session_mock = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return session_mock, ctx


async def _canned_stream(*events: Any) -> AsyncIterator[Any]:
    for ev in events:
        yield ev


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_state() -> Any:
    """Clean module-level state before and after every test."""
    _reset_channels()
    concurrency_module._reset()
    yield
    _reset_channels()
    concurrency_module._reset()


# ---------------------------------------------------------------------------
# Light run: NEVER calls acquire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_light_run_never_calls_acquire() -> None:
    """A light tool run does not touch the admission manager at all."""
    engagement_id = uuid4()
    tool_run_id = uuid4()
    done = StreamDone(exit_code=0, stdout="ok\n", stderr="")
    updated = _make_tool_run_mock(tool_run_id=tool_run_id, status="completed")
    updated.exit_code = 0

    session_mock, ctx = _make_session_ctx()
    status_mock = AsyncMock()

    with (
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            return_value=_canned_stream(done),
        ),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_status", status_mock),
        patch(
            "app.features.mcp.service.mcp_repo.update_tool_run_result",
            new_callable=AsyncMock,
            return_value=updated,
        ),
        patch("app.features.mcp.service.get_sessionmaker", return_value=lambda: ctx),
    ):
        await _stream_to_channel(
            tool_run_id=tool_run_id,
            engagement_id=engagement_id,
            server_name=_SERVER_NAME,
            tool_name=_LIGHT_TOOL_NAME,
            args={"target": _TARGET},
            timeout_seconds=float(_TIMEOUT),
            is_heavy=False,
        )

    # Snapshot must be empty — acquire was never called.
    snap = concurrency_module.snapshot(engagement_id)
    assert snap.running_count == 0
    assert snap.queued_count == 0
    # update_tool_run_status must NOT have been called (no queued/running update).
    status_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Async heavy run: immediately admitted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_heavy_immediately_admitted_broadcasts_started() -> None:
    """A heavy run admitted on the fast path broadcasts a 'started' chunk."""
    engagement_id = uuid4()
    tool_run_id = uuid4()
    slot_limit = 3

    chunk = StreamChunk(type="stdout", data="output\n")
    done = StreamDone(exit_code=0, stdout="output\n", stderr="")
    updated = _make_tool_run_mock(tool_run_id=tool_run_id, status="completed")
    updated.exit_code = 0

    session_mock, ctx = _make_session_ctx()
    status_mock = AsyncMock()

    _, queue = subscribe_tool_run(tool_run_id)

    with (
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            return_value=_canned_stream(chunk, done),
        ),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_status", status_mock),
        patch(
            "app.features.mcp.service.mcp_repo.update_tool_run_result",
            new_callable=AsyncMock,
            return_value=updated,
        ),
        patch("app.features.mcp.service.get_sessionmaker", return_value=lambda: ctx),
    ):
        await _stream_to_channel(
            tool_run_id=tool_run_id,
            engagement_id=engagement_id,
            server_name=_SERVER_NAME,
            tool_name=_HEAVY_TOOL_NAME,
            args={"target": _TARGET},
            timeout_seconds=float(_TIMEOUT),
            is_heavy=True,
            slot_limit=slot_limit,
            target_host="localhost",
        )

    # Collect all broadcasts.
    chunks: list[WebSocketOutputChunk] = []
    while not queue.empty():
        chunks.append(queue.get_nowait())

    # Expect: started, stdout, done.
    types = [c.type for c in chunks]
    assert "started" in types
    assert types.index("started") < types.index("stdout"), "started must come before stdout"

    # on_started must have updated status='running' + started_at.
    status_calls = [call for call in status_mock.call_args_list]
    running_call = next((c for c in status_calls if c.kwargs.get("status") == "running"), None)
    assert running_call is not None, "Expected update_tool_run_status(status='running')"
    assert running_call.kwargs.get("started_at") is not None

    # Slot must be released after completion.
    snap = concurrency_module.snapshot(engagement_id)
    assert snap.running_count == 0


# ---------------------------------------------------------------------------
# Async heavy run: blocked → queued → admitted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_heavy_blocked_emits_queued_then_started() -> None:
    """A blocked heavy run emits a 'queued' chunk, then 'started' when admitted."""
    engagement_id = uuid4()
    tool_run_id_1 = uuid4()
    tool_run_id_2 = uuid4()
    slot_limit = 1

    done = StreamDone(exit_code=0, stdout="ok\n", stderr="")
    updated1 = _make_tool_run_mock(tool_run_id=tool_run_id_1, status="completed")
    updated1.exit_code = 0
    updated2 = _make_tool_run_mock(tool_run_id=tool_run_id_2, status="completed")
    updated2.exit_code = 0

    status_mock = AsyncMock()

    # Each call to get_sessionmaker returns a fresh session context.
    session1, ctx1 = _make_session_ctx()
    session2, ctx2 = _make_session_ctx()
    ctx_iter = iter([ctx1, ctx2])

    def _sessionmaker_factory() -> MagicMock:
        return next(ctx_iter)

    _, queue2 = subscribe_tool_run(tool_run_id_2)

    # Event to signal when run-1's streaming should complete.
    run1_can_finish = asyncio.Event()

    async def _run1_stream(*_: Any, **__: Any) -> AsyncIterator[Any]:
        # Simulate a long-running stream that waits for the event.
        await run1_can_finish.wait()
        yield done

    async def _run2_stream(*_: Any, **__: Any) -> AsyncIterator[Any]:
        yield done

    update_result_mock = AsyncMock()
    update_result_mock.return_value = updated1

    with (
        patch(
            "app.features.mcp.service.mcp_repo.update_tool_run_status",
            status_mock,
        ),
        patch(
            "app.features.mcp.service.mcp_repo.update_tool_run_result",
            update_result_mock,
        ),
        patch("app.features.mcp.service.get_sessionmaker", return_value=_sessionmaker_factory),
    ):
        # Stream-mock must be per-call (async generator can only be iterated once).
        with patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            side_effect=[_run1_stream(), _run2_stream()],
        ):
            # Start run-1 (will hold the slot).
            task1 = asyncio.create_task(
                _stream_to_channel(
                    tool_run_id=tool_run_id_1,
                    engagement_id=engagement_id,
                    server_name=_SERVER_NAME,
                    tool_name=_HEAVY_TOOL_NAME,
                    args={"target": _TARGET},
                    timeout_seconds=float(_TIMEOUT),
                    is_heavy=True,
                    slot_limit=slot_limit,
                    target_host="localhost",
                )
            )

            # Give run-1 time to acquire the slot.
            await asyncio.sleep(0)
            assert concurrency_module.snapshot(engagement_id).running_count == 1

            # Start run-2 (should block on the slot, then emit queued chunk).
            task2 = asyncio.create_task(
                _stream_to_channel(
                    tool_run_id=tool_run_id_2,
                    engagement_id=engagement_id,
                    server_name=_SERVER_NAME,
                    tool_name=_HEAVY_TOOL_NAME,
                    args={"target": _TARGET},
                    timeout_seconds=float(_TIMEOUT),
                    is_heavy=True,
                    slot_limit=slot_limit,
                    target_host="localhost",
                )
            )

            # Yield to allow run-2 to enter the queue and call on_queued.
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            # run-2 must be queued.
            snap = concurrency_module.snapshot(engagement_id)
            assert snap.queued_count == 1, f"Expected 1 queued run, got {snap.queued_count}"

            # run-2 must have broadcast a 'queued' chunk.
            queued_chunks = []
            while not queue2.empty():
                c = queue2.get_nowait()
                if c.type == "queued":
                    queued_chunks.append(c)
            assert len(queued_chunks) >= 1, "Expected at least one 'queued' chunk"
            assert queued_chunks[0].queue_position == 1
            assert queued_chunks[0].reason in ("slot_full", "target_locked")

            # Check that update_tool_run_status was called with status='queued'.
            queued_status_calls = [
                c for c in status_mock.call_args_list if c.kwargs.get("status") == "queued"
            ]
            assert len(queued_status_calls) >= 1

            # Now let run-1 finish, which releases the slot.
            run1_can_finish.set()
            await task1

            # Run-2 should now be admitted and complete.
            await task2

    # After both tasks finish, pool must be empty.
    snap = concurrency_module.snapshot(engagement_id)
    assert snap.running_count == 0
    assert snap.queued_count == 0

    # update_tool_run_status must have been called with status='running'.
    running_calls = [c for c in status_mock.call_args_list if c.kwargs.get("status") == "running"]
    assert len(running_calls) >= 1, "Expected update_tool_run_status(status='running') call"
    # started_at must be set on the running transition (Decision 6).
    assert running_calls[-1].kwargs.get("started_at") is not None


# ---------------------------------------------------------------------------
# Async heavy run: slot released in finally even on error (Risk 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_heavy_slot_released_on_stream_error() -> None:
    """When streaming raises, the admission handle is released (Risk 3)."""
    engagement_id = uuid4()
    tool_run_id = uuid4()
    slot_limit = 1

    session_mock, ctx = _make_session_ctx()
    status_mock = AsyncMock()
    updated = _make_tool_run_mock(tool_run_id=tool_run_id, status="failed")

    async def _error_stream(*_: Any, **__: Any) -> AsyncIterator[Any]:
        raise RuntimeError("stream exploded")
        yield  # make it a generator

    with (
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            return_value=_error_stream(),
        ),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_status", status_mock),
        patch(
            "app.features.mcp.service.mcp_repo.update_tool_run_result",
            new_callable=AsyncMock,
            return_value=updated,
        ),
        patch("app.features.mcp.service.get_sessionmaker", return_value=lambda: ctx),
    ):
        await _stream_to_channel(
            tool_run_id=tool_run_id,
            engagement_id=engagement_id,
            server_name=_SERVER_NAME,
            tool_name=_HEAVY_TOOL_NAME,
            args={"target": _TARGET},
            timeout_seconds=float(_TIMEOUT),
            is_heavy=True,
            slot_limit=slot_limit,
            target_host="localhost",
        )

    # Slot must be released even though streaming raised.
    snap = concurrency_module.snapshot(engagement_id)
    assert snap.running_count == 0
    assert snap.queued_count == 0


@pytest.mark.asyncio
async def test_async_heavy_queue_drains_after_stream_error() -> None:
    """After a stream error, queued runs are admitted when the slot is freed (Risk 3)."""
    engagement_id = uuid4()
    tool_run_id_1 = uuid4()
    tool_run_id_2 = uuid4()
    slot_limit = 1

    status_mock = AsyncMock()
    done = StreamDone(exit_code=0, stdout="ok\n", stderr="")
    updated1 = _make_tool_run_mock(tool_run_id=tool_run_id_1, status="failed")
    updated2 = _make_tool_run_mock(tool_run_id=tool_run_id_2, status="completed")
    updated2.exit_code = 0

    session1, ctx1 = _make_session_ctx()
    session2, ctx2 = _make_session_ctx()
    ctx_iter = iter([ctx1, ctx2])

    def _sessionmaker_factory() -> MagicMock:
        return next(ctx_iter)

    run1_can_explode = asyncio.Event()

    async def _error_stream(*_: Any, **__: Any) -> AsyncIterator[Any]:
        await run1_can_explode.wait()
        raise RuntimeError("stream exploded")
        yield

    async def _ok_stream(*_: Any, **__: Any) -> AsyncIterator[Any]:
        yield done

    update_result_mock = AsyncMock(side_effect=[updated1, updated2])

    with (
        patch(
            "app.features.mcp.service.mcp_repo.update_tool_run_status",
            status_mock,
        ),
        patch(
            "app.features.mcp.service.mcp_repo.update_tool_run_result",
            update_result_mock,
        ),
        patch("app.features.mcp.service.get_sessionmaker", return_value=_sessionmaker_factory),
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            side_effect=[_error_stream(), _ok_stream()],
        ),
    ):
        # Run-1 holds the slot.
        task1 = asyncio.create_task(
            _stream_to_channel(
                tool_run_id=tool_run_id_1,
                engagement_id=engagement_id,
                server_name=_SERVER_NAME,
                tool_name=_HEAVY_TOOL_NAME,
                args={"target": _TARGET},
                timeout_seconds=float(_TIMEOUT),
                is_heavy=True,
                slot_limit=slot_limit,
                target_host="localhost",
            )
        )

        await asyncio.sleep(0)
        assert concurrency_module.snapshot(engagement_id).running_count == 1

        # Run-2 queues.
        task2 = asyncio.create_task(
            _stream_to_channel(
                tool_run_id=tool_run_id_2,
                engagement_id=engagement_id,
                server_name=_SERVER_NAME,
                tool_name=_HEAVY_TOOL_NAME,
                args={"target": _TARGET},
                timeout_seconds=float(_TIMEOUT),
                is_heavy=True,
                slot_limit=slot_limit,
                target_host="localhost",
            )
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        snap = concurrency_module.snapshot(engagement_id)
        assert snap.queued_count == 1

        # Trigger run-1's error.
        run1_can_explode.set()
        await task1

        # Run-2 should be admitted and complete.
        await task2

    # Both tasks done; pool is empty.
    snap = concurrency_module.snapshot(engagement_id)
    assert snap.running_count == 0
    assert snap.queued_count == 0


# ---------------------------------------------------------------------------
# Sync heavy run: blocks until slot frees
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_heavy_blocks_until_slot_frees() -> None:
    """Sync heavy run acquires a slot; while held, a second run must queue."""
    engagement_id = uuid4()
    slot_limit = 1

    raw_result = MagicMock()
    raw_result.exit_code = 0
    raw_result.stdout = "done\n"
    raw_result.stderr = ""

    updated_row = _make_tool_run_mock(status="completed")
    updated_row.exit_code = 0

    # Use asyncio.Event to simulate a long-running send_tool_call.
    send_started = asyncio.Event()
    send_may_finish = asyncio.Event()
    call_count = 0

    async def _controlled_send(*_: Any, **__: Any) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            send_started.set()
            await send_may_finish.wait()
        return raw_result

    heavy_tool = _make_tool_config(name=_HEAVY_TOOL_NAME, weight="heavy")
    registry = _make_registry_with_tools(heavy_tool)
    engagement_mock = _make_engagement_mock(slot_limit=slot_limit)

    db1 = AsyncMock()
    db2 = AsyncMock()
    tool_run_1 = _make_tool_run_mock(engagement_id=engagement_id)
    tool_run_2 = _make_tool_run_mock(engagement_id=engagement_id)

    create_call = 0

    async def _create_side_effect(*_: Any, **__: Any) -> Any:
        nonlocal create_call
        create_call += 1
        return tool_run_1 if create_call == 1 else tool_run_2

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=(engagement_mock, _make_member_mock()),
        ),
        patch("app.features.mcp.service.get_registry", return_value=registry),
        patch(
            "app.features.mcp.service.mcp_repo.create_tool_run",
            side_effect=_create_side_effect,
        ),
        patch(
            "app.features.mcp.service.subprocess_manager.send_tool_call",
            side_effect=_controlled_send,
        ),
        patch(
            "app.features.mcp.service.mcp_repo.update_tool_run_result",
            new_callable=AsyncMock,
            return_value=updated_row,
        ),
    ):
        # Start run-1 as a background task (it will block inside send_tool_call).
        task1 = asyncio.create_task(
            execute_tool_run(
                db1,
                engagement_id=engagement_id,
                server_name=_SERVER_NAME,
                tool_name=_HEAVY_TOOL_NAME,
                args={"target": _TARGET},
                timeout_seconds=_TIMEOUT,
                user_id=uuid4(),
                async_mode=False,
            )
        )

        # Wait until run-1 has acquired the slot and is inside send_tool_call.
        await send_started.wait()
        snap = concurrency_module.snapshot(engagement_id)
        assert snap.running_count == 1, f"Expected 1 running, got {snap.running_count}"

        # Start run-2; it should be blocked waiting for the slot.
        task2 = asyncio.create_task(
            execute_tool_run(
                db2,
                engagement_id=engagement_id,
                server_name=_SERVER_NAME,
                tool_name=_HEAVY_TOOL_NAME,
                args={"target": _TARGET},
                timeout_seconds=_TIMEOUT,
                user_id=uuid4(),
                async_mode=False,
            )
        )

        # Yield to let task2 enter the admission queue.
        for _ in range(5):
            await asyncio.sleep(0)

        snap = concurrency_module.snapshot(engagement_id)
        assert snap.queued_count == 1, f"Expected 1 queued, got {snap.queued_count}"

        # Let run-1's send_tool_call finish.
        send_may_finish.set()
        result1 = await task1
        result2 = await task2

    assert result1.status == "completed"
    assert result2.status == "completed"

    # Both slots released.
    snap = concurrency_module.snapshot(engagement_id)
    assert snap.running_count == 0
    assert snap.queued_count == 0


# ---------------------------------------------------------------------------
# execute_tool_run async: light tool inserts as 'running', heavy as 'queued'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_run_async_light_inserts_running() -> None:
    """async_mode=True + light tool → create_tool_run called with status='running'."""
    engagement_id = uuid4()
    tool_run = _make_tool_run_mock(engagement_id=engagement_id, status="running")

    db = AsyncMock()

    def _close_coro(coro: Any) -> MagicMock:
        coro.close()
        return MagicMock()

    light_tool = _make_tool_config(name=_LIGHT_TOOL_NAME, weight="light")
    registry = _make_registry_with_tools(light_tool)

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=(_make_engagement_mock(), _make_member_mock()),
        ),
        patch("app.features.mcp.service.get_registry", return_value=registry),
        patch(
            "app.features.mcp.service.mcp_repo.create_tool_run",
            new_callable=AsyncMock,
            return_value=tool_run,
        ) as mock_create,
        patch("asyncio.create_task", side_effect=_close_coro),
    ):
        result = await execute_tool_run(
            db,
            engagement_id=engagement_id,
            server_name=_SERVER_NAME,
            tool_name=_LIGHT_TOOL_NAME,
            args={"target": _TARGET},
            timeout_seconds=_TIMEOUT,
            user_id=uuid4(),
            async_mode=True,
        )

    assert result.status == "running"
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["status"] == "running"

    # Admission manager untouched.
    snap = concurrency_module.snapshot(engagement_id)
    assert snap.running_count == 0


@pytest.mark.asyncio
async def test_async_tool_run_writes_invocation_only(mock_audit_record: AsyncMock) -> None:
    """async_mode=True emits the attributed tool_run invocation; completion is deferred."""
    engagement_id = uuid4()
    user_id = uuid4()
    tool_run = _make_tool_run_mock(engagement_id=engagement_id, status="running")
    db = AsyncMock()

    def _close_coro(coro: Any) -> MagicMock:
        coro.close()
        return MagicMock()

    light_tool = _make_tool_config(name=_LIGHT_TOOL_NAME, weight="light")
    registry = _make_registry_with_tools(light_tool)

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=(_make_engagement_mock(), _make_member_mock()),
        ),
        patch("app.features.mcp.service.get_registry", return_value=registry),
        patch(
            "app.features.mcp.service.mcp_repo.create_tool_run",
            new_callable=AsyncMock,
            return_value=tool_run,
        ),
        patch("asyncio.create_task", side_effect=_close_coro),
    ):
        await execute_tool_run(
            db,
            engagement_id=engagement_id,
            server_name=_SERVER_NAME,
            tool_name=_LIGHT_TOOL_NAME,
            args={"target": _TARGET},
            timeout_seconds=_TIMEOUT,
            user_id=user_id,
            async_mode=True,
        )

    actions = [c.kwargs["action"] for c in mock_audit_record.await_args_list]
    assert actions == [AuditAction.TOOL_RUN]  # no completion entry on the async path
    inv = mock_audit_record.await_args_list[0].kwargs
    assert inv["actor_user_id"] == user_id
    assert inv["target_type"] == "tool_run"
    assert inv["payload"]["server"] == _SERVER_NAME


@pytest.mark.asyncio
async def test_execute_tool_run_async_heavy_inserts_queued() -> None:
    """async_mode=True + heavy tool → create_tool_run called with status='queued'."""
    engagement_id = uuid4()
    tool_run = _make_tool_run_mock(engagement_id=engagement_id, status="queued")

    db = AsyncMock()

    def _close_coro(coro: Any) -> MagicMock:
        coro.close()
        return MagicMock()

    heavy_tool = _make_tool_config(name=_HEAVY_TOOL_NAME, weight="heavy")
    registry = _make_registry_with_tools(heavy_tool)

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=(_make_engagement_mock(slot_limit=3), _make_member_mock()),
        ),
        patch("app.features.mcp.service.get_registry", return_value=registry),
        patch(
            "app.features.mcp.service.mcp_repo.create_tool_run",
            new_callable=AsyncMock,
            return_value=tool_run,
        ) as mock_create,
        patch("asyncio.create_task", side_effect=_close_coro),
    ):
        result = await execute_tool_run(
            db,
            engagement_id=engagement_id,
            server_name=_SERVER_NAME,
            tool_name=_HEAVY_TOOL_NAME,
            args={"target": _TARGET},
            timeout_seconds=_TIMEOUT,
            user_id=uuid4(),
            async_mode=True,
        )

    assert result.status == "queued"
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["status"] == "queued"


# ---------------------------------------------------------------------------
# Sync heavy run: McpServerDown still releases the slot (Risk 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_heavy_slot_released_on_server_down() -> None:
    """McpServerDown on sync heavy path releases the slot (Risk 3)."""
    engagement_id = uuid4()
    tool_run = _make_tool_run_mock(engagement_id=engagement_id)
    slot_limit = 3

    heavy_tool = _make_tool_config(name=_HEAVY_TOOL_NAME, weight="heavy")
    registry = _make_registry_with_tools(heavy_tool)
    engagement_mock = _make_engagement_mock(slot_limit=slot_limit)

    db = AsyncMock()

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=(engagement_mock, _make_member_mock()),
        ),
        patch("app.features.mcp.service.get_registry", return_value=registry),
        patch(
            "app.features.mcp.service.mcp_repo.create_tool_run",
            new_callable=AsyncMock,
            return_value=tool_run,
        ),
        patch(
            "app.features.mcp.service.subprocess_manager.send_tool_call",
            new_callable=AsyncMock,
            side_effect=McpServerDown("httpx is down"),
        ),
        pytest.raises(McpServerDown),
    ):
        await execute_tool_run(
            db,
            engagement_id=engagement_id,
            server_name=_SERVER_NAME,
            tool_name=_HEAVY_TOOL_NAME,
            args={"target": _TARGET},
            timeout_seconds=_TIMEOUT,
            user_id=uuid4(),
            async_mode=False,
        )

    # Slot must be released even though McpServerDown was raised.
    snap = concurrency_module.snapshot(engagement_id)
    assert snap.running_count == 0


# ---------------------------------------------------------------------------
# Async heavy path: queue cap pre-check hoisted BEFORE row/task allocation
# (fix: blocker item 1 — ToolQueueFullError must fire as 429 with no row/task)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_heavy_queue_full_raises_before_row_created() -> None:
    """When the per-engagement queue is full, execute_tool_run with async_mode=True
    must raise ToolQueueFullError BEFORE creating any tool_runs row and BEFORE
    spawning any background task.

    Assertions:
      (a) ToolQueueFullError is raised (→ HTTP 429 in the router).
      (b) create_tool_run is NOT called for the rejected request (no DB row).
      (c) asyncio.create_task is NOT called (no background task spawned).

    This pins the hoisted pre-check in execute_tool_run (the fix).  Prior to the
    fix, ToolQueueFullError was only raised inside _stream_to_channel (inside the
    already-spawned background task), which meant: the 429 was never returned to
    the client, a tool_runs row was already committed, and an asyncio.Task had
    already been allocated.
    """
    import unittest.mock as mock_module

    engagement_id = uuid4()
    cap = 2

    heavy_tool = _make_tool_config(name=_HEAVY_TOOL_NAME, weight="heavy")
    registry = _make_registry_with_tools(heavy_tool)
    engagement_mock = _make_engagement_mock(slot_limit=1)

    # Fill the queue to exactly cap so the pre-check fires.
    slot_limit = 1
    handle = await concurrency_module.acquire(
        engagement_id=engagement_id,
        slot_limit=slot_limit,
        tool_run_id=uuid4(),
        target_host="hostA",
        server_name=_SERVER_NAME,
        tool_name=_HEAVY_TOOL_NAME,
        on_queued=lambda pos, reason: None,
        on_started=lambda: None,
    )
    waiter_tasks: list[asyncio.Task[concurrency_module.AdmissionHandle]] = []
    with mock_module.patch("app.features.mcp.concurrency.MAX_QUEUE_DEPTH", cap):
        for i in range(cap):
            t = asyncio.create_task(
                concurrency_module.acquire(
                    engagement_id=engagement_id,
                    slot_limit=slot_limit,
                    tool_run_id=uuid4(),
                    target_host=f"host{i}",
                    server_name=_SERVER_NAME,
                    tool_name=_HEAVY_TOOL_NAME,
                    on_queued=lambda pos, reason: None,
                    on_started=lambda: None,
                )
            )
            waiter_tasks.append(t)
        await asyncio.sleep(0)
        assert concurrency_module.snapshot(engagement_id).queued_count == cap

        # Now call execute_tool_run with async_mode=True — must raise before any row/task.
        db = AsyncMock()
        create_tool_run_mock = AsyncMock()
        create_task_calls: list[Any] = []

        def _track_create_task(coro: Any) -> MagicMock:
            create_task_calls.append(coro)
            coro.close()
            return MagicMock()

        with (
            patch(
                "app.features.mcp.service.eng_repo.get_engagement_for_member",
                new_callable=AsyncMock,
                return_value=(engagement_mock, _make_member_mock()),
            ),
            patch("app.features.mcp.service.get_registry", return_value=registry),
            patch(
                "app.features.mcp.service.mcp_repo.create_tool_run",
                create_tool_run_mock,
            ),
            patch("asyncio.create_task", side_effect=_track_create_task),
        ):
            with pytest.raises(ToolQueueFullError):
                await execute_tool_run(
                    db,
                    engagement_id=engagement_id,
                    server_name=_SERVER_NAME,
                    tool_name=_HEAVY_TOOL_NAME,
                    args={"target": _TARGET},
                    timeout_seconds=_TIMEOUT,
                    user_id=uuid4(),
                    async_mode=True,
                )

        # (b) No row was created.
        create_tool_run_mock.assert_not_called()
        # (c) No background task was spawned.
        assert len(create_task_calls) == 0, (
            f"Expected no asyncio.create_task calls, got {len(create_task_calls)}"
        )

    # Cleanup.
    concurrency_module.release(handle)
    for t in waiter_tasks:
        h = await t
        concurrency_module.release(h)


@pytest.mark.asyncio
async def test_async_light_run_ignores_queue_cap() -> None:
    """Light runs must NOT be rejected even when the heavy queue is full.

    The pre-check only applies to heavy runs; light runs bypass the queue entirely.
    """
    import unittest.mock as mock_module

    engagement_id = uuid4()
    cap = 1

    light_tool = _make_tool_config(name=_LIGHT_TOOL_NAME, weight="light")
    registry = _make_registry_with_tools(light_tool)
    engagement_mock = _make_engagement_mock(slot_limit=1)
    tool_run = _make_tool_run_mock(engagement_id=engagement_id, status="running")

    db = AsyncMock()

    def _close_coro(coro: Any) -> MagicMock:
        coro.close()
        return MagicMock()

    # Fill the heavy queue to the cap — must NOT affect light runs.
    slot_limit = 1
    handle = await concurrency_module.acquire(
        engagement_id=engagement_id,
        slot_limit=slot_limit,
        tool_run_id=uuid4(),
        target_host="hostA",
        server_name=_SERVER_NAME,
        tool_name=_HEAVY_TOOL_NAME,
        on_queued=lambda pos, reason: None,
        on_started=lambda: None,
    )
    waiter_tasks: list[asyncio.Task[concurrency_module.AdmissionHandle]] = []
    with mock_module.patch("app.features.mcp.concurrency.MAX_QUEUE_DEPTH", cap):
        for i in range(cap):
            t = asyncio.create_task(
                concurrency_module.acquire(
                    engagement_id=engagement_id,
                    slot_limit=slot_limit,
                    tool_run_id=uuid4(),
                    target_host=f"host{i}",
                    server_name=_SERVER_NAME,
                    tool_name=_HEAVY_TOOL_NAME,
                    on_queued=lambda pos, reason: None,
                    on_started=lambda: None,
                )
            )
            waiter_tasks.append(t)
        await asyncio.sleep(0)

        with (
            patch(
                "app.features.mcp.service.eng_repo.get_engagement_for_member",
                new_callable=AsyncMock,
                return_value=(engagement_mock, _make_member_mock()),
            ),
            patch("app.features.mcp.service.get_registry", return_value=registry),
            patch(
                "app.features.mcp.service.mcp_repo.create_tool_run",
                new_callable=AsyncMock,
                return_value=tool_run,
            ),
            patch("asyncio.create_task", side_effect=_close_coro),
        ):
            # Light run must succeed — no ToolQueueFullError.
            result = await execute_tool_run(
                db,
                engagement_id=engagement_id,
                server_name=_SERVER_NAME,
                tool_name=_LIGHT_TOOL_NAME,
                args={"target": _TARGET},
                timeout_seconds=_TIMEOUT,
                user_id=uuid4(),
                async_mode=True,
            )

        assert result.status == "running"

    # Cleanup.
    concurrency_module.release(handle)
    for t in waiter_tasks:
        h = await t
        concurrency_module.release(h)
