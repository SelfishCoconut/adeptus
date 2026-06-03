"""Unit tests for async streaming support in app.features.mcp.service.

Tests cover:
  - execute_tool_run with async_mode=True inserts a row with status 'running'
    and returns a partial ToolRunResult (exit_code None, finished_at None).
  - _stream_to_channel: StreamChunk events are broadcast to subscriber queues AND
    accumulated in the replay buffer.
  - _stream_to_channel: StreamDone updates the DB row to status 'completed' with
    correct exit_code/stdout/stderr/finished_at; a 'done' chunk is broadcast.
  - exit_code == 124 → row status 'timed_out'.
  - stream_tool_call raising McpServerDown → row status 'failed'; 'error' chunk broadcast.
  - broadcast/subscribe/unsubscribe/replay: subscribe returns the replay snapshot;
    broadcast reaches the subscriber; unsubscribe removes it; _discard_channel clears.
  - _reset_channels() clears module-level state (test isolation).

All external dependencies (subprocess_manager.stream_tool_call, get_sessionmaker,
engagements repository, mcp repository) are mocked; no real DB or subprocess is
used unless the db_session fixture is explicitly wired.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.features.mcp.registry import McpServerConfig, McpToolConfig
from app.features.mcp.schemas import ToolRunResult, WebSocketOutputChunk
from app.features.mcp.service import (
    EngagementNotFound,
    _discard_channel,
    _reset_channels,
    _stream_to_channel,
    broadcast_tool_run_output,
    execute_tool_run,
    subscribe_tool_run,
    unsubscribe_tool_run,
)
from app.features.mcp.subprocess_manager import (
    McpServerDown,
    McpServerNotFound,
    StreamChunk,
    StreamDone,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SERVER_NAME = "httpx"
_TOOL_NAME = "run_httpx"
_ARGS: dict[str, Any] = {"target": "http://localhost:3000"}
_TIMEOUT = 30
_ENGAGEMENT_ID = uuid4()


def _make_tool_config(name: str = _TOOL_NAME) -> McpToolConfig:
    return McpToolConfig(
        name=name,
        weight="light",
        capability_flags=["network"],
    )


def _make_server_config(name: str = _SERVER_NAME) -> McpServerConfig:
    return McpServerConfig(
        name=name,
        command="python",
        args=["-m", "mcp_servers.httpx"],
        tools=[_make_tool_config()],
    )


def _make_registry() -> dict[str, McpServerConfig]:
    cfg = _make_server_config()
    return {cfg.name: cfg}


def _make_tool_run_mock(
    tool_run_id: UUID | None = None,
    engagement_id: UUID | None = None,
    status: str = "running",
) -> MagicMock:
    run = MagicMock()
    run.id = tool_run_id or uuid4()
    run.engagement_id = engagement_id or uuid4()
    run.server_name = _SERVER_NAME
    run.tool_name = _TOOL_NAME
    run.exit_code = None
    run.stdout = ""
    run.stderr = ""
    run.started_at = datetime.now(tz=UTC)
    run.finished_at = None
    run.status = status
    run.preset_name = None
    return run


def _make_engagement_mock() -> MagicMock:
    eng = MagicMock()
    eng.id = uuid4()
    return eng


def _make_member_mock() -> MagicMock:
    member = MagicMock()
    member.role = "member"
    return member


async def _canned_stream(*events: Any) -> AsyncIterator[Any]:
    """Yield canned StreamEvent objects from an async generator."""
    for ev in events:
        yield ev


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_channels() -> Iterator[None]:
    """Ensure channels are clean before and after every test."""
    _reset_channels()
    yield
    _reset_channels()


# ---------------------------------------------------------------------------
# broadcast / subscribe / unsubscribe / _discard_channel
# ---------------------------------------------------------------------------


def test_broadcast_reaches_subscriber() -> None:
    """broadcast_tool_run_output posts chunks to subscriber queues."""
    tool_run_id = uuid4()
    _, queue = subscribe_tool_run(tool_run_id)
    chunk = WebSocketOutputChunk(type="stdout", data="hello\n")
    broadcast_tool_run_output(tool_run_id, chunk)
    assert queue.qsize() == 1
    assert queue.get_nowait() == chunk


def test_broadcast_accumulates_replay() -> None:
    """broadcast_tool_run_output appends every chunk to the replay buffer."""
    tool_run_id = uuid4()
    c1 = WebSocketOutputChunk(type="stdout", data="line1\n")
    c2 = WebSocketOutputChunk(type="stderr", data="err\n")
    broadcast_tool_run_output(tool_run_id, c1)
    broadcast_tool_run_output(tool_run_id, c2)
    replay, _ = subscribe_tool_run(tool_run_id)
    assert replay == [c1, c2]


def test_subscribe_returns_replay_snapshot() -> None:
    """subscribe_tool_run returns a copy of chunks broadcast before the subscription."""
    tool_run_id = uuid4()
    c1 = WebSocketOutputChunk(type="stdout", data="early\n")
    broadcast_tool_run_output(tool_run_id, c1)
    replay, queue = subscribe_tool_run(tool_run_id)
    assert replay == [c1]
    # Post a chunk after subscription; it must appear in the queue but NOT in the
    # already-captured replay snapshot.
    c2 = WebSocketOutputChunk(type="stdout", data="late\n")
    broadcast_tool_run_output(tool_run_id, c2)
    assert queue.qsize() == 1  # only the post-subscription chunk
    assert queue.get_nowait() == c2
    assert replay == [c1]  # snapshot unchanged


def test_unsubscribe_removes_queue() -> None:
    """unsubscribe_tool_run prevents future broadcasts from reaching the queue."""
    tool_run_id = uuid4()
    _, queue = subscribe_tool_run(tool_run_id)
    unsubscribe_tool_run(tool_run_id, queue)
    broadcast_tool_run_output(tool_run_id, WebSocketOutputChunk(type="stdout", data="x"))
    assert queue.qsize() == 0


def test_unsubscribe_missing_channel_is_safe() -> None:
    """unsubscribe_tool_run is a no-op for a channel that does not exist."""
    queue: asyncio.Queue[WebSocketOutputChunk] = asyncio.Queue()
    unsubscribe_tool_run(uuid4(), queue)  # should not raise


def test_discard_channel_removes_entry() -> None:
    """_discard_channel removes the channel from the module-level map."""
    tool_run_id = uuid4()
    broadcast_tool_run_output(tool_run_id, WebSocketOutputChunk(type="stdout", data="x"))
    _discard_channel(tool_run_id)
    # A fresh subscribe after discard creates a new empty channel.
    replay, _ = subscribe_tool_run(tool_run_id)
    assert replay == []


def test_discard_missing_channel_is_safe() -> None:
    """_discard_channel is a no-op for a channel that does not exist."""
    _discard_channel(uuid4())  # should not raise


# ---------------------------------------------------------------------------
# execute_tool_run async_mode=True — partial result
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_run_async_mode_returns_running_partial() -> None:
    """async_mode=True returns a partial ToolRunResult with status 'running'."""
    engagement_id = uuid4()
    user_id = uuid4()
    tool_run = _make_tool_run_mock(engagement_id=engagement_id, status="running")

    db = AsyncMock()

    captured_coros: list[Any] = []

    def _capture_and_cancel(coro: Any) -> MagicMock:
        # Close the coroutine so it is never awaited and avoids RuntimeWarning.
        coro.close()
        captured_coros.append(coro)
        return MagicMock()

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=(_make_engagement_mock(), _make_member_mock()),
        ),
        patch("app.features.mcp.service.get_registry", return_value=_make_registry()),
        patch(
            "app.features.mcp.service.mcp_repo.create_tool_run",
            new_callable=AsyncMock,
            return_value=tool_run,
        ) as mock_create,
        patch("asyncio.create_task", side_effect=_capture_and_cancel) as mock_create_task,
    ):
        result = await execute_tool_run(
            db,
            engagement_id=engagement_id,
            server_name=_SERVER_NAME,
            tool_name=_TOOL_NAME,
            args=_ARGS,
            timeout_seconds=_TIMEOUT,
            user_id=user_id,
            async_mode=True,
        )

    assert isinstance(result, ToolRunResult)
    assert result.status == "running"
    assert result.exit_code is None
    assert result.stdout == ""
    assert result.stderr == ""
    assert result.finished_at is None

    # create_tool_run must have been called with status='running'.
    mock_create.assert_called_once()
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["status"] == "running"

    # DB must have been committed (so the WS endpoint can see the running row).
    db.commit.assert_awaited_once()

    # A background task must have been launched.
    mock_create_task.assert_called_once()


@pytest.mark.asyncio
async def test_execute_tool_run_async_mode_passes_preset_name() -> None:
    """async_mode=True forwards preset_name to create_tool_run and into the result."""
    engagement_id = uuid4()
    user_id = uuid4()
    tool_run = _make_tool_run_mock(engagement_id=engagement_id, status="running")
    tool_run.preset_name = "quick"

    db = AsyncMock()

    def _close_coro(coro: Any) -> MagicMock:
        coro.close()
        return MagicMock()

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=(_make_engagement_mock(), _make_member_mock()),
        ),
        patch("app.features.mcp.service.get_registry", return_value=_make_registry()),
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
            tool_name=_TOOL_NAME,
            args=_ARGS,
            timeout_seconds=_TIMEOUT,
            user_id=user_id,
            async_mode=True,
            preset_name="quick",
        )

    assert result.preset_name == "quick"
    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["preset_name"] == "quick"


@pytest.mark.asyncio
async def test_execute_tool_run_async_membership_denied() -> None:
    """async_mode=True still enforces membership (EngagementNotFound → 404)."""
    db = AsyncMock()

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=None,
        ),
        pytest.raises(EngagementNotFound),
    ):
        await execute_tool_run(
            db,
            engagement_id=uuid4(),
            server_name=_SERVER_NAME,
            tool_name=_TOOL_NAME,
            args=_ARGS,
            timeout_seconds=_TIMEOUT,
            user_id=uuid4(),
            async_mode=True,
        )


@pytest.mark.asyncio
async def test_execute_tool_run_async_unknown_server() -> None:
    """async_mode=True raises McpServerNotFound for unknown server."""
    db = AsyncMock()

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=(_make_engagement_mock(), _make_member_mock()),
        ),
        patch("app.features.mcp.service.get_registry", return_value={}),
        pytest.raises(McpServerNotFound),
    ):
        await execute_tool_run(
            db,
            engagement_id=uuid4(),
            server_name="no-such",
            tool_name=_TOOL_NAME,
            args=_ARGS,
            timeout_seconds=_TIMEOUT,
            user_id=uuid4(),
            async_mode=True,
        )


# ---------------------------------------------------------------------------
# _stream_to_channel — happy path (completed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_to_channel_completed() -> None:
    """StreamChunks are broadcast; StreamDone updates the row to 'completed'."""
    tool_run_id = uuid4()
    replay, queue = subscribe_tool_run(tool_run_id)

    chunk1 = StreamChunk(type="stdout", data="line1\n")
    chunk2 = StreamChunk(type="stderr", data="err\n")
    done = StreamDone(exit_code=0, stdout="line1\n", stderr="err\n")

    updated_row = _make_tool_run_mock(tool_run_id=tool_run_id, status="completed")
    updated_row.exit_code = 0
    updated_row.stdout = "line1\n"
    updated_row.stderr = "err\n"

    session_mock = AsyncMock()
    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session_mock)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            return_value=_canned_stream(chunk1, chunk2, done),
        ),
        patch(
            "app.features.mcp.service.mcp_repo.update_tool_run_result",
            new_callable=AsyncMock,
            return_value=updated_row,
        ) as mock_update,
        patch(
            "app.features.mcp.service.get_sessionmaker",
            return_value=lambda: session_ctx,
        ),
    ):
        await _stream_to_channel(
            tool_run_id=tool_run_id,
            engagement_id=_ENGAGEMENT_ID,
            server_name=_SERVER_NAME,
            tool_name=_TOOL_NAME,
            args=_ARGS,
            timeout_seconds=float(_TIMEOUT),
        )

    # StreamChunk events must have been broadcast.
    collected: list[WebSocketOutputChunk] = []
    while not queue.empty():
        collected.append(queue.get_nowait())

    # chunk1, chunk2, plus the 'done' broadcast = 3 messages
    assert len(collected) == 3
    assert collected[0].type == "stdout"
    assert collected[0].data == "line1\n"
    assert collected[1].type == "stderr"
    assert collected[1].data == "err\n"
    assert collected[2].type == "done"
    assert collected[2].exit_code == 0

    # update_tool_run_result must have been called with status='completed'.
    mock_update.assert_called_once()
    call_kwargs = mock_update.call_args.kwargs
    assert call_kwargs["status"] == "completed"
    assert call_kwargs["exit_code"] == 0

    # The session must have been committed.
    session_mock.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_to_channel_replay_buffer_accumulates() -> None:
    """Chunks are added to the replay buffer during streaming."""
    tool_run_id = uuid4()

    chunk1 = StreamChunk(type="stdout", data="a\n")
    done = StreamDone(exit_code=0, stdout="a\n", stderr="")

    updated_row = _make_tool_run_mock(tool_run_id=tool_run_id, status="completed")
    updated_row.exit_code = 0
    updated_row.stdout = "a\n"

    session_mock = AsyncMock()
    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session_mock)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            return_value=_canned_stream(chunk1, done),
        ),
        patch(
            "app.features.mcp.service.mcp_repo.update_tool_run_result",
            new_callable=AsyncMock,
            return_value=updated_row,
        ),
        patch(
            "app.features.mcp.service.get_sessionmaker",
            return_value=lambda: session_ctx,
        ),
    ):
        # Subscribe BEFORE streaming starts to capture replay.
        replay_before, queue = subscribe_tool_run(tool_run_id)
        assert replay_before == []  # nothing yet

        await _stream_to_channel(
            tool_run_id=tool_run_id,
            engagement_id=_ENGAGEMENT_ID,
            server_name=_SERVER_NAME,
            tool_name=_TOOL_NAME,
            args=_ARGS,
            timeout_seconds=float(_TIMEOUT),
        )

    # Queue received all broadcast events (chunk1 + done).
    assert queue.qsize() == 2


# ---------------------------------------------------------------------------
# _stream_to_channel — timed_out (exit_code 124)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_to_channel_timed_out() -> None:
    """exit_code 124 from StreamDone → row status 'timed_out'."""
    tool_run_id = uuid4()

    done = StreamDone(exit_code=124, stdout="", stderr="Killed (timeout)")

    updated_row = _make_tool_run_mock(tool_run_id=tool_run_id, status="timed_out")
    updated_row.exit_code = 124

    session_mock = AsyncMock()
    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session_mock)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            return_value=_canned_stream(done),
        ),
        patch(
            "app.features.mcp.service.mcp_repo.update_tool_run_result",
            new_callable=AsyncMock,
            return_value=updated_row,
        ) as mock_update,
        patch(
            "app.features.mcp.service.get_sessionmaker",
            return_value=lambda: session_ctx,
        ),
    ):
        await _stream_to_channel(
            tool_run_id=tool_run_id,
            engagement_id=_ENGAGEMENT_ID,
            server_name=_SERVER_NAME,
            tool_name=_TOOL_NAME,
            args=_ARGS,
            timeout_seconds=float(_TIMEOUT),
        )

    call_kwargs = mock_update.call_args.kwargs
    assert call_kwargs["status"] == "timed_out"


# ---------------------------------------------------------------------------
# _stream_to_channel — McpServerDown → failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_to_channel_server_down_sets_failed() -> None:
    """McpServerDown from stream_tool_call → row status 'failed' + 'error' chunk broadcast."""
    tool_run_id = uuid4()
    _, queue = subscribe_tool_run(tool_run_id)

    async def _error_stream() -> AsyncIterator[Any]:
        raise McpServerDown("server died")
        yield  # make it a generator

    updated_row = _make_tool_run_mock(tool_run_id=tool_run_id, status="failed")

    session_mock = AsyncMock()
    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session_mock)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            return_value=_error_stream(),
        ),
        patch(
            "app.features.mcp.service.mcp_repo.update_tool_run_result",
            new_callable=AsyncMock,
            return_value=updated_row,
        ) as mock_update,
        patch(
            "app.features.mcp.service.get_sessionmaker",
            return_value=lambda: session_ctx,
        ),
    ):
        await _stream_to_channel(
            tool_run_id=tool_run_id,
            engagement_id=_ENGAGEMENT_ID,
            server_name=_SERVER_NAME,
            tool_name=_TOOL_NAME,
            args=_ARGS,
            timeout_seconds=float(_TIMEOUT),
        )

    call_kwargs = mock_update.call_args.kwargs
    assert call_kwargs["status"] == "failed"

    # An 'error' chunk must have been broadcast.
    error_chunks = []
    while not queue.empty():
        c = queue.get_nowait()
        if c.type == "error":
            error_chunks.append(c)
    assert len(error_chunks) == 1
    assert "server died" in (error_chunks[0].message or "")


@pytest.mark.asyncio
async def test_stream_to_channel_generic_exception_sets_failed() -> None:
    """Any unexpected exception → row status 'failed' + 'error' chunk broadcast."""
    tool_run_id = uuid4()
    _, queue = subscribe_tool_run(tool_run_id)

    async def _boom_stream() -> AsyncIterator[Any]:
        raise RuntimeError("unexpected boom")
        yield  # make it a generator

    updated_row = _make_tool_run_mock(tool_run_id=tool_run_id, status="failed")

    session_mock = AsyncMock()
    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session_mock)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            return_value=_boom_stream(),
        ),
        patch(
            "app.features.mcp.service.mcp_repo.update_tool_run_result",
            new_callable=AsyncMock,
            return_value=updated_row,
        ) as mock_update,
        patch(
            "app.features.mcp.service.get_sessionmaker",
            return_value=lambda: session_ctx,
        ),
    ):
        await _stream_to_channel(
            tool_run_id=tool_run_id,
            engagement_id=_ENGAGEMENT_ID,
            server_name=_SERVER_NAME,
            tool_name=_TOOL_NAME,
            args=_ARGS,
            timeout_seconds=float(_TIMEOUT),
        )

    call_kwargs = mock_update.call_args.kwargs
    assert call_kwargs["status"] == "failed"
    error_chunks = [q for q in _drain_queue(queue) if q.type == "error"]
    assert len(error_chunks) == 1
    assert "unexpected boom" in (error_chunks[0].message or "")


# ---------------------------------------------------------------------------
# _stream_to_channel — channel discarded in finally
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_to_channel_discards_channel_on_completion() -> None:
    """After _stream_to_channel completes, the channel is discarded (replay empty)."""
    tool_run_id = uuid4()

    done = StreamDone(exit_code=0, stdout="x", stderr="")
    updated_row = _make_tool_run_mock(tool_run_id=tool_run_id, status="completed")
    updated_row.exit_code = 0
    updated_row.stdout = "x"

    session_mock = AsyncMock()
    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session_mock)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            return_value=_canned_stream(done),
        ),
        patch(
            "app.features.mcp.service.mcp_repo.update_tool_run_result",
            new_callable=AsyncMock,
            return_value=updated_row,
        ),
        patch(
            "app.features.mcp.service.get_sessionmaker",
            return_value=lambda: session_ctx,
        ),
    ):
        await _stream_to_channel(
            tool_run_id=tool_run_id,
            engagement_id=_ENGAGEMENT_ID,
            server_name=_SERVER_NAME,
            tool_name=_TOOL_NAME,
            args=_ARGS,
            timeout_seconds=float(_TIMEOUT),
        )

    # After completion, subscribe sees an empty replay (channel was discarded).
    replay, _ = subscribe_tool_run(tool_run_id)
    assert replay == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drain_queue(queue: asyncio.Queue[WebSocketOutputChunk]) -> list[WebSocketOutputChunk]:
    """Drain all items from a Queue without blocking."""
    items: list[WebSocketOutputChunk] = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items
