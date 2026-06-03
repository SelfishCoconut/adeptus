"""Unit tests for Slice 06 Task 4: pause gate + cancellation registry wiring.

Tests cover:
  - execute_tool_run while engagement is paused raises EngagementPaused and writes NO row
    (async heavy path, sync heavy path, and light path — Risk 5).
  - execute_tool_run on an admitted async run registers the task in the cancellation
    registry after asyncio.create_task.
  - The registry is cleared on task completion (unregister_run in finally).

All external dependencies are mocked; no real DB or subprocess is used.
concurrency._reset() and service._reset_channels() are called in the autouse fixture.
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
from app.features.mcp.concurrency import EngagementPaused, set_paused
from app.features.mcp.registry import McpServerConfig, McpToolConfig
from app.features.mcp.service import (
    _reset_channels,
    execute_tool_run,
)
from app.features.mcp.subprocess_manager import (
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


def _make_tool_config(name: str, weight: str = "light") -> McpToolConfig:
    return McpToolConfig(name=name, weight=weight, capability_flags=["network"])


def _make_registry_with_heavy_and_light() -> dict[str, McpServerConfig]:
    cfg = McpServerConfig(
        name=_SERVER_NAME,
        command="python",
        args=[],
        tools=[
            _make_tool_config(_HEAVY_TOOL_NAME, "heavy"),
            _make_tool_config(_LIGHT_TOOL_NAME, "light"),
        ],
    )
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


def _make_engagement_mock(engagement_id: UUID | None = None, slot_limit: int = 3) -> MagicMock:
    eng = MagicMock()
    eng.id = engagement_id or uuid4()
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
# Pause gate — async heavy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_run_paused_async_heavy_raises_engagement_paused() -> None:
    """A paused engagement rejects a new heavy async run with EngagementPaused.

    No tool_runs row is created and no task is spawned.
    """
    engagement_id = uuid4()
    user_id = uuid4()

    # Pause the engagement in the in-process state.
    set_paused(engagement_id, True)

    create_mock = AsyncMock()
    db = AsyncMock()

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=(_make_engagement_mock(engagement_id), _make_member_mock()),
        ),
        patch(
            "app.features.mcp.service.get_registry",
            return_value=_make_registry_with_heavy_and_light(),
        ),
        patch("app.features.mcp.service.mcp_repo.create_tool_run", create_mock),
        pytest.raises(EngagementPaused),
    ):
        await execute_tool_run(
            db,
            engagement_id=engagement_id,
            server_name=_SERVER_NAME,
            tool_name=_HEAVY_TOOL_NAME,
            args={"target": _TARGET},
            timeout_seconds=_TIMEOUT,
            user_id=user_id,
            async_mode=True,
        )

    # No DB row must have been created.
    create_mock.assert_not_called()


@pytest.mark.asyncio
async def test_execute_tool_run_paused_member_gets_409() -> None:
    """A paused engagement rejects a member's run with EngagementPaused (→ 409).

    C-2 fix: the membership gate runs FIRST (→ 404 for non-members), then the
    pause gate fires (→ 409 for members).  A member submitting to a paused
    engagement must receive EngagementPaused, not EngagementNotFound.
    """
    engagement_id = uuid4()
    user_id = uuid4()

    set_paused(engagement_id, True)

    membership_mock = AsyncMock(
        return_value=(_make_engagement_mock(engagement_id), _make_member_mock())
    )
    db = AsyncMock()

    with (
        patch("app.features.mcp.service.eng_repo.get_engagement_for_member", membership_mock),
        patch(
            "app.features.mcp.service.get_registry",
            return_value=_make_registry_with_heavy_and_light(),
        ),
        pytest.raises(EngagementPaused),
    ):
        await execute_tool_run(
            db,
            engagement_id=engagement_id,
            server_name=_SERVER_NAME,
            tool_name=_HEAVY_TOOL_NAME,
            args={"target": _TARGET},
            timeout_seconds=_TIMEOUT,
            user_id=user_id,
            async_mode=True,
        )

    # C-2: membership gate runs FIRST, so get_engagement_for_member IS called.
    # (A non-member would have received EngagementNotFound → 404 here, not EngagementPaused → 409)
    membership_mock.assert_called_once()


@pytest.mark.asyncio
async def test_execute_tool_run_paused_non_member_gets_not_found() -> None:
    """C-2: A non-member submitting to a paused engagement receives EngagementNotFound (→ 404).

    The membership gate runs BEFORE the pause gate so a non-member cannot infer that
    the engagement exists OR is paused (§17.1 no existence/state disclosure).
    """
    from app.features.mcp.service import EngagementNotFound

    engagement_id = uuid4()
    user_id = uuid4()

    set_paused(engagement_id, True)

    # Simulate non-member: get_engagement_for_member returns None.
    membership_mock = AsyncMock(return_value=None)
    db = AsyncMock()

    with (
        patch("app.features.mcp.service.eng_repo.get_engagement_for_member", membership_mock),
        patch(
            "app.features.mcp.service.get_registry",
            return_value=_make_registry_with_heavy_and_light(),
        ),
        pytest.raises(EngagementNotFound),
    ):
        await execute_tool_run(
            db,
            engagement_id=engagement_id,
            server_name=_SERVER_NAME,
            tool_name=_HEAVY_TOOL_NAME,
            args={"target": _TARGET},
            timeout_seconds=_TIMEOUT,
            user_id=user_id,
            async_mode=True,
        )

    # Must get EngagementNotFound (→ 404), not EngagementPaused (→ 409).
    membership_mock.assert_called_once()


# ---------------------------------------------------------------------------
# Pause gate — sync heavy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_run_paused_sync_heavy_raises_engagement_paused() -> None:
    """Paused engagement rejects a sync heavy run — no row created."""
    engagement_id = uuid4()
    user_id = uuid4()

    set_paused(engagement_id, True)

    create_mock = AsyncMock()
    db = AsyncMock()

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=(_make_engagement_mock(engagement_id), _make_member_mock()),
        ),
        patch(
            "app.features.mcp.service.get_registry",
            return_value=_make_registry_with_heavy_and_light(),
        ),
        patch("app.features.mcp.service.mcp_repo.create_tool_run", create_mock),
        pytest.raises(EngagementPaused),
    ):
        await execute_tool_run(
            db,
            engagement_id=engagement_id,
            server_name=_SERVER_NAME,
            tool_name=_HEAVY_TOOL_NAME,
            args={"target": _TARGET},
            timeout_seconds=_TIMEOUT,
            user_id=user_id,
            async_mode=False,
        )

    create_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Pause gate — light path (Risk 5: pause blocks light lane too)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_run_paused_light_raises_engagement_paused() -> None:
    """Paused engagement rejects a light run (Risk 5: pause blocks all lanes).

    Light runs never call acquire, so the pause check MUST be in execute_tool_run
    itself (before the heavy/light branch) rather than relying on acquire's guard.
    """
    engagement_id = uuid4()
    user_id = uuid4()

    set_paused(engagement_id, True)

    create_mock = AsyncMock()
    db = AsyncMock()

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=(_make_engagement_mock(engagement_id), _make_member_mock()),
        ),
        patch(
            "app.features.mcp.service.get_registry",
            return_value=_make_registry_with_heavy_and_light(),
        ),
        patch("app.features.mcp.service.mcp_repo.create_tool_run", create_mock),
        pytest.raises(EngagementPaused),
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

    create_mock.assert_not_called()


@pytest.mark.asyncio
async def test_execute_tool_run_paused_light_sync_raises_engagement_paused() -> None:
    """Paused engagement rejects a sync light run — no row created."""
    engagement_id = uuid4()
    user_id = uuid4()

    set_paused(engagement_id, True)

    create_mock = AsyncMock()
    db = AsyncMock()

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=(_make_engagement_mock(engagement_id), _make_member_mock()),
        ),
        patch(
            "app.features.mcp.service.get_registry",
            return_value=_make_registry_with_heavy_and_light(),
        ),
        patch("app.features.mcp.service.mcp_repo.create_tool_run", create_mock),
        pytest.raises(EngagementPaused),
    ):
        await execute_tool_run(
            db,
            engagement_id=engagement_id,
            server_name=_SERVER_NAME,
            tool_name=_LIGHT_TOOL_NAME,
            args={"target": _TARGET},
            timeout_seconds=_TIMEOUT,
            user_id=user_id,
            async_mode=False,
        )

    create_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Admitted async run: registered in the kill registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_run_admitted_async_registers_in_registry() -> None:
    """An admitted async run is registered in the cancellation registry.

    After execute_tool_run returns (async_mode=True), the tool_run_id must be
    present in the cancellation registry so kill_run can find and cancel it.
    """
    engagement_id = uuid4()
    user_id = uuid4()
    tool_run_id = uuid4()

    tool_run_mock = _make_tool_run_mock(tool_run_id=tool_run_id, engagement_id=engagement_id)

    # We need the background task to run to completion so the test can observe
    # registration.  Use an event to let us control when it finishes.
    task_started = asyncio.Event()
    task_should_finish = asyncio.Event()

    async def _slow_stream(*_: Any, **__: Any) -> AsyncIterator[Any]:
        task_started.set()
        await task_should_finish.wait()
        yield StreamDone(exit_code=0, stdout="ok\n", stderr="")

    updated = _make_tool_run_mock(tool_run_id=tool_run_id, status="completed")
    updated.exit_code = 0

    session_mock, ctx = _make_session_ctx()

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=(_make_engagement_mock(engagement_id), _make_member_mock()),
        ),
        patch(
            "app.features.mcp.service.get_registry",
            return_value=_make_registry_with_heavy_and_light(),
        ),
        patch(
            "app.features.mcp.service.mcp_repo.create_tool_run",
            new_callable=AsyncMock,
            return_value=tool_run_mock,
        ),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_status", AsyncMock()),
        patch(
            "app.features.mcp.service.mcp_repo.update_tool_run_result",
            new_callable=AsyncMock,
            return_value=updated,
        ),
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            side_effect=_slow_stream,
        ),
        patch("app.features.mcp.service.get_sessionmaker", return_value=lambda: ctx),
    ):
        await execute_tool_run(
            AsyncMock(),
            engagement_id=engagement_id,
            server_name=_SERVER_NAME,
            tool_name=_LIGHT_TOOL_NAME,
            args={"target": _TARGET},
            timeout_seconds=_TIMEOUT,
            user_id=user_id,
            async_mode=True,
        )

        # Give the background task a tick to register.
        await task_started.wait()

        # The run must be in the registry while it is in-flight.
        assert tool_run_id in concurrency_module._registry, (
            "In-flight tool run must be registered in the cancellation registry"
        )
        entry = concurrency_module._registry[tool_run_id]
        assert entry.engagement_id == engagement_id
        assert not entry.task.done()

        # Let the task finish.
        task_should_finish.set()
        await asyncio.sleep(0)
        # Drain the event loop to let the task complete.
        for _ in range(10):
            await asyncio.sleep(0)

    # After completion the run is unregistered from the registry.
    assert tool_run_id not in concurrency_module._registry, (
        "Completed tool run must be unregistered from the cancellation registry"
    )


# ---------------------------------------------------------------------------
# Admitted async heavy run: registered in the kill registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_run_admitted_async_heavy_registers_in_registry() -> None:
    """An admitted async heavy run is also registered in the cancellation registry."""
    engagement_id = uuid4()
    user_id = uuid4()
    tool_run_id = uuid4()

    tool_run_mock = _make_tool_run_mock(tool_run_id=tool_run_id, engagement_id=engagement_id)

    task_started = asyncio.Event()
    task_should_finish = asyncio.Event()

    async def _slow_stream(*_: Any, **__: Any) -> AsyncIterator[Any]:
        task_started.set()
        await task_should_finish.wait()
        yield StreamDone(exit_code=0, stdout="ok\n", stderr="")

    updated = _make_tool_run_mock(tool_run_id=tool_run_id, status="completed")
    updated.exit_code = 0

    session_mock, ctx = _make_session_ctx()

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=(_make_engagement_mock(engagement_id, slot_limit=3), _make_member_mock()),
        ),
        patch(
            "app.features.mcp.service.get_registry",
            return_value=_make_registry_with_heavy_and_light(),
        ),
        patch(
            "app.features.mcp.service.mcp_repo.create_tool_run",
            new_callable=AsyncMock,
            return_value=tool_run_mock,
        ),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_status", AsyncMock()),
        patch(
            "app.features.mcp.service.mcp_repo.update_tool_run_result",
            new_callable=AsyncMock,
            return_value=updated,
        ),
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            side_effect=_slow_stream,
        ),
        patch("app.features.mcp.service.get_sessionmaker", return_value=lambda: ctx),
    ):
        await execute_tool_run(
            AsyncMock(),
            engagement_id=engagement_id,
            server_name=_SERVER_NAME,
            tool_name=_HEAVY_TOOL_NAME,
            args={"target": _TARGET},
            timeout_seconds=_TIMEOUT,
            user_id=user_id,
            async_mode=True,
        )

        await task_started.wait()

        # Registered while in-flight.
        assert tool_run_id in concurrency_module._registry, (
            "In-flight heavy tool run must be registered in the cancellation registry"
        )
        entry = concurrency_module._registry[tool_run_id]
        assert entry.engagement_id == engagement_id
        assert entry.holds_slot is True  # heavy run holds a slot

        # Allow completion.
        task_should_finish.set()
        for _ in range(10):
            await asyncio.sleep(0)

    # Unregistered after completion.
    assert tool_run_id not in concurrency_module._registry


# ---------------------------------------------------------------------------
# Not paused → run proceeds normally
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_run_not_paused_light_async_succeeds() -> None:
    """A non-paused engagement admits a light async run normally."""
    engagement_id = uuid4()
    user_id = uuid4()
    tool_run_id = uuid4()

    tool_run_mock = _make_tool_run_mock(tool_run_id=tool_run_id, engagement_id=engagement_id)
    done = StreamDone(exit_code=0, stdout="ok\n", stderr="")
    updated = _make_tool_run_mock(tool_run_id=tool_run_id, status="completed")
    updated.exit_code = 0

    session_mock, ctx = _make_session_ctx()
    create_mock = AsyncMock(return_value=tool_run_mock)

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=(_make_engagement_mock(engagement_id), _make_member_mock()),
        ),
        patch(
            "app.features.mcp.service.get_registry",
            return_value=_make_registry_with_heavy_and_light(),
        ),
        patch("app.features.mcp.service.mcp_repo.create_tool_run", create_mock),
        patch("app.features.mcp.service.mcp_repo.update_tool_run_status", AsyncMock()),
        patch(
            "app.features.mcp.service.mcp_repo.update_tool_run_result",
            new_callable=AsyncMock,
            return_value=updated,
        ),
        patch(
            "app.features.mcp.service.subprocess_manager.stream_tool_call",
            return_value=_canned_stream(done),
        ),
        patch("app.features.mcp.service.get_sessionmaker", return_value=lambda: ctx),
    ):
        await execute_tool_run(
            AsyncMock(),
            engagement_id=engagement_id,
            server_name=_SERVER_NAME,
            tool_name=_LIGHT_TOOL_NAME,
            args={"target": _TARGET},
            timeout_seconds=_TIMEOUT,
            user_id=user_id,
            async_mode=True,
        )

    # Row was created.
    create_mock.assert_called_once()
