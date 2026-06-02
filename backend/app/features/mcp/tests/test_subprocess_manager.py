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
    _TIMEOUT_MARGIN_SECONDS,
    McpRawResult,
    McpServerDown,
    McpServerNotFound,
    McpToolNotFound,
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
    """Timeout tests patch _TIMEOUT_MARGIN_SECONDS to 0.0 so the outer deadline
    equals the caller's timeout_seconds and tests don't need to wait 5+ seconds."""

    @pytest.mark.asyncio
    async def test_timeout_raises_mcp_server_down(self) -> None:
        async def _slow_readline() -> bytes:
            await asyncio.sleep(10)
            return b""

        fake_proc = _make_fake_process()
        fake_proc.stdout.readline = _slow_readline

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        with patch("app.features.mcp.subprocess_manager._TIMEOUT_MARGIN_SECONDS", 0.0):
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

        with patch("app.features.mcp.subprocess_manager._TIMEOUT_MARGIN_SECONDS", 0.0):
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

        with patch("app.features.mcp.subprocess_manager._TIMEOUT_MARGIN_SECONDS", 0.0):
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

        with patch("app.features.mcp.subprocess_manager._TIMEOUT_MARGIN_SECONDS", 0.0):
            with pytest.raises(AdeptusError):
                await send_tool_call("shell-exec", "run_command", {}, timeout_seconds=0.01)

    @pytest.mark.asyncio
    async def test_timeout_margin_gives_outer_deadline_above_inner(self) -> None:
        """Outer timeout = timeout_seconds + _TIMEOUT_MARGIN_SECONDS.

        Verify that when _TIMEOUT_MARGIN_SECONDS > 0 the outer deadline is larger
        than timeout_seconds, meaning a _slow_readline that fires before the margin
        expires does NOT raise McpServerDown (the margin is working).

        We simulate this by making readline sleep for timeout_seconds * 2 seconds
        but less than timeout_seconds + margin.  With margin=0 it times out; with
        the real margin it succeeds (if the margin exceeds the sleep duration).
        This test just asserts the constant exists and is positive.
        """
        assert _TIMEOUT_MARGIN_SECONDS > 0


# ---------------------------------------------------------------------------
# FIX 1: large stdout line (>64 KB default limit) parsed successfully
# ---------------------------------------------------------------------------


class TestLargeResponseLine:
    @pytest.mark.asyncio
    async def test_response_line_larger_than_64kb_is_parsed(self) -> None:
        """A JSON-RPC response line larger than the asyncio default 64 KB
        StreamReader limit (but within _STDOUT_LIMIT_BYTES) must be parsed
        without raising ValueError/LimitOverrunError.

        We fake the readline by returning a 200 KB response line directly
        from the mock (bypassing the StreamReader limit — the limit is only
        enforced by the real asyncio subprocess, not our mock).  The important
        thing is that send_tool_call does NOT wrap ValueError as McpServerDown
        for a valid large line coming from readline (that path is only for the
        LimitOverrunError case).  This test confirms the happy path works with
        a large payload.
        """
        # Build a ~200 KB stdout payload embedded in a JSON-RPC result.
        large_stdout = "X" * (200 * 1024)
        response_line = _make_result_line(exit_code=0, stdout=large_stdout, stderr="")
        assert len(response_line) > 64 * 1024, "Sanity: response line must exceed 64 KB"

        fake_proc = _make_fake_process(stdout_lines=[response_line])
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        result = await send_tool_call("shell-exec", "run_command", {"cmd": "cat big"}, 10.0)

        assert result.exit_code == 0
        assert result.stdout == large_stdout
        assert len(result.stdout) == 200 * 1024

    @pytest.mark.asyncio
    async def test_value_error_from_readline_raises_mcp_server_down(self) -> None:
        """If readline() raises ValueError (LimitOverrunError subclass), it is
        caught and raised as McpServerDown WITHOUT calling _mark_stopped
        (the subprocess is still alive).
        """

        async def _overlong_readline() -> bytes:
            raise ValueError("Separator is not found, and chunk exceed the limit")

        fake_proc = _make_fake_process()
        fake_proc.stdout.readline = _overlong_readline

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        with pytest.raises(McpServerDown, match="exceeded StreamReader limit"):
            await send_tool_call("shell-exec", "run_command", {}, timeout_seconds=5.0)

        # The server is NOT marked stopped — the subprocess is still alive.
        assert get_server_status("shell-exec") == "running"


