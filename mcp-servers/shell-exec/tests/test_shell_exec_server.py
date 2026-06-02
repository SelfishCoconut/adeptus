"""Unit tests for the shell-exec MCP server.

Tests mock asyncio.create_subprocess_shell to avoid spawning real processes.
All tests exercise the internal coroutines directly (no stdin/stdout wiring
needed for unit testing).
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add mcp-servers/shell-exec to the path so we can import server directly.
_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from server import (  # noqa: E402
    JSONRPC_INVALID_REQUEST,
    JSONRPC_PARSE_ERROR,
    MAX_OUTPUT_BYTES,
    TRUNCATION_SENTINEL,
    _handle_request,
    _run_command,
    _truncate,
    main,
)

# ---------------------------------------------------------------------------
# Helper: build a fake subprocess with configurable stdout/stderr/returncode
# ---------------------------------------------------------------------------


def _make_mock_process(
    stdout: bytes = b"",
    stderr: bytes = b"",
    returncode: int = 0,
) -> MagicMock:
    """Return a mock that mimics asyncio.subprocess.Process."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)
    return proc


# ---------------------------------------------------------------------------
# _truncate
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_short_output_unchanged(self) -> None:
        data = b"hello world"
        assert _truncate(data) == "hello world"

    def test_exactly_at_limit_unchanged(self) -> None:
        data = b"x" * MAX_OUTPUT_BYTES
        result = _truncate(data)
        assert not result.endswith(TRUNCATION_SENTINEL)
        assert len(result) == MAX_OUTPUT_BYTES

    def test_one_byte_over_limit_truncated(self) -> None:
        data = b"x" * (MAX_OUTPUT_BYTES + 1)
        result = _truncate(data)
        assert result.endswith(TRUNCATION_SENTINEL)
        # First MAX_OUTPUT_BYTES bytes should be "x" repeated
        prefix = result[:MAX_OUTPUT_BYTES]
        assert prefix == "x" * MAX_OUTPUT_BYTES

    def test_large_output_truncated_with_sentinel(self) -> None:
        data = b"A" * (MAX_OUTPUT_BYTES * 2)
        result = _truncate(data)
        assert result.endswith(TRUNCATION_SENTINEL)
        assert result.startswith("A" * MAX_OUTPUT_BYTES)


# ---------------------------------------------------------------------------
# _run_command
# ---------------------------------------------------------------------------


