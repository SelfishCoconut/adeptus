"""Unit tests for app.features.mcp.subprocess_manager.

All subprocess interaction is mocked — no real processes are spawned.
The registry singleton and the manager handle table are reset between tests.
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.features.mcp.registry import _reset_registry, load_registry
from app.features.mcp.subprocess_manager import (
    McpRawResult,
    McpServerDown,
    McpServerNotFound,
    _handles,
    _reset_manager,
    get_server_status,
    send_tool_call,
    shutdown,
    startup,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VALID_YAML = textwrap.dedent(
    """\
    servers:
      - name: shell-exec
        command: python
        args:
          - -m
          - mcp_servers.shell_exec
        tools:
          - name: run_command
            weight: light
            capability_flags:
              - shell-exec
    """
)


@pytest.fixture(autouse=True)
def clean_state(tmp_path: Path) -> Generator[None, None, None]:
    """Reset registry and manager state before/after every test."""
    _reset_registry()
    _reset_manager()
    # Load a minimal registry so most tests have something to work with.
    p = tmp_path / "mcp.yaml"
    p.write_text(_VALID_YAML)
    load_registry(config_path=str(p))
    yield
    _reset_manager()
    _reset_registry()


# ---------------------------------------------------------------------------
# Helpers — fake subprocess
# ---------------------------------------------------------------------------


def _make_fake_process(
    *,
    stdout_lines: list[bytes] | None = None,
    pid: int = 12345,
    returncode: int | None = None,
) -> MagicMock:
    """Return a MagicMock that looks like asyncio.subprocess.Process.

    ``stdout.readline`` is an AsyncMock that pops from ``stdout_lines`` in order.
    ``stdin.write`` and ``stdin.drain`` are mocked for introspection.
    """
    process = MagicMock()
    process.pid = pid
    process.returncode = returncode

    # stdin
    stdin = MagicMock()
    stdin.write = MagicMock()
    stdin.drain = AsyncMock()
    process.stdin = stdin

    # stdout
    if stdout_lines is None:
        stdout_lines = []

    async def _readline() -> bytes:
        if stdout_lines:
            return stdout_lines.pop(0)
        return b""

    stdout = MagicMock()
    stdout.readline = _readline
    process.stdout = stdout

    # terminate / wait / kill
    process.terminate = MagicMock()
    process.kill = MagicMock()
    process.wait = AsyncMock(return_value=0)

    return process


def _make_result_line(
    *,
    exit_code: int = 0,
    stdout: str = "",
    stderr: str = "",
    id_: int = 1,
) -> bytes:
    response = {
        "jsonrpc": "2.0",
        "id": id_,
        "result": {"exit_code": exit_code, "stdout": stdout, "stderr": stderr},
    }
    return (json.dumps(response) + "\n").encode()


# ---------------------------------------------------------------------------
# startup / shutdown
# ---------------------------------------------------------------------------


class TestStartup:
    @pytest.mark.asyncio
    async def test_startup_spawns_configured_servers(self) -> None:
        fake_proc = _make_fake_process()
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        assert "shell-exec" in _handles
        assert _handles["shell-exec"].process is fake_proc

    @pytest.mark.asyncio
    async def test_startup_passes_command_and_args(self) -> None:
        fake_proc = _make_fake_process()
        mock_exec = AsyncMock(return_value=fake_proc)
        with patch("asyncio.create_subprocess_exec", new=mock_exec):
            await startup()

        args, kwargs = mock_exec.call_args
        assert args[0] == "python"
        assert args[1] == "-m"
        assert args[2] == "mcp_servers.shell_exec"
        assert kwargs.get("stdin") == asyncio.subprocess.PIPE
        assert kwargs.get("stdout") == asyncio.subprocess.PIPE
        assert kwargs.get("stderr") == asyncio.subprocess.PIPE

    @pytest.mark.asyncio
    async def test_startup_skips_already_running_server(self) -> None:
        fake_proc = _make_fake_process()
        mock_exec = AsyncMock(return_value=fake_proc)
        with patch("asyncio.create_subprocess_exec", new=mock_exec):
            await startup()
            await startup()  # second call

        assert mock_exec.call_count == 1  # only spawned once

    @pytest.mark.asyncio
    async def test_startup_marks_stopped_when_command_not_found(self) -> None:
        with patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(side_effect=FileNotFoundError("no such file")),
        ):
            await startup()  # should not raise

        assert get_server_status("shell-exec") == "stopped"


class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_terminates_running_servers(self) -> None:
        fake_proc = _make_fake_process()
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        await shutdown()

        fake_proc.terminate.assert_called_once()
        assert _handles == {}

    @pytest.mark.asyncio
    async def test_shutdown_is_safe_with_no_servers(self) -> None:
        await shutdown()  # should not raise


# ---------------------------------------------------------------------------
# get_server_status
# ---------------------------------------------------------------------------


class TestGetServerStatus:
    def test_unknown_server_returns_stopped(self) -> None:
        assert get_server_status("nonexistent") == "stopped"

    @pytest.mark.asyncio
    async def test_running_server_returns_running(self) -> None:
        fake_proc = _make_fake_process()
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        assert get_server_status("shell-exec") == "running"

    @pytest.mark.asyncio
    async def test_dead_process_returns_stopped(self) -> None:
        fake_proc = _make_fake_process(returncode=1)
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        assert get_server_status("shell-exec") == "stopped"


# ---------------------------------------------------------------------------
# send_tool_call — happy path
# ---------------------------------------------------------------------------


class TestSendToolCallHappyPath:
    @pytest.mark.asyncio
    async def test_returns_mcp_raw_result(self) -> None:
        response_line = _make_result_line(exit_code=0, stdout="hello", stderr="")
        fake_proc = _make_fake_process(stdout_lines=[response_line])
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()
        result = await send_tool_call("shell-exec", "run_command", {"cmd": "echo hello"}, 10.0)

        assert isinstance(result, McpRawResult)
        assert result.exit_code == 0
        assert result.stdout == "hello"
        assert result.stderr == ""

    @pytest.mark.asyncio
    async def test_serializes_jsonrpc_request_to_stdin(self) -> None:
        response_line = _make_result_line()
        fake_proc = _make_fake_process(stdout_lines=[response_line])
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        await send_tool_call("shell-exec", "run_command", {"cmd": "id"}, 5.0)

        fake_proc.stdin.write.assert_called_once()
        written_bytes: bytes = fake_proc.stdin.write.call_args[0][0]
        written_str = written_bytes.decode()
        assert written_str.endswith("\n"), "Request line must end with newline"
        request = json.loads(written_str)
        assert request["jsonrpc"] == "2.0"
        assert request["method"] == "tools/call"
        assert request["params"]["name"] == "run_command"
        assert request["params"]["arguments"] == {"cmd": "id"}

    @pytest.mark.asyncio
    async def test_drain_is_called_after_write(self) -> None:
        response_line = _make_result_line()
        fake_proc = _make_fake_process(stdout_lines=[response_line])
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        await send_tool_call("shell-exec", "run_command", {}, 5.0)

        fake_proc.stdin.drain.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_zero_exit_code_is_returned(self) -> None:
        response_line = _make_result_line(exit_code=127, stdout="", stderr="not found")
        fake_proc = _make_fake_process(stdout_lines=[response_line])
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        result = await send_tool_call("shell-exec", "run_command", {"cmd": "bogus"}, 5.0)

        assert result.exit_code == 127
        assert result.stderr == "not found"

    @pytest.mark.asyncio
    async def test_increments_request_id_per_call(self) -> None:
        written_bodies: list[bytes] = []

        fake_proc = _make_fake_process(
            stdout_lines=[
                _make_result_line(id_=1),
                _make_result_line(id_=2),
            ]
        )

        original_write = MagicMock(side_effect=lambda b: written_bodies.append(b))
        fake_proc.stdin.write = original_write

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        await send_tool_call("shell-exec", "run_command", {}, 5.0)
        await send_tool_call("shell-exec", "run_command", {}, 5.0)

        ids = [json.loads(b.decode())["id"] for b in written_bodies]
        assert ids == [1, 2]


# ---------------------------------------------------------------------------
# send_tool_call — McpServerNotFound
# ---------------------------------------------------------------------------


class TestSendToolCallServerNotFound:
    @pytest.mark.asyncio
    async def test_unknown_server_raises_not_found(self) -> None:
        with pytest.raises(McpServerNotFound, match="not in the registry"):
            await send_tool_call("nonexistent", "run_command", {}, 5.0)

    @pytest.mark.asyncio
    async def test_not_found_is_adeptus_error(self) -> None:
        from app.core.errors import AdeptusError

        with pytest.raises(AdeptusError):
            await send_tool_call("no-such-server", "run_command", {}, 5.0)


# ---------------------------------------------------------------------------
# send_tool_call — McpServerDown (process not running)
# ---------------------------------------------------------------------------


class TestSendToolCallServerDown:
    @pytest.mark.asyncio
    async def test_raises_when_startup_not_called(self) -> None:
        """Handle table is empty — server never started."""
        with pytest.raises(McpServerDown):
            await send_tool_call("shell-exec", "run_command", {}, 5.0)

    @pytest.mark.asyncio
    async def test_raises_when_process_has_died(self) -> None:
        """returncode is not None — process exited."""
        fake_proc = _make_fake_process(returncode=1)
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        with pytest.raises(McpServerDown):
            await send_tool_call("shell-exec", "run_command", {}, 5.0)

    @pytest.mark.asyncio
    async def test_subprocess_death_detected_via_empty_stdout(self) -> None:
        """readline() returns b'' — EOF on stdout pipe."""
        fake_proc = _make_fake_process(stdout_lines=[b""])  # empty = EOF
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        with pytest.raises(McpServerDown, match="closed stdout"):
            await send_tool_call("shell-exec", "run_command", {}, 5.0)

    @pytest.mark.asyncio
    async def test_subprocess_death_marks_server_stopped(self) -> None:
        """After McpServerDown due to EOF, status should be stopped."""
        fake_proc = _make_fake_process(stdout_lines=[b""])
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        with pytest.raises(McpServerDown):
            await send_tool_call("shell-exec", "run_command", {}, 5.0)

        assert get_server_status("shell-exec") == "stopped"

    @pytest.mark.asyncio
    async def test_jsonrpc_error_response_raises_server_down(self) -> None:
        error_response = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32600, "message": "Invalid Request"},
                }
            ).encode()
            + b"\n"
        )
        fake_proc = _make_fake_process(stdout_lines=[error_response])
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        with pytest.raises(McpServerDown, match="JSON-RPC error"):
            await send_tool_call("shell-exec", "run_command", {}, 5.0)


# ---------------------------------------------------------------------------
# send_tool_call — timeout
# ---------------------------------------------------------------------------


class TestSendToolCallTimeout:
    @pytest.mark.asyncio
    async def test_timeout_raises_mcp_server_down(self) -> None:
        async def _slow_readline() -> bytes:
            await asyncio.sleep(10)
            return b""

        fake_proc = _make_fake_process()
        fake_proc.stdout.readline = _slow_readline

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        with pytest.raises(McpServerDown, match="timed out"):
            await send_tool_call("shell-exec", "run_command", {}, timeout_seconds=0.01)

    @pytest.mark.asyncio
    async def test_timeout_includes_timeout_value_in_message(self) -> None:
        async def _slow_readline() -> bytes:
            await asyncio.sleep(10)
            return b""

        fake_proc = _make_fake_process()
        fake_proc.stdout.readline = _slow_readline

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        with pytest.raises(McpServerDown, match="0.01"):
            await send_tool_call("shell-exec", "run_command", {}, timeout_seconds=0.01)

    @pytest.mark.asyncio
    async def test_timeout_marks_server_stopped(self) -> None:
        async def _slow_readline() -> bytes:
            await asyncio.sleep(10)
            return b""

        fake_proc = _make_fake_process()
        fake_proc.stdout.readline = _slow_readline

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        with pytest.raises(McpServerDown):
            await send_tool_call("shell-exec", "run_command", {}, timeout_seconds=0.01)

        assert get_server_status("shell-exec") == "stopped"

    @pytest.mark.asyncio
    async def test_timeout_is_adeptus_error(self) -> None:
        from app.core.errors import AdeptusError

        async def _slow_readline() -> bytes:
            await asyncio.sleep(10)
            return b""

        fake_proc = _make_fake_process()
        fake_proc.stdout.readline = _slow_readline

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        with pytest.raises(AdeptusError):
            await send_tool_call("shell-exec", "run_command", {}, timeout_seconds=0.01)
