"""Unit tests for the httpx MCP server.

Tests mock asyncio.create_subprocess_exec to avoid spawning a real httpx
binary.  All tests exercise the internal coroutines directly (no stdin/stdout
wiring needed for unit testing).
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

# Add mcp-servers/httpx to the path so we can import server directly.
_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from server import (  # noqa: E402
    JSONRPC_INVALID_REQUEST,
    JSONRPC_PARSE_ERROR,
    MAX_OUTPUT_BYTES,
    TRUNCATION_SENTINEL,
    _cap_buffer,
    _handle_request,
    _run_httpx,
    main,
)

# ---------------------------------------------------------------------------
# Helpers: fake AsyncStreamReader and fake process
# ---------------------------------------------------------------------------


class _FakeStreamReader:
    """Simulates asyncio.StreamReader for line-by-line async iteration.

    Yields each bytes item in *lines* in order, then raises StopAsyncIteration.
    """

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = iter(lines)

    def __aiter__(self) -> _FakeStreamReader:
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._lines)
        except StopIteration:
            raise StopAsyncIteration from None


def _make_mock_process(
    stdout_lines: list[bytes] | None = None,
    stderr_lines: list[bytes] | None = None,
    returncode: int | None = 0,
) -> MagicMock:
    """Return a mock that mimics asyncio.subprocess.Process with streaming."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.pid = 12345
    proc.stdout = _FakeStreamReader(list(stdout_lines) if stdout_lines else [])
    proc.stderr = _FakeStreamReader(list(stderr_lines) if stderr_lines else [])
    proc.kill = MagicMock()
    proc.wait = AsyncMock(return_value=returncode)
    return proc


def _noop_write(line: str) -> None:
    """No-op write_line for tests that don't care about notifications."""


# ---------------------------------------------------------------------------
# _cap_buffer
# ---------------------------------------------------------------------------


class TestCapBuffer:
    def test_short_buffer_unchanged(self) -> None:
        buf, capped = _cap_buffer("hello world")
        assert buf == "hello world"
        assert not capped

    def test_exactly_at_limit_unchanged(self) -> None:
        buf = "x" * MAX_OUTPUT_BYTES
        result, capped = _cap_buffer(buf)
        assert not capped
        assert result == buf

    def test_one_byte_over_limit_truncated(self) -> None:
        # One UTF-8 byte over the limit triggers truncation.
        buf = "x" * (MAX_OUTPUT_BYTES + 1)
        result, capped = _cap_buffer(buf)
        assert capped
        assert result.endswith(TRUNCATION_SENTINEL)
        assert result.startswith("x" * MAX_OUTPUT_BYTES)


# ---------------------------------------------------------------------------
# _run_httpx — happy path and basic behaviour
# ---------------------------------------------------------------------------