class TestRunCommand:
    @pytest.mark.asyncio
    async def test_happy_path_exit_zero(self) -> None:
        mock_proc = _make_mock_process(stdout=b"hello\n", stderr=b"", returncode=0)
        with patch("server.asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_shell:
            mock_shell.return_value = mock_proc
            result = await _run_command({"command": "echo hello"})

        assert result["exit_code"] == 0
        assert result["stdout"] == "hello\n"
        assert result["stderr"] == ""
        mock_shell.assert_called_once_with(
            "echo hello",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

    @pytest.mark.asyncio
    async def test_non_zero_exit_code_returned_not_raised(self) -> None:
        mock_proc = _make_mock_process(stdout=b"", stderr=b"not found\n", returncode=127)
        with patch("server.asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_shell:
            mock_shell.return_value = mock_proc
            result = await _run_command({"command": "nonexistent_command_xyz"})

        # Non-zero exit code RETURNED, not raised
        assert result["exit_code"] == 127
        assert result["stderr"] == "not found\n"

    @pytest.mark.asyncio
    async def test_missing_command_returns_error_result(self) -> None:
        result = await _run_command({})
        assert result["exit_code"] == 1
        assert "command" in result["stderr"].lower()

    @pytest.mark.asyncio
    async def test_empty_command_returns_error_result(self) -> None:
        result = await _run_command({"command": ""})
        assert result["exit_code"] == 1
        assert "command" in result["stderr"].lower()

    @pytest.mark.asyncio
    async def test_stdout_truncated_at_1mb(self) -> None:
        big_stdout = b"B" * (MAX_OUTPUT_BYTES + 100)
        mock_proc = _make_mock_process(stdout=big_stdout, stderr=b"", returncode=0)
        with patch("server.asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_shell:
            mock_shell.return_value = mock_proc
            result = await _run_command({"command": "cat /dev/urandom"})

        assert result["exit_code"] == 0
        assert result["stdout"].endswith(TRUNCATION_SENTINEL)
        assert result["stdout"].startswith("B" * MAX_OUTPUT_BYTES)
        assert result["stderr"] == ""

    @pytest.mark.asyncio
    async def test_stderr_truncated_at_1mb(self) -> None:
        big_stderr = b"E" * (MAX_OUTPUT_BYTES + 50)
        mock_proc = _make_mock_process(stdout=b"", stderr=big_stderr, returncode=1)
        with patch("server.asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_shell:
            mock_shell.return_value = mock_proc
            result = await _run_command({"command": "some_failing_cmd"})

        assert result["exit_code"] == 1
        assert result["stderr"].endswith(TRUNCATION_SENTINEL)
        assert result["stderr"].startswith("E" * MAX_OUTPUT_BYTES)
        assert result["stdout"] == ""

    @pytest.mark.asyncio
    async def test_timeout_seconds_override_honoured(self) -> None:
        """timeout_seconds is forwarded to asyncio.wait_for; when it fires,
        exit_code 124 is returned with a timeout message in stderr.
        The entire process group is killed via os.killpg."""

        async def _slow_communicate() -> tuple[bytes, bytes]:
            await asyncio.sleep(9999)
            return b"", b""

        mock_proc = MagicMock()
        mock_proc.pid = 42000
        mock_proc.returncode = None
        mock_proc.communicate = _slow_communicate
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=None)

        with patch("server.asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_shell:
            # Patch wait_for to immediately raise TimeoutError when timeout=1
            original_wait_for = asyncio.wait_for

            # noqa target: this helper deliberately mirrors asyncio.wait_for's
            # (coro, timeout) signature so it can monkeypatch it.
            async def _patched_wait_for(coro: Any, timeout: float) -> Any:  # noqa: ASYNC109
                if timeout <= 1:
                    # Cancel the coroutine to avoid resource leaks
                    coro.close()
                    raise TimeoutError
                return await original_wait_for(coro, timeout)

            mock_shell.return_value = mock_proc
            with (
                patch("server.asyncio.wait_for", side_effect=_patched_wait_for),
                patch("server.os.getpgid", return_value=42000) as mock_getpgid,
                patch("server.os.killpg") as mock_killpg,
            ):
                result = await _run_command({"command": "sleep 9999", "timeout_seconds": 1})

        assert result["exit_code"] == 124
        assert "timed out" in result["stderr"]
        assert result["stdout"] == ""
        # Verify process group kill was attempted.
        mock_getpgid.assert_called_once_with(42000)
        mock_killpg.assert_called_once_with(42000, signal.SIGKILL)

    @pytest.mark.asyncio
    async def test_default_timeout_is_30_seconds(self) -> None:
        """Default timeout_seconds = 30 is passed to asyncio.wait_for."""
        mock_proc = _make_mock_process(stdout=b"done\n", stderr=b"", returncode=0)

        captured_timeouts: list[float] = []
        original_wait_for = asyncio.wait_for

        # Mirrors asyncio.wait_for's (coro, timeout) signature for monkeypatching.
        async def _capturing_wait_for(coro: Any, timeout: float) -> Any:  # noqa: ASYNC109
            captured_timeouts.append(timeout)
            return await original_wait_for(coro, timeout)

        with patch("server.asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_shell:
            mock_shell.return_value = mock_proc
            with patch("server.asyncio.wait_for", side_effect=_capturing_wait_for):
                await _run_command({"command": "echo done"})

        assert captured_timeouts == [30.0]


# ---------------------------------------------------------------------------
# _handle_request (JSON-RPC dispatch)
# ---------------------------------------------------------------------------


class TestHandleRequest:
    @pytest.mark.asyncio
    async def test_run_command_returns_correct_response_shape(self) -> None:
        mock_proc = _make_mock_process(stdout=b"hello\n", stderr=b"", returncode=0)
        with patch("server.asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_shell:
            mock_shell.return_value = mock_proc
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "run_command", "arguments": {"command": "echo hello"}},
            }
            response = await _handle_request(request)

        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        assert "result" in response
        assert "error" not in response
        result = response["result"]
        assert result["exit_code"] == 0
        assert result["stdout"] == "hello\n"
        assert result["stderr"] == ""

    @pytest.mark.asyncio
    async def test_unknown_tool_name_returns_method_not_found(self) -> None:
        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        }
        response = await _handle_request(request)

        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 2
        assert "error" in response
        assert "result" not in response
        error = response["error"]
        assert error["code"] == -32601  # JSON-RPC method-not-found

    @pytest.mark.asyncio
    async def test_unknown_method_returns_method_not_found(self) -> None:
        request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "some/other/method",
            "params": {},
        }
        response = await _handle_request(request)

        assert "error" in response
        assert response["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_non_zero_exit_is_in_result_not_error(self) -> None:
        """A non-zero exit code is returned in result.exit_code, not as a
        JSON-RPC error — the transport succeeded, the command failed."""
        mock_proc = _make_mock_process(stdout=b"", stderr=b"oops\n", returncode=1)
        with patch("server.asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_shell:
            mock_shell.return_value = mock_proc
            request = {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "run_command", "arguments": {"command": "false"}},
            }
            response = await _handle_request(request)

        assert "result" in response
        assert "error" not in response
        assert response["result"]["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_response_is_json_serializable(self) -> None:
        mock_proc = _make_mock_process(stdout=b"out\n", stderr=b"", returncode=0)
        with patch("server.asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_shell:
            mock_shell.return_value = mock_proc
            request = {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "run_command", "arguments": {"command": "echo out"}},
            }
            response = await _handle_request(request)

        # Must serialize to a single JSON line (no newlines within the payload)
        serialized = json.dumps(response)
        assert "\n" not in serialized

    @pytest.mark.asyncio
    async def test_id_preserved_in_response(self) -> None:
        """The response id must match the request id (JSON-RPC contract)."""
        mock_proc = _make_mock_process(stdout=b"", stderr=b"", returncode=0)
        with patch("server.asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_shell:
            mock_shell.return_value = mock_proc
            for req_id in [42, "abc", None]:
                request = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "tools/call",
                    "params": {"name": "run_command", "arguments": {"command": "true"}},
                }
                response = await _handle_request(request)
                assert response["id"] == req_id

    @pytest.mark.asyncio
    async def test_non_dict_params_treated_as_empty(self) -> None:
        """When params is not a dict, the server normalises it to {} and
        proceeds — this exercises the ``if not isinstance(params, dict)``
        branch (line 126 in server.py)."""
        request = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": "not-a-dict",
        }
        response = await _handle_request(request)
        # params becomes {}, so tool_name is None → tool not found error
        assert "error" in response
        assert response["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_non_dict_arguments_treated_as_empty(self) -> None:
        """When arguments is not a dict, it's normalised to {} — exercises
        the ``if not isinstance(arguments, dict)`` branch (line 142)."""
        mock_proc = _make_mock_process(stdout=b"hi\n", stderr=b"", returncode=0)
        with patch("server.asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_shell:
            mock_shell.return_value = mock_proc
            request = {
                "jsonrpc": "2.0",
                "id": 8,
                "method": "tools/call",
                "params": {"name": "run_command", "arguments": "not-a-dict"},
            }
            response = await _handle_request(request)
        # arguments becomes {} → missing command → exit_code 1 result (not JSON-RPC error)
        assert "result" in response
        assert response["result"]["exit_code"] == 1


# ---------------------------------------------------------------------------
# _run_command — additional error-path coverage
# ---------------------------------------------------------------------------


class TestRunCommandErrorPaths:
    @pytest.mark.asyncio
    async def test_oserror_on_subprocess_start_returns_error_result(self) -> None:
        """If asyncio.create_subprocess_shell raises OSError, a structured
        error dict is returned instead of propagating the exception."""
        with patch(
            "server.asyncio.create_subprocess_shell",
            new_callable=AsyncMock,
            side_effect=OSError("permission denied"),
        ):
            result = await _run_command({"command": "restricted_cmd"})

        assert result["exit_code"] == 1
        assert result["stdout"] == ""
        assert "Failed to start subprocess" in result["stderr"]

    @pytest.mark.asyncio
    async def test_processlookuperror_on_killpg_is_swallowed(self) -> None:
        """If os.getpgid raises ProcessLookupError (process already gone),
        the timeout path still returns exit_code 124."""

        async def _never_finishes() -> tuple[bytes, bytes]:
            await asyncio.sleep(9999)
            return b"", b""

        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.returncode = None
        mock_proc.communicate = _never_finishes
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=None)

        original_wait_for = asyncio.wait_for

        async def _patched_wait_for(coro: Any, timeout: float) -> Any:  # noqa: ASYNC109
            if timeout <= 1:
                coro.close()
                raise TimeoutError
            return await original_wait_for(coro, timeout)

        with patch("server.asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_shell:
            mock_shell.return_value = mock_proc
            with (
                patch("server.asyncio.wait_for", side_effect=_patched_wait_for),
                patch("server.os.getpgid", side_effect=ProcessLookupError),
            ):
                result = await _run_command({"command": "sleep 9999", "timeout_seconds": 1})

        assert result["exit_code"] == 124
        assert "timed out" in result["stderr"]

    @pytest.mark.asyncio
    async def test_fallback_kill_called_when_killpg_raises_generic_oserror(self) -> None:
        """If os.killpg raises a generic OSError (e.g. unsupported platform),
        process.kill() is called as a fallback and exit_code 124 is returned."""

        async def _never_finishes() -> tuple[bytes, bytes]:
            await asyncio.sleep(9999)
            return b"", b""

        mock_proc = MagicMock()
        mock_proc.pid = 99998
        mock_proc.returncode = None
        mock_proc.communicate = _never_finishes
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=None)

        original_wait_for = asyncio.wait_for

        async def _patched_wait_for(coro: Any, timeout: float) -> Any:  # noqa: ASYNC109
            if timeout <= 1:
                coro.close()
                raise TimeoutError
            return await original_wait_for(coro, timeout)

        with patch("server.asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_shell:
            mock_shell.return_value = mock_proc
            with (
                patch("server.asyncio.wait_for", side_effect=_patched_wait_for),
                patch("server.os.getpgid", return_value=99998),
                patch("server.os.killpg", side_effect=OSError("operation not permitted")),
            ):
                result = await _run_command({"command": "sleep 9999", "timeout_seconds": 1})

        assert result["exit_code"] == 124
        assert "timed out" in result["stderr"]
        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_exception_on_process_wait_after_timeout_is_swallowed(self) -> None:
        """If process.wait() raises after a timeout, the exception is swallowed
        and exit_code 124 is still returned."""

        async def _never_finishes() -> tuple[bytes, bytes]:
            await asyncio.sleep(9999)
            return b"", b""

        mock_proc = MagicMock()
        mock_proc.pid = 99997
        mock_proc.returncode = None
        mock_proc.communicate = _never_finishes
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(side_effect=RuntimeError("wait failed"))

        original_wait_for = asyncio.wait_for

        async def _patched_wait_for(coro: Any, timeout: float) -> Any:  # noqa: ASYNC109
            if timeout <= 1:
                coro.close()
                raise TimeoutError
            return await original_wait_for(coro, timeout)

        with patch("server.asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_shell:
            mock_shell.return_value = mock_proc
            with (
                patch("server.asyncio.wait_for", side_effect=_patched_wait_for),
                patch("server.os.getpgid", return_value=99997),
                patch("server.os.killpg"),
            ):
                result = await _run_command({"command": "sleep 9999", "timeout_seconds": 1})

        assert result["exit_code"] == 124
        assert "timed out" in result["stderr"]


# ---------------------------------------------------------------------------
# main() — stdin/stdout loop coverage
# ---------------------------------------------------------------------------


def _make_main_mocks(
    lines: list[bytes],
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Build mocks for the main() event-loop plumbing.

    Returns:
        (mock_loop, mock_reader, mock_write_transport)
    """
    mock_reader = MagicMock()

    # readline() returns each line in sequence, then b"" to signal EOF
    side_effects = list(lines) + [b""]
    mock_reader.readline = AsyncMock(side_effect=side_effects)

    mock_write_transport = MagicMock()
    mock_write_protocol = MagicMock()

    mock_loop = MagicMock()
    mock_loop.connect_read_pipe = AsyncMock(return_value=(MagicMock(), MagicMock()))
    mock_loop.connect_write_pipe = AsyncMock(
        return_value=(mock_write_transport, mock_write_protocol)
    )

    return mock_loop, mock_reader, mock_write_transport


class TestMainLoop:
    @pytest.mark.asyncio
    async def test_valid_request_writes_response_line(self) -> None:
        """A well-formed tools/call request goes through the main loop and
        produces exactly one newline-terminated JSON response on stdout."""
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "run_command", "arguments": {"command": "echo hi"}},
        }
        line = (json.dumps(request) + "\n").encode()

        mock_loop, mock_reader, mock_write_transport = _make_main_mocks([line])
        mock_proc = _make_mock_process(stdout=b"hi\n", stderr=b"", returncode=0)

        with (
            patch("server.asyncio.get_event_loop", return_value=mock_loop),
            patch("server.asyncio.StreamReader", return_value=mock_reader),
            patch("server.asyncio.StreamReaderProtocol"),
            patch("server.asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_shell,
        ):
            mock_shell.return_value = mock_proc
            await main()

        assert mock_write_transport.write.call_count == 1
        written_bytes: bytes = mock_write_transport.write.call_args[0][0]
        assert written_bytes.endswith(b"\n")
        response = json.loads(written_bytes.decode())
        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        assert "result" in response
        assert response["result"]["exit_code"] == 0
        assert response["result"]["stdout"] == "hi\n"

    @pytest.mark.asyncio
    async def test_invalid_json_line_writes_parse_error(self) -> None:
        """A malformed JSON line produces a JSON-RPC parse error (code -32700)."""
        mock_loop, mock_reader, mock_write_transport = _make_main_mocks([b"not valid json\n"])

        with (
            patch("server.asyncio.get_event_loop", return_value=mock_loop),
            patch("server.asyncio.StreamReader", return_value=mock_reader),
            patch("server.asyncio.StreamReaderProtocol"),
        ):
            await main()

        assert mock_write_transport.write.call_count == 1
        written_bytes = mock_write_transport.write.call_args[0][0]
        response = json.loads(written_bytes.decode().strip())
        assert response["jsonrpc"] == "2.0"
        assert response["id"] is None
        assert response["error"]["code"] == JSONRPC_PARSE_ERROR

    @pytest.mark.asyncio
    async def test_non_dict_json_writes_invalid_request_error(self) -> None:
        """A valid JSON line that is not an object (e.g. a JSON array) produces
        a JSON-RPC invalid-request error (code -32600)."""
        mock_loop, mock_reader, mock_write_transport = _make_main_mocks(
            [(json.dumps([1, 2, 3]) + "\n").encode()]
        )

        with (
            patch("server.asyncio.get_event_loop", return_value=mock_loop),
            patch("server.asyncio.StreamReader", return_value=mock_reader),
            patch("server.asyncio.StreamReaderProtocol"),
        ):
            await main()

        assert mock_write_transport.write.call_count == 1
        written_bytes = mock_write_transport.write.call_args[0][0]
        response = json.loads(written_bytes.decode().strip())
        assert response["error"]["code"] == JSONRPC_INVALID_REQUEST

    @pytest.mark.asyncio
    async def test_empty_line_is_skipped(self) -> None:
        """An empty/whitespace-only line is silently skipped (no response written)."""
        mock_loop, mock_reader, mock_write_transport = _make_main_mocks([b"\n", b"   \n"])

        with (
            patch("server.asyncio.get_event_loop", return_value=mock_loop),
            patch("server.asyncio.StreamReader", return_value=mock_reader),
            patch("server.asyncio.StreamReaderProtocol"),
        ):
            await main()

        mock_write_transport.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_eof_exits_cleanly(self) -> None:
        """When stdin closes (readline returns b''), main() exits without error."""
        mock_loop, mock_reader, mock_write_transport = _make_main_mocks([])  # immediate EOF

        with (
            patch("server.asyncio.get_event_loop", return_value=mock_loop),
            patch("server.asyncio.StreamReader", return_value=mock_reader),
            patch("server.asyncio.StreamReaderProtocol"),
        ):
            await main()  # must return, not raise

        mock_write_transport.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_readline_exception_exits_cleanly(self) -> None:
        """If readline() raises an unexpected exception, main() exits without
        propagating it (exercises the bare except in the read loop)."""
        mock_reader = MagicMock()
        mock_reader.readline = AsyncMock(side_effect=RuntimeError("pipe broken"))

        mock_write_transport = MagicMock()
        mock_loop = MagicMock()
        mock_loop.connect_read_pipe = AsyncMock(return_value=(MagicMock(), MagicMock()))
        mock_loop.connect_write_pipe = AsyncMock(return_value=(mock_write_transport, MagicMock()))

        with (
            patch("server.asyncio.get_event_loop", return_value=mock_loop),
            patch("server.asyncio.StreamReader", return_value=mock_reader),
            patch("server.asyncio.StreamReaderProtocol"),
        ):
            await main()  # must return, not raise

        mock_write_transport.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_requests_processed_in_order(self) -> None:
        """Multiple valid requests are processed in sequence; each produces
        one response line."""
        req1 = {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "run_command", "arguments": {"command": "echo a"}},
        }
        req2 = {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "run_command", "arguments": {"command": "echo b"}},
        }
        lines = [
            (json.dumps(req1) + "\n").encode(),
            (json.dumps(req2) + "\n").encode(),
        ]
        mock_loop, mock_reader, mock_write_transport = _make_main_mocks(lines)

        proc_a = _make_mock_process(stdout=b"a\n", stderr=b"", returncode=0)
        proc_b = _make_mock_process(stdout=b"b\n", stderr=b"", returncode=0)

        with (
            patch("server.asyncio.get_event_loop", return_value=mock_loop),
            patch("server.asyncio.StreamReader", return_value=mock_reader),
            patch("server.asyncio.StreamReaderProtocol"),
            patch(
                "server.asyncio.create_subprocess_shell",
                new_callable=AsyncMock,
                side_effect=[proc_a, proc_b],
            ),
        ):
            await main()

        assert mock_write_transport.write.call_count == 2
        resp1 = json.loads(mock_write_transport.write.call_args_list[0][0][0].decode().strip())
        resp2 = json.loads(mock_write_transport.write.call_args_list[1][0][0].decode().strip())
        assert resp1["id"] == 10
        assert resp1["result"]["stdout"] == "a\n"
        assert resp2["id"] == 11
        assert resp2["result"]["stdout"] == "b\n"
