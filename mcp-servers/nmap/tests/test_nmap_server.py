"""Unit tests for the nmap MCP server.

Tests mock asyncio.create_subprocess_exec to avoid spawning a real nmap binary.
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

# Add mcp-servers/nmap to the path so we can import server directly.
_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from server import (  # noqa: E402
    DEFAULT_NMAP_BIN,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_PARSE_ERROR,
    MAX_OUTPUT_BYTES,
    NMAP_BIN_ENV,
    TRUNCATION_SENTINEL,
    _cap_buffer,
    _handle_request,
    _resolve_nmap_binary,
    _run_nmap,
    main,
)

# ---------------------------------------------------------------------------
# Helpers: fake AsyncStreamReader and fake process
# ---------------------------------------------------------------------------


class _FakeStreamReader:
    """Simulates asyncio.StreamReader for line-by-line async iteration."""

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

    def test_one_byte_over_limit_truncated(self) -> None:
        buf = "x" * (MAX_OUTPUT_BYTES + 1)
        result, capped = _cap_buffer(buf)
        assert capped
        assert result.endswith(TRUNCATION_SENTINEL)
        assert result.startswith("x" * MAX_OUTPUT_BYTES)


# ---------------------------------------------------------------------------
# _resolve_nmap_binary — never let a stray "nmap" on PATH win
# ---------------------------------------------------------------------------


class TestResolveNmapBinary:
    def test_env_override_wins(self) -> None:
        with patch.dict(os.environ, {NMAP_BIN_ENV: "/opt/nmap"}):
            assert _resolve_nmap_binary() == "/opt/nmap"

    def test_default_install_path_used_when_present(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("server.os.path.exists", return_value=True),
        ):
            assert _resolve_nmap_binary() == DEFAULT_NMAP_BIN
        # Regression guard: always an absolute path, never a bare "nmap".
        assert DEFAULT_NMAP_BIN.startswith("/")

    def test_empty_env_override_ignored(self) -> None:
        with (
            patch.dict(os.environ, {NMAP_BIN_ENV: ""}),
            patch("server.os.path.exists", return_value=True),
        ):
            assert _resolve_nmap_binary() == DEFAULT_NMAP_BIN

    def test_falls_back_to_which_when_default_missing(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("server.os.path.exists", return_value=False),
            patch("server.shutil.which", return_value="/home/dev/bin/nmap"),
        ):
            assert _resolve_nmap_binary() == "/home/dev/bin/nmap"

    def test_falls_back_to_default_when_nothing_found(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("server.os.path.exists", return_value=False),
            patch("server.shutil.which", return_value=None),
        ):
            assert _resolve_nmap_binary() == DEFAULT_NMAP_BIN


# ---------------------------------------------------------------------------
# _run_nmap — happy path and basic behaviour
# ---------------------------------------------------------------------------


class TestRunNmap:
    @pytest.mark.asyncio
    async def test_happy_path_stdout_lines_and_exit_zero(self) -> None:
        mock_proc = _make_mock_process(
            stdout_lines=[b"Nmap scan report for juice-shop (172.18.0.5)\n", b"3000/tcp open\n"],
            returncode=0,
        )
        notifications: list[dict[str, Any]] = []

        def _capture(line: str) -> None:
            notifications.append(json.loads(line))

        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            result = await _run_nmap(
                {"target": "juice-shop", "flags": ["-Pn", "-sT", "--top-ports", "100"]},
                _capture,
                req_id=1,
            )

        assert result["exit_code"] == 0
        assert "3000/tcp open" in result["stdout"]
        assert len(notifications) == 2
        assert notifications[0]["params"]["type"] == "stdout"

    @pytest.mark.asyncio
    async def test_argv_is_binary_flags_then_positional_target(self) -> None:
        """nmap takes a POSITIONAL target (unlike httpx's -u); stdin is DEVNULL."""
        mock_proc = _make_mock_process(returncode=0)
        with (
            patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec,
            patch("server._resolve_nmap_binary", return_value="/usr/bin/nmap"),
        ):
            mock_exec.return_value = mock_proc
            await _run_nmap(
                {"target": "juice-shop", "flags": ["-Pn", "-sT"]},
                _noop_write,
                req_id=5,
            )

        mock_exec.assert_called_once_with(
            "/usr/bin/nmap",
            "-Pn",
            "-sT",
            "juice-shop",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

    @pytest.mark.asyncio
    async def test_stderr_lines_emit_notifications(self) -> None:
        mock_proc = _make_mock_process(stderr_lines=[b"warning: something\n"], returncode=0)
        notifications: list[dict[str, Any]] = []
        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            result = await _run_nmap(
                {"target": "juice-shop"}, lambda s: notifications.append(json.loads(s)), req_id=7
            )
        assert notifications[0]["params"]["type"] == "stderr"
        assert result["stderr"] == "warning: something\n"

    @pytest.mark.asyncio
    async def test_non_zero_exit_code_propagated(self) -> None:
        mock_proc = _make_mock_process(stderr_lines=[b"error\n"], returncode=1)
        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            result = await _run_nmap({"target": "juice-shop"}, _noop_write, req_id=2)
        assert result["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_missing_target_returns_error(self) -> None:
        result = await _run_nmap({}, _noop_write, req_id=3)
        assert result["exit_code"] == 1
        assert "target" in result["stderr"].lower()

    @pytest.mark.asyncio
    async def test_empty_target_returns_error(self) -> None:
        result = await _run_nmap({"target": ""}, _noop_write, req_id=4)
        assert result["exit_code"] == 1
        assert "target" in result["stderr"].lower()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "bad_flag",
        [
            "-oN",  # filesystem write
            "-oX",
            "-oA",
            "--datadir",
            "-iL",  # alternate target source (sandbox bypass)
            "-iR",  # random internet targets (sandbox bypass)
            "--excludefile",
            "--proxies",  # egress/SSRF redirection
            "-b",  # FTP-bounce scan (pivot through a relay host)
            "--script",  # NSE risk-class escalation
            "--script-args",
        ],
    )
    async def test_denylisted_flag_rejected_without_exec(self, bad_flag: str) -> None:
        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            result = await _run_nmap(
                {"target": "juice-shop", "flags": [bad_flag, "value"]}, _noop_write, req_id=11
            )
        assert result["exit_code"] == 1
        assert bad_flag.split("=", 1)[0] in result["stderr"]
        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_denylisted_flag_with_equals_value_rejected(self) -> None:
        """``--script=exploit`` (single-token form) is rejected on the bare name."""
        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            result = await _run_nmap(
                {"target": "juice-shop", "flags": ["--script=exploit"]}, _noop_write, req_id=12
            )
        assert result["exit_code"] == 1
        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_denylist_is_case_insensitive(self) -> None:
        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            result = await _run_nmap(
                {"target": "juice-shop", "flags": ["-On", "out.txt"]}, _noop_write, req_id=13
            )
        assert result["exit_code"] == 1
        mock_exec.assert_not_called()

    @pytest.mark.asyncio
    async def test_preset_flags_pass(self) -> None:
        """The aggressive preset's flags are not on the denylist."""
        mock_proc = _make_mock_process(returncode=0)
        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            result = await _run_nmap(
                {
                    "target": "juice-shop",
                    "flags": ["-Pn", "-sT", "-sV", "-T4", "--top-ports", "1000"],
                },
                _noop_write,
                req_id=14,
            )
        assert result["exit_code"] == 0
        mock_exec.assert_called_once()

    @pytest.mark.asyncio
    async def test_binary_missing_returns_error(self) -> None:
        with patch(
            "server.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=FileNotFoundError("No such file"),
        ):
            result = await _run_nmap({"target": "juice-shop"}, _noop_write, req_id=8)
        assert result["exit_code"] == 1
        assert "nmap" in result["stderr"].lower()

    @pytest.mark.asyncio
    async def test_oserror_on_spawn_returns_error(self) -> None:
        with patch(
            "server.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=OSError("permission denied"),
        ):
            result = await _run_nmap({"target": "juice-shop"}, _noop_write, req_id=9)
        assert result["exit_code"] == 1
        assert "Failed to start subprocess" in result["stderr"]

    @pytest.mark.asyncio
    async def test_stdout_truncated_at_1mb_with_sentinel(self) -> None:
        big_line = b"A" * (MAX_OUTPUT_BYTES + 500) + b"\n"
        mock_proc = _make_mock_process(stdout_lines=[big_line], returncode=0)
        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            result = await _run_nmap({"target": "juice-shop"}, _noop_write, req_id=10)
        assert result["stdout"].endswith(TRUNCATION_SENTINEL)

    @pytest.mark.asyncio
    async def test_timeout_returns_exit_code_124(self) -> None:
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
                result = await _run_nmap(
                    {"target": "juice-shop", "timeout_seconds": 1}, _noop_write, req_id=12
                )
        assert result["exit_code"] == 124
        mock_killpg.assert_called_once_with(12345, signal.SIGKILL)

    @pytest.mark.asyncio
    async def test_timeout_killpg_oserror_falls_back_to_kill(self) -> None:
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
                patch("server.os.killpg", side_effect=OSError("not permitted")),
            ):
                result = await _run_nmap(
                    {"target": "juice-shop", "timeout_seconds": 1}, _noop_write, req_id=14
                )
        assert result["exit_code"] == 124
        mock_proc.kill.assert_called_once()


# ---------------------------------------------------------------------------
# _handle_request (JSON-RPC dispatch)
# ---------------------------------------------------------------------------


class TestHandleRequest:
    @pytest.mark.asyncio
    async def test_run_nmap_correct_response_shape(self) -> None:
        mock_proc = _make_mock_process(stdout_lines=[b"3000/tcp open\n"], returncode=0)
        with patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.return_value = mock_proc
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "run_nmap", "arguments": {"target": "juice-shop"}},
            }
            response = await _handle_request(request, _noop_write)
        assert response["id"] == 1
        assert response["result"]["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_unknown_tool_name_returns_method_not_found(self) -> None:
        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "run_httpx", "arguments": {}},
        }
        response = await _handle_request(request, _noop_write)
        assert response["error"]["code"] == JSONRPC_METHOD_NOT_FOUND

    @pytest.mark.asyncio
    async def test_unknown_method_returns_method_not_found(self) -> None:
        request = {"jsonrpc": "2.0", "id": 3, "method": "some/method", "params": {}}
        response = await _handle_request(request, _noop_write)
        assert response["error"]["code"] == JSONRPC_METHOD_NOT_FOUND

    @pytest.mark.asyncio
    async def test_non_dict_params_treated_as_empty(self) -> None:
        request = {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": "nope"}
        response = await _handle_request(request, _noop_write)
        assert response["error"]["code"] == JSONRPC_METHOD_NOT_FOUND

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
                    "params": {"name": "run_nmap", "arguments": {"target": "juice-shop"}},
                }
                response = await _handle_request(request, _noop_write)
                assert response["id"] == req_id