class TestRunHttpx:
    @pytest.mark.asyncio
    async def test_happy_path_stdout_lines_and_exit_zero(self) -> None:
        """Each stdout line emits a notification; final result has exit_code 0
        and accumulated stdout."""
        mock_proc = _make_mock_process(
            stdout_lines=[b"https://example.com [200]\n", b"Title: Example\n"],
            stderr_lines=[],
            returncode=0,
        )
        notifications: list[dict[str, Any]] = []

        def _capture(line: str) -> None:
            notifications.append(json.loads(line))

        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            result = await _run_httpx(
                {"target": "https://example.com", "flags": ["-sc", "-title"]},
                _capture,
                req_id=1,
            )

        assert result["exit_code"] == 0
        assert "https://example.com [200]" in result["stdout"]
        assert "Title: Example" in result["stdout"]
        assert result["stderr"] == ""

        # Two stdout notifications, each correct shape.
        assert len(notifications) == 2
        for note in notifications:
            assert note["jsonrpc"] == "2.0"
            assert note["method"] == "tools/output"
            assert note["params"]["id"] == 1
            assert note["params"]["type"] == "stdout"
        assert notifications[0]["params"]["data"] == "https://example.com [200]"
        assert notifications[1]["params"]["data"] == "Title: Example"

    @pytest.mark.asyncio
    async def test_stderr_lines_emit_stderr_notifications(self) -> None:
        mock_proc = _make_mock_process(
            stdout_lines=[],
            stderr_lines=[b"warning: something\n"],
            returncode=0,
        )
        notifications: list[dict[str, Any]] = []

        def _capture_stderr(line: str) -> None:
            notifications.append(json.loads(line))

        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            result = await _run_httpx({"target": "http://localhost"}, _capture_stderr, req_id=7)

        assert len(notifications) == 1
        note = notifications[0]
        assert note["params"]["type"] == "stderr"
        assert note["params"]["data"] == "warning: something"
        assert result["stderr"] == "warning: something\n"

    @pytest.mark.asyncio
    async def test_non_zero_exit_code_propagated(self) -> None:
        mock_proc = _make_mock_process(
            stdout_lines=[],
            stderr_lines=[b"error: connection refused\n"],
            returncode=1,
        )
        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            result = await _run_httpx({"target": "http://10.0.0.1"}, _noop_write, req_id=2)

        assert result["exit_code"] == 1
        assert "connection refused" in result["stderr"]

    @pytest.mark.asyncio
    async def test_missing_target_returns_error_result(self) -> None:
        result = await _run_httpx({}, _noop_write, req_id=3)
        assert result["exit_code"] == 1
        assert "target" in result["stderr"].lower()

    @pytest.mark.asyncio
    async def test_empty_target_returns_error_result(self) -> None:
        result = await _run_httpx({"target": ""}, _noop_write, req_id=4)
        assert result["exit_code"] == 1
        assert "target" in result["stderr"].lower()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_flag",
        ["-o", "--output", "-proxy", "--proxy", "-sr", "--store-response", "-config", "-r"],
    )
    async def test_denylisted_flag_rejected_without_exec(self, bad_flag: str) -> None:
        """Filesystem-write / proxy / config flags are rejected before spawning httpx."""
        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            result = await _run_httpx(
                {"target": "http://localhost", "flags": [bad_flag, "/tmp/x"]},
                _noop_write,
                req_id=11,
            )
        assert result["exit_code"] == 1
        assert bad_flag in result["stderr"]
        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_denylisted_flag_with_equals_value_rejected(self) -> None:
        """``-proxy=http://attacker`` (single-token form) is rejected on the bare name."""
        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            result = await _run_httpx(
                {"target": "http://localhost", "flags": ["-proxy=http://attacker:8080"]},
                _noop_write,
                req_id=12,
            )
        assert result["exit_code"] == 1
        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_allowed_flags_still_pass(self) -> None:
        """Preset flags (-sc, -title, -tech-detect) are not on the denylist."""
        mock_proc = _make_mock_process(returncode=0)
        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            result = await _run_httpx(
                {"target": "http://localhost", "flags": ["-sc", "-title", "-tech-detect"]},
                _noop_write,
                req_id=13,
            )
        assert result["exit_code"] == 0
        mock_exec.assert_called_once()

    @pytest.mark.asyncio
    async def test_argv_built_correctly_flags_before_target(self) -> None:
        """Verify argv = ['httpx', *flags, target] with create_subprocess_exec."""
        mock_proc = _make_mock_process(returncode=0)
        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            await _run_httpx(
                {"target": "http://localhost:3000", "flags": ["-sc", "-title"]},
                _noop_write,
                req_id=5,
            )

        # Must use create_subprocess_exec (not shell), with argv unpacked as positional args.
        mock_exec.assert_called_once_with(
            "httpx",
            "-sc",
            "-title",
            "http://localhost:3000",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

    @pytest.mark.asyncio
    async def test_no_flags_argv_is_httpx_plus_target(self) -> None:
        mock_proc = _make_mock_process(returncode=0)
        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            await _run_httpx({"target": "http://localhost"}, _noop_write, req_id=6)

        mock_exec.assert_called_once_with(
            "httpx",
            "http://localhost",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

    @pytest.mark.asyncio
    async def test_httpx_binary_missing_returns_error_result(self) -> None:
        """FileNotFoundError (missing binary) → exit_code 1, descriptive stderr."""
        with patch(
            "server.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=FileNotFoundError("No such file or directory: 'httpx'"),
        ):
            result = await _run_httpx({"target": "http://localhost"}, _noop_write, req_id=8)

        assert result["exit_code"] == 1
        assert result["stdout"] == ""
        assert "httpx" in result["stderr"].lower()

    @pytest.mark.asyncio
    async def test_oserror_on_spawn_returns_error_result(self) -> None:
        with patch(
            "server.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=OSError("permission denied"),
        ):
            result = await _run_httpx({"target": "http://localhost"}, _noop_write, req_id=9)

        assert result["exit_code"] == 1
        assert "Failed to start subprocess" in result["stderr"]

    @pytest.mark.asyncio
    async def test_stdout_truncated_at_1mb_with_sentinel(self) -> None:
        """Output exceeding 1 MB is truncated; sentinel appended; no crash."""
        # Produce a single stdout line that blows the 1 MB cap.
        big_line = b"A" * (MAX_OUTPUT_BYTES + 500) + b"\n"
        mock_proc = _make_mock_process(stdout_lines=[big_line], returncode=0)

        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            result = await _run_httpx({"target": "http://localhost"}, _noop_write, req_id=10)

        assert result["exit_code"] == 0
        assert result["stdout"].endswith(TRUNCATION_SENTINEL)
        assert result["stderr"] == ""

    @pytest.mark.asyncio
    async def test_second_stdout_line_dropped_after_cap(self) -> None:
        """After the 1 MB cap is reached, subsequent lines are NOT appended.

        The first line (which triggers the cap) still gets one notification
        emitted for it.  The second line must be completely suppressed.
        """
        big_line = b"B" * (MAX_OUTPUT_BYTES + 100) + b"\n"
        extra_line = b"should not appear\n"
        mock_proc = _make_mock_process(stdout_lines=[big_line, extra_line], returncode=0)

        notifications: list[str] = []
        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            result = await _run_httpx(
                {"target": "http://localhost"}, notifications.append, req_id=11
            )

        # sentinel present in final result, extra content absent.
        assert TRUNCATION_SENTINEL in result["stdout"]
        assert "should not appear" not in result["stdout"]
        # Exactly one notification (the over-cap line itself); the second line
        # was dropped because the cap was already reached.
        assert len(notifications) == 1
        note = json.loads(notifications[0])
        assert note["params"]["type"] == "stdout"
        # The data for the first line is the raw decoded content (no sentinel).
        assert "should not appear" not in note["params"]["data"]

    @pytest.mark.asyncio
    async def test_timeout_returns_exit_code_124(self) -> None:
        """When the subprocess hangs beyond timeout_seconds, exit_code 124 is returned."""
        mock_proc = _make_mock_process(returncode=None)

        original_wait_for = asyncio.wait_for

        async def _patched_wait_for(coro: Any, timeout: float) -> Any:  # noqa: ASYNC109
            if timeout <= 1:
                coro.close()
                raise TimeoutError
            return await original_wait_for(coro, timeout)

        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            with (
                patch("server.asyncio.wait_for", side_effect=_patched_wait_for),
                patch("server.os.getpgid", return_value=12345),
                patch("server.os.killpg") as mock_killpg,
            ):
                result = await _run_httpx(
                    {"target": "http://localhost", "timeout_seconds": 1},
                    _noop_write,
                    req_id=12,
                )

        assert result["exit_code"] == 124
        mock_killpg.assert_called_once_with(12345, signal.SIGKILL)

    @pytest.mark.asyncio
    async def test_timeout_processlookuperror_swallowed(self) -> None:
        """ProcessLookupError on getpgid during timeout is swallowed; still returns 124."""
        mock_proc = _make_mock_process(returncode=None)
        original_wait_for = asyncio.wait_for

        async def _patched_wait_for(coro: Any, timeout: float) -> Any:  # noqa: ASYNC109
            if timeout <= 1:
                coro.close()
                raise TimeoutError
            return await original_wait_for(coro, timeout)

        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            with (
                patch("server.asyncio.wait_for", side_effect=_patched_wait_for),
                patch("server.os.getpgid", side_effect=ProcessLookupError),
            ):
                result = await _run_httpx(
                    {"target": "http://localhost", "timeout_seconds": 1},
                    _noop_write,
                    req_id=13,
                )

        assert result["exit_code"] == 124

    @pytest.mark.asyncio
    async def test_timeout_killpg_oserror_falls_back_to_kill(self) -> None:
        """Generic OSError from killpg falls back to process.kill()."""
        mock_proc = _make_mock_process(returncode=None)
        original_wait_for = asyncio.wait_for

        async def _patched_wait_for(coro: Any, timeout: float) -> Any:  # noqa: ASYNC109
            if timeout <= 1:
                coro.close()
                raise TimeoutError
            return await original_wait_for(coro, timeout)

        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            with (
                patch("server.asyncio.wait_for", side_effect=_patched_wait_for),
                patch("server.os.getpgid", return_value=12345),
                patch("server.os.killpg", side_effect=OSError("operation not permitted")),
            ):
                result = await _run_httpx(
                    {"target": "http://localhost", "timeout_seconds": 1},
                    _noop_write,
                    req_id=14,
                )

        assert result["exit_code"] == 124
        mock_proc.kill.assert_called_once()


# ---------------------------------------------------------------------------
# _handle_request (JSON-RPC dispatch)
# ---------------------------------------------------------------------------


class TestHandleRequest:
    @pytest.mark.asyncio
    async def test_run_httpx_correct_response_shape(self) -> None:
        mock_proc = _make_mock_process(
            stdout_lines=[b"http://localhost [200]\n"],
            returncode=0,
        )
        notifications: list[dict[str, Any]] = []

        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "run_httpx",
                    "arguments": {"target": "http://localhost"},
                },
            }

            def _capture_note(line: str) -> None:
                notifications.append(json.loads(line))

            response = await _handle_request(request, _capture_note)

        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        assert "result" in response
        assert "error" not in response
        result = response["result"]
        assert result["exit_code"] == 0
        assert "http://localhost [200]" in result["stdout"]

        # Verify notification was emitted with matching id.
        assert len(notifications) == 1
        assert notifications[0]["method"] == "tools/output"
        assert notifications[0]["params"]["id"] == 1

    @pytest.mark.asyncio
    async def test_unknown_tool_name_returns_method_not_found(self) -> None:
        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "run_nmap", "arguments": {}},
        }
        response = await _handle_request(request, _noop_write)

        assert "error" in response
        assert "result" not in response
        assert response["error"]["code"] == -32601
        assert response["id"] == 2

    @pytest.mark.asyncio
    async def test_unknown_method_returns_method_not_found(self) -> None:
        request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "some/other/method",
            "params": {},
        }
        response = await _handle_request(request, _noop_write)

        assert "error" in response
        assert response["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_non_zero_exit_in_result_not_error(self) -> None:
        mock_proc = _make_mock_process(
            stderr_lines=[b"error\n"],
            returncode=2,
        )
        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            request = {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "run_httpx",
                    "arguments": {"target": "http://10.0.0.1"},
                },
            }
            response = await _handle_request(request, _noop_write)

        assert "result" in response
        assert "error" not in response
        assert response["result"]["exit_code"] == 2

    @pytest.mark.asyncio
    async def test_non_dict_params_treated_as_empty(self) -> None:
        request = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": "not-a-dict",
        }
        response = await _handle_request(request, _noop_write)
        assert "error" in response
        assert response["error"]["code"] == -32601

    @pytest.mark.asyncio
    async def test_id_preserved_in_response(self) -> None:
        mock_proc = _make_mock_process(returncode=0)
        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            for req_id in [42, "abc", None]:
                request = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "tools/call",
                    "params": {
                        "name": "run_httpx",
                        "arguments": {"target": "http://localhost"},
                    },
                }
                response = await _handle_request(request, _noop_write)
                assert response["id"] == req_id

    @pytest.mark.asyncio
    async def test_response_is_json_serializable(self) -> None:
        mock_proc = _make_mock_process(
            stdout_lines=[b"out\n"],
            returncode=0,
        )
        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            request = {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "run_httpx",
                    "arguments": {"target": "http://localhost"},
                },
            }
            response = await _handle_request(request, _noop_write)

        serialized = json.dumps(response)
        assert "\n" not in serialized


