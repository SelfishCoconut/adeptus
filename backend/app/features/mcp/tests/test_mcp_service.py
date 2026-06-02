"""Unit tests for app.features.mcp.service.

All external dependencies (registry, subprocess_manager, engagements repository,
mcp repository) are mocked — no real DB or subprocess is used.

Tests cover:
  - list_servers aggregates registry + subprocess status
  - execute_tool_run happy path
  - execute_tool_run: unknown server → McpServerNotFound
  - execute_tool_run: down server → McpServerDown
  - execute_tool_run: non-member → EngagementNotFound (404, existence hidden — §17.1)
  - execute_tool_run: admin user who is not an explicit engagement member →
    EngagementNotFound (§4 no-bypass; denial is a 404, not a 403, per §17.1)
  - execute_tool_run: engagement not found → EngagementNotFound
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.features.mcp.registry import McpServerConfig, McpToolConfig
from app.features.mcp.schemas import McpServerInfo, McpToolDeclaration, ToolRunResult
from app.features.mcp.service import (
    EngagementNotFound,
    execute_tool_run,
    list_servers,
)
from app.features.mcp.subprocess_manager import (
    McpRawResult,
    McpServerDown,
    McpServerNotFound,
)

# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------

_SERVER_NAME = "shell-exec"
_TOOL_NAME = "run_command"
_ARGS: dict[str, Any] = {"command": "echo hello"}
_TIMEOUT = 30


def _make_tool_config(
    name: str = _TOOL_NAME,
    weight: str = "light",
    capability_flags: list[str] | None = None,
) -> McpToolConfig:
    return McpToolConfig(
        name=name,
        weight=weight,
        capability_flags=capability_flags or ["shell-exec", "filesystem-write"],
    )


def _make_server_config(
    name: str = _SERVER_NAME,
    tools: list[McpToolConfig] | None = None,
) -> McpServerConfig:
    return McpServerConfig(
        name=name,
        command="python",
        args=["-m", "mcp_servers.shell_exec"],
        tools=tools or [_make_tool_config()],
    )


def _make_registry(
    servers: list[McpServerConfig] | None = None,
) -> dict[str, McpServerConfig]:
    if servers is None:
        servers = [_make_server_config()]
    return {s.name: s for s in servers}


def _make_tool_run(
    tool_run_id: UUID | None = None,
    engagement_id: UUID | None = None,
    exit_code: int = 0,
    stdout: str = "hello\n",
    stderr: str = "",
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
) -> MagicMock:
    """Return a mock ToolRun ORM object with the given field values."""
    run = MagicMock()
    run.id = tool_run_id or uuid4()
    run.engagement_id = engagement_id or uuid4()
    run.server_name = _SERVER_NAME
    run.tool_name = _TOOL_NAME
    run.exit_code = exit_code
    run.stdout = stdout
    run.stderr = stderr
    run.started_at = started_at or datetime.now(tz=UTC)
    run.finished_at = finished_at or datetime.now(tz=UTC)
    run.status = "completed"
    run.preset_name = None
    return run


def _make_engagement_mock() -> MagicMock:
    """Return a mock Engagement ORM object."""
    eng = MagicMock()
    eng.id = uuid4()
    return eng


def _make_member_mock() -> MagicMock:
    """Return a mock EngagementMember ORM object."""
    member = MagicMock()
    member.role = "member"
    return member


# ---------------------------------------------------------------------------
# list_servers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_servers_returns_server_info() -> None:
    """list_servers returns one McpServerInfo per registry entry."""
    registry = _make_registry()

    with (
        patch("app.features.mcp.service.get_registry", return_value=registry),
        patch(
            "app.features.mcp.service.subprocess_manager.get_server_status",
            return_value="running",
        ),
    ):
        result = await list_servers()

    assert len(result) == 1
    info = result[0]
    assert isinstance(info, McpServerInfo)
    assert info.server_name == _SERVER_NAME
    assert info.status == "running"


@pytest.mark.asyncio
async def test_list_servers_aggregates_tool_declarations() -> None:
    """list_servers includes McpToolDeclaration for each declared tool."""
    tool = _make_tool_config(name="run_command", weight="light", capability_flags=["shell-exec"])
    registry = _make_registry(servers=[_make_server_config(tools=[tool])])

    with (
        patch("app.features.mcp.service.get_registry", return_value=registry),
        patch(
            "app.features.mcp.service.subprocess_manager.get_server_status",
            return_value="running",
        ),
    ):
        result = await list_servers()

    assert len(result[0].tools) == 1
    decl = result[0].tools[0]
    assert isinstance(decl, McpToolDeclaration)
    assert decl.name == "run_command"
    assert decl.weight == "light"
    assert decl.capability_flags == ["shell-exec"]


@pytest.mark.asyncio
async def test_list_servers_reflects_stopped_status() -> None:
    """list_servers reports 'stopped' when the subprocess manager says so."""
    registry = _make_registry()

    with (
        patch("app.features.mcp.service.get_registry", return_value=registry),
        patch(
            "app.features.mcp.service.subprocess_manager.get_server_status",
            return_value="stopped",
        ),
    ):
        result = await list_servers()

    assert result[0].status == "stopped"


@pytest.mark.asyncio
async def test_list_servers_multiple_servers() -> None:
    """list_servers returns an entry for every server in the registry."""
    servers = [
        _make_server_config(name="shell-exec"),
        _make_server_config(name="other-server"),
    ]
    registry = _make_registry(servers=servers)

    with (
        patch("app.features.mcp.service.get_registry", return_value=registry),
        patch(
            "app.features.mcp.service.subprocess_manager.get_server_status",
            return_value="running",
        ),
    ):
        result = await list_servers()

    assert len(result) == 2
    names = {info.server_name for info in result}
    assert names == {"shell-exec", "other-server"}


# ---------------------------------------------------------------------------
# execute_tool_run — helpers
# ---------------------------------------------------------------------------


def _make_db_with_engagement(engagement: MagicMock | None) -> AsyncMock:
    """Return a generic mock AsyncSession.

    The §17.1 membership chokepoint (eng_repo.get_engagement_for_member) is patched
    directly in each test, so the session itself is only passed through to the
    patched repository calls and never executes a real query. The ``engagement``
    argument is retained for call-site readability.
    """
    return AsyncMock()


# ---------------------------------------------------------------------------
# execute_tool_run — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_run_happy_path() -> None:
    """execute_tool_run returns a ToolRunResult on success."""
    engagement_id = uuid4()
    user_id = uuid4()

    engagement = _make_engagement_mock()
    tool_run = _make_tool_run(engagement_id=engagement_id)
    raw = McpRawResult(exit_code=0, stdout="hello\n", stderr="")

    db = _make_db_with_engagement(engagement)

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=(_make_engagement_mock(), _make_member_mock()),
        ),
        patch(
            "app.features.mcp.service.get_registry",
            return_value=_make_registry(),
        ),
        patch(
            "app.features.mcp.service.mcp_repo.create_tool_run",
            new_callable=AsyncMock,
            return_value=tool_run,
        ),
        patch(
            "app.features.mcp.service.subprocess_manager.send_tool_call",
            new_callable=AsyncMock,
            return_value=raw,
        ),
        patch(
            "app.features.mcp.service.mcp_repo.update_tool_run_result",
            new_callable=AsyncMock,
            return_value=tool_run,
        ),
    ):
        result = await execute_tool_run(
            db,
            engagement_id=engagement_id,
            server_name=_SERVER_NAME,
            tool_name=_TOOL_NAME,
            args=_ARGS,
            timeout_seconds=_TIMEOUT,
            user_id=user_id,
        )

    assert isinstance(result, ToolRunResult)
    assert result.exit_code == 0
    assert result.stdout == "hello\n"
    assert result.engagement_id == engagement_id


@pytest.mark.asyncio
async def test_execute_tool_run_calls_send_tool_call_with_correct_args() -> None:
    """execute_tool_run forwards server_name, tool_name, args, timeout to send_tool_call."""
    engagement_id = uuid4()
    user_id = uuid4()
    custom_args = {"command": "ls -la", "cwd": "/tmp"}
    custom_timeout = 60

    engagement = _make_engagement_mock()
    tool_run = _make_tool_run(engagement_id=engagement_id)
    raw = McpRawResult(exit_code=0, stdout="output", stderr="")

    db = _make_db_with_engagement(engagement)

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
        ),
        patch(
            "app.features.mcp.service.subprocess_manager.send_tool_call",
            new_callable=AsyncMock,
            return_value=raw,
        ) as mock_send,
        patch(
            "app.features.mcp.service.mcp_repo.update_tool_run_result",
            new_callable=AsyncMock,
            return_value=tool_run,
        ),
    ):
        await execute_tool_run(
            db,
            engagement_id=engagement_id,
            server_name=_SERVER_NAME,
            tool_name=_TOOL_NAME,
            args=custom_args,
            timeout_seconds=custom_timeout,
            user_id=user_id,
        )

    mock_send.assert_called_once_with(
        server_name=_SERVER_NAME,
        tool_name=_TOOL_NAME,
        args=custom_args,
        timeout_seconds=float(custom_timeout),
    )


# ---------------------------------------------------------------------------
# execute_tool_run — EngagementNotFound
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_run_engagement_not_found() -> None:
    """execute_tool_run raises EngagementNotFound when the engagement does not exist."""
    db = _make_db_with_engagement(None)

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=None,  # engagement missing
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
        )


# ---------------------------------------------------------------------------
# execute_tool_run — non-member collapses to EngagementNotFound (§4 + §17.1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_run_non_member_raises_engagement_not_found() -> None:
    """A non-member receives EngagementNotFound (404), not a 403.

    get_engagement_for_member returns None for non-members; the service collapses
    that to EngagementNotFound so existence is not disclosed (§17.1).
    """
    db = _make_db_with_engagement(None)

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=None,  # not a member → indistinguishable from "missing"
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
        )


@pytest.mark.asyncio
async def test_execute_tool_run_admin_without_membership_raises_engagement_not_found() -> None:
    """Admin users without an explicit member row are denied (§4 no-bypass).

    The fused membership query never consults user.role, so an admin who is not a
    member gets None → EngagementNotFound, exactly like any other non-member. The
    denial is a 404 (not a 403) to avoid existence disclosure (§17.1).
    """
    db = _make_db_with_engagement(None)

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=None,  # admin has no explicit member row; role never checked
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
            user_id=uuid4(),  # even an admin user_id — service never checks role
        )


# ---------------------------------------------------------------------------
# execute_tool_run — McpServerNotFound (unknown server)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_run_unknown_server_raises_not_found() -> None:
    """execute_tool_run raises McpServerNotFound when the server is not in the registry."""
    engagement = _make_engagement_mock()
    db = _make_db_with_engagement(engagement)

    with (
        patch(
            "app.features.mcp.service.eng_repo.get_engagement_for_member",
            new_callable=AsyncMock,
            return_value=(_make_engagement_mock(), _make_member_mock()),
        ),
        patch("app.features.mcp.service.get_registry", return_value={}),  # empty registry
        pytest.raises(McpServerNotFound),
    ):
        await execute_tool_run(
            db,
            engagement_id=uuid4(),
            server_name="nonexistent-server",
            tool_name=_TOOL_NAME,
            args=_ARGS,
            timeout_seconds=_TIMEOUT,
            user_id=uuid4(),
        )


# ---------------------------------------------------------------------------
# execute_tool_run — McpServerDown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_tool_run_server_down_propagates() -> None:
    """execute_tool_run propagates McpServerDown from send_tool_call (→ 503 in router)."""
    engagement_id = uuid4()
    user_id = uuid4()

    engagement = _make_engagement_mock()
    tool_run = _make_tool_run(engagement_id=engagement_id)
    db = _make_db_with_engagement(engagement)

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
        ),
        patch(
            "app.features.mcp.service.subprocess_manager.send_tool_call",
            new_callable=AsyncMock,
            side_effect=McpServerDown("shell-exec is down"),
        ),
        pytest.raises(McpServerDown),
    ):
        await execute_tool_run(
            db,
            engagement_id=engagement_id,
            server_name=_SERVER_NAME,
            tool_name=_TOOL_NAME,
            args=_ARGS,
            timeout_seconds=_TIMEOUT,
            user_id=user_id,
        )


@pytest.mark.asyncio
async def test_execute_tool_run_server_down_does_not_update_row() -> None:
    """When McpServerDown is raised, update_tool_run_result is NOT called.

    The in-flight row is left with exit_code NULL (crash recovery handles cleanup
    at next startup per §13 / Slice 38).
    """
    engagement_id = uuid4()
    user_id = uuid4()

    engagement = _make_engagement_mock()
    tool_run = _make_tool_run(engagement_id=engagement_id)
    db = _make_db_with_engagement(engagement)

    update_mock = AsyncMock()

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
        ),
        patch(
            "app.features.mcp.service.subprocess_manager.send_tool_call",
            new_callable=AsyncMock,
            side_effect=McpServerDown("down"),
        ),
        patch(
            "app.features.mcp.service.mcp_repo.update_tool_run_result",
            update_mock,
        ),
        pytest.raises(McpServerDown),
    ):
        await execute_tool_run(
            db,
            engagement_id=engagement_id,
            server_name=_SERVER_NAME,
            tool_name=_TOOL_NAME,
            args=_ARGS,
            timeout_seconds=_TIMEOUT,
            user_id=user_id,
        )

    update_mock.assert_not_called()