# ---------------------------------------------------------------------------
# FIX 2: per-server lock prevents response cross-talk; id mismatch → McpServerDown
# ---------------------------------------------------------------------------


class TestConcurrencyLock:
    @pytest.mark.asyncio
    async def test_concurrent_calls_each_get_their_own_response(self) -> None:
        """Two concurrent send_tool_call coroutines must each receive the
        response for their own request (no swap).

        We drive the fake readline with a delay so both coroutines arrive at
        the locked section before either completes.  Because the lock serialises
        them, the first caller gets id=1 and the second gets id=2 — matching
        the responses we provide in order.
        """
        # Responses in order: id=1 first, id=2 second.
        response_1 = _make_result_line(exit_code=0, stdout="first", id_=1)
        response_2 = _make_result_line(exit_code=0, stdout="second", id_=2)

        # readline alternates between the two responses, with a tiny delay so
        # both coroutines reach the lock before either acquires it.
        responses = [response_1, response_2]
        call_index = 0

        async def _ordered_readline() -> bytes:
            nonlocal call_index
            idx = call_index
            call_index += 1
            await asyncio.sleep(0)  # yield so both coroutines are started
            return responses[idx]

        fake_proc = _make_fake_process()
        fake_proc.stdout.readline = _ordered_readline

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        result_1, result_2 = await asyncio.gather(
            send_tool_call("shell-exec", "run_command", {}, 10.0),
            send_tool_call("shell-exec", "run_command", {}, 10.0),
        )

        # Each caller receives its own response, not the other's.
        assert result_1.stdout == "first"
        assert result_2.stdout == "second"

    @pytest.mark.asyncio
    async def test_response_id_mismatch_raises_mcp_server_down(self) -> None:
        """If the parsed response id does not match the request id, McpServerDown
        is raised and the server is marked stopped (desync detected).
        """
        # Return a response with id=99 while the request will have id=1.
        mismatched_line = _make_result_line(exit_code=0, stdout="oops", id_=99)
        fake_proc = _make_fake_process(stdout_lines=[mismatched_line])

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        with pytest.raises(McpServerDown, match="id mismatch"):
            await send_tool_call("shell-exec", "run_command", {}, 10.0)

        # Desync → server is marked stopped.
        assert get_server_status("shell-exec") == "stopped"


# ---------------------------------------------------------------------------
# FIX 3: JSON-RPC -32601 raises McpToolNotFound (not McpServerDown)
# ---------------------------------------------------------------------------


class TestMcpToolNotFound:
    @pytest.mark.asyncio
    async def test_jsonrpc_32601_raises_mcp_tool_not_found(self) -> None:
        """-32601 (method/tool not found) is a client error → McpToolNotFound."""
        error_response = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32601, "message": "Tool not found: 'bad_tool'"},
                }
            ).encode()
            + b"\n"
        )
        fake_proc = _make_fake_process(stdout_lines=[error_response])
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        with pytest.raises(McpToolNotFound, match="bad_tool"):
            await send_tool_call("shell-exec", "bad_tool", {}, 5.0)

    @pytest.mark.asyncio
    async def test_jsonrpc_32602_raises_mcp_tool_not_found(self) -> None:
        """-32602 (invalid params) is also a client error → McpToolNotFound."""
        error_response = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32602, "message": "Invalid params"},
                }
            ).encode()
            + b"\n"
        )
        fake_proc = _make_fake_process(stdout_lines=[error_response])
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        with pytest.raises(McpToolNotFound):
            await send_tool_call("shell-exec", "run_command", {}, 5.0)

    @pytest.mark.asyncio
    async def test_other_jsonrpc_error_codes_still_raise_mcp_server_down(self) -> None:
        """JSON-RPC codes outside the not-found set remain McpServerDown."""
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

        with pytest.raises(McpServerDown):
            await send_tool_call("shell-exec", "run_command", {}, 5.0)

    @pytest.mark.asyncio
    async def test_mcp_tool_not_found_is_adeptus_error(self) -> None:
        from app.core.errors import AdeptusError

        error_response = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32601, "message": "no such tool"},
                }
            ).encode()
            + b"\n"
        )
        fake_proc = _make_fake_process(stdout_lines=[error_response])
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            await startup()

        with pytest.raises(AdeptusError):
            await send_tool_call("shell-exec", "bad_tool", {}, 5.0)
