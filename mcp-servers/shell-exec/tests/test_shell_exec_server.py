"""Unit tests for the shell-exec MCP server.

Tests mock asyncio.create_subprocess_shell to avoid spawning real processes.
All tests exercise the internal coroutines directly (no stdin/stdout wiring
needed for unit testing).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sys
import os

# Add mcp-servers/shell-exec to the path so we can import server directly.
_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from server import (  # noqa: E402
    MAX_OUTPUT_BYTES,
    TRUNCATION_SENTINEL,
    _handle_request,
    _run_command,
    _truncate,
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
        prefix = result[: MAX_OUTPUT_BYTES]
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
        exit_code 124 is returned with a timeout message in stderr."""

        async def _slow_communicate() -> tuple[bytes, bytes]:
            await asyncio.sleep(9999)
            return b"", b""

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.communicate = _slow_communicate
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(return_value=None)

        with patch("server.asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_shell:
            # Patch wait_for to immediately raise TimeoutError when timeout=1
            original_wait_for = asyncio.wait_for

            async def _patched_wait_for(coro: Any, timeout: float) -> Any:
                if timeout <= 1:
                    # Cancel the coroutine to avoid resource leaks
                    coro.close()
                    raise TimeoutError
                return await original_wait_for(coro, timeout)

            mock_shell.return_value = mock_proc
            with patch("server.asyncio.wait_for", side_effect=_patched_wait_for):
                result = await _run_command({"command": "sleep 9999", "timeout_seconds": 1})

        assert result["exit_code"] == 124
        assert "timed out" in result["stderr"]
        assert result["stdout"] == ""
        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_default_timeout_is_30_seconds(self) -> None:
        """Default timeout_seconds = 30 is passed to asyncio.wait_for."""
        mock_proc = _make_mock_process(stdout=b"done\n", stderr=b"", returncode=0)

        captured_timeouts: list[float] = []
        original_wait_for = asyncio.wait_for

        async def _capturing_wait_for(coro: Any, timeout: float) -> Any:
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