# ---------------------------------------------------------------------------
# main() — stdin/stdout loop coverage
# ---------------------------------------------------------------------------


def _make_main_mocks(lines: list[bytes]) -> tuple[MagicMock, MagicMock, MagicMock]:
    mock_reader = MagicMock()
    mock_reader.readline = AsyncMock(side_effect=list(lines) + [b""])
    mock_write_transport = MagicMock()
    mock_loop = MagicMock()
    mock_loop.connect_read_pipe = AsyncMock(return_value=(MagicMock(), MagicMock()))
    mock_loop.connect_write_pipe = AsyncMock(return_value=(mock_write_transport, MagicMock()))
    return mock_loop, mock_reader, mock_write_transport


class TestMainLoop:
    @pytest.mark.asyncio
    async def test_valid_request_writes_notification_then_final_response(self) -> None:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "run_nmap", "arguments": {"target": "juice-shop"}},
        }
        line = (json.dumps(request) + "\n").encode()
        mock_loop, mock_reader, mock_write_transport = _make_main_mocks([line])
        mock_proc = _make_mock_process(stdout_lines=[b"3000/tcp open\n"], returncode=0)

        with (
            patch("server.asyncio.get_running_loop", return_value=mock_loop),
            patch("server.asyncio.StreamReader", return_value=mock_reader),
            patch("server.asyncio.StreamReaderProtocol"),
            patch("server.asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec,
        ):
            mock_exec.return_value = mock_proc
            await main()

        assert mock_write_transport.write.call_count == 2  # 1 notification + 1 final
        final = json.loads(mock_write_transport.write.call_args_list[1][0][0].decode().strip())
        assert final["result"]["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_invalid_json_line_writes_parse_error(self) -> None:
        mock_loop, mock_reader, mock_write_transport = _make_main_mocks([b"not json\n"])
        with (
            patch("server.asyncio.get_running_loop", return_value=mock_loop),
            patch("server.asyncio.StreamReader", return_value=mock_reader),
            patch("server.asyncio.StreamReaderProtocol"),
        ):
            await main()
        response = json.loads(mock_write_transport.write.call_args[0][0].decode().strip())
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
        response = json.loads(mock_write_transport.write.call_args[0][0].decode().strip())
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