# ---------------------------------------------------------------------------
# main() — stdin/stdout loop coverage
# ---------------------------------------------------------------------------


def _make_main_mocks(
    lines: list[bytes],
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Build mocks for the main() event-loop plumbing."""
    mock_reader = MagicMock()
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
    async def test_valid_request_writes_notification_then_final_response(self) -> None:
        """A run_httpx request produces one notification and one final response."""
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "run_httpx",
                "arguments": {"target": "http://localhost"},
            },
        }
        line = (json.dumps(request) + "\n").encode()
        mock_loop, mock_reader, mock_write_transport = _make_main_mocks([line])

        mock_proc = _make_mock_process(
            stdout_lines=[b"http://localhost [200]\n"],
            returncode=0,
        )

        with (
            patch("server.asyncio.get_running_loop", return_value=mock_loop),
            patch("server.asyncio.StreamReader", return_value=mock_reader),
            patch("server.asyncio.StreamReaderProtocol"),
            patch(
                "server.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
            ) as mock_exec,
        ):
            mock_exec.return_value = mock_proc
            await main()

        # 2 writes: 1 notification + 1 final response.
        assert mock_write_transport.write.call_count == 2

        notification_bytes: bytes = mock_write_transport.write.call_args_list[0][0][0]
        notification = json.loads(notification_bytes.decode().strip())
        assert notification["method"] == "tools/output"
        assert notification["params"]["type"] == "stdout"

        final_bytes: bytes = mock_write_transport.write.call_args_list[1][0][0]
        assert final_bytes.endswith(b"\n")
        final = json.loads(final_bytes.decode().strip())
        assert final["jsonrpc"] == "2.0"
        assert final["id"] == 1
        assert "result" in final
        assert final["result"]["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_invalid_json_line_writes_parse_error(self) -> None:
        mock_loop, mock_reader, mock_write_transport = _make_main_mocks([b"not valid json\n"])

        with (
            patch("server.asyncio.get_running_loop", return_value=mock_loop),
            patch("server.asyncio.StreamReader", return_value=mock_reader),
            patch("server.asyncio.StreamReaderProtocol"),
        ):
            await main()

        assert mock_write_transport.write.call_count == 1
        written_bytes = mock_write_transport.write.call_args[0][0]
        response = json.loads(written_bytes.decode().strip())
        assert response["id"] is None
        assert response["error"]["code"] == JSONRPC_PARSE_ERROR

    @pytest.mark.asyncio
    async def test_non_dict_json_writes_invalid_request_error(self) -> None:
        mock_loop, mock_reader, mock_write_transport = _make_main_mocks(
            [(json.dumps([1, 2, 3]) + "\n").encode()]
        )

        with (
            patch("server.asyncio.get_running_loop", return_value=mock_loop),
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
        mock_loop, mock_reader, mock_write_transport = _make_main_mocks([b"\n", b"   \n"])

        with (
            patch("server.asyncio.get_running_loop", return_value=mock_loop),
            patch("server.asyncio.StreamReader", return_value=mock_reader),
            patch("server.asyncio.StreamReaderProtocol"),
        ):
            await main()

        mock_write_transport.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_eof_exits_cleanly(self) -> None:
        mock_loop, mock_reader, mock_write_transport = _make_main_mocks([])

        with (
            patch("server.asyncio.get_running_loop", return_value=mock_loop),
            patch("server.asyncio.StreamReader", return_value=mock_reader),
            patch("server.asyncio.StreamReaderProtocol"),
        ):
            await main()

        mock_write_transport.write.assert_not_called()
