"""Shell-exec MCP server.

Reads JSON-RPC 2.0 requests from stdin (newline-delimited), executes shell
commands via asyncio.create_subprocess_shell, and writes JSON-RPC 2.0
responses to stdout (newline-delimited).

Wire protocol matches backend/app/features/mcp/subprocess_manager.py exactly:
  Request:  {"jsonrpc": "2.0", "id": <int>, "method": "tools/call",
             "params": {"name": "<tool_name>", "arguments": <args dict>}}
  Response: {"jsonrpc": "2.0", "id": <int>,
             "result": {"exit_code": <int>, "stdout": "<str>", "stderr": "<str>"}}
  Error:    {"jsonrpc": "2.0", "id": <int>,
             "error": {"code": -32601, "message": "Method not found"}}

Output hard cap: MAX_OUTPUT_BYTES = 1_048_576 (1 MB).  If either stdout or
stderr exceeds this limit, the stream is truncated at exactly MAX_OUTPUT_BYTES
bytes and the sentinel "\\n[output truncated at 1 MB]" is appended.

Usage (from subprocess_manager):
    python mcp-servers/shell-exec/server.py
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from typing import Any

MAX_OUTPUT_BYTES: int = 1_048_576  # 1 MB
TRUNCATION_SENTINEL: str = "\n[output truncated at 1 MB]"

JSONRPC_METHOD_NOT_FOUND: int = -32601
JSONRPC_PARSE_ERROR: int = -32700
JSONRPC_INVALID_REQUEST: int = -32600


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _truncate(data: bytes) -> str:
    """Decode bytes, truncating at MAX_OUTPUT_BYTES and appending sentinel."""
    if len(data) > MAX_OUTPUT_BYTES:
        truncated = data[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        return truncated + TRUNCATION_SENTINEL
    return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------


async def _run_command(arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute a shell command and return exit_code/stdout/stderr."""
    command = arguments.get("command")
    if not isinstance(command, str) or not command:
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": "run_command requires a non-empty 'command' string argument",
        }

    timeout_seconds: float = float(arguments.get("timeout_seconds", 30))

    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": f"Failed to start subprocess: {exc}",
        }

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        # Kill the entire process group to reap any children spawned by the shell.
        try:
            pgid = os.getpgid(process.pid)
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception:  # noqa: BLE001
            # Fallback: kill just the shell process if killpg is unavailable
            # (e.g. unsupported platform or process already gone).
            try:
                process.kill()
            except ProcessLookupError:
                pass
        try:
            await process.wait()
        except Exception:  # noqa: BLE001
            pass
        return {
            "exit_code": 124,  # conventional timeout exit code (same as `timeout` utility)
            "stdout": "",
            "stderr": f"Command timed out after {timeout_seconds}s",
        }

    exit_code = process.returncode if process.returncode is not None else 1

    return {
        "exit_code": exit_code,
        "stdout": _truncate(stdout_bytes),
        "stderr": _truncate(stderr_bytes),
    }


# ---------------------------------------------------------------------------
# JSON-RPC dispatch
# ---------------------------------------------------------------------------

_TOOLS: dict[str, Any] = {
    "run_command": _run_command,
}


async def _handle_request(request: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a single JSON-RPC 2.0 request and return a response dict."""
    req_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {})

    if not isinstance(params, dict):
        params = {}

    # We only handle "tools/call"
    if method != "tools/call":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": JSONRPC_METHOD_NOT_FOUND,
                "message": f"Method not found: {method!r}",
            },
        }

    tool_name = params.get("name")
    arguments = params.get("arguments", {})
    if not isinstance(arguments, dict):
        arguments = {}

    handler = _TOOLS.get(str(tool_name) if tool_name is not None else "")
    if handler is None:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": JSONRPC_METHOD_NOT_FOUND,
                "message": f"Tool not found: {tool_name!r}",
            },
        }

    result = await handler(arguments)
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": result,
    }


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


async def main() -> None:
    """Read JSON-RPC requests from stdin; write responses to stdout."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    # Wrap stdout for async writes
    write_transport, write_protocol = await loop.connect_write_pipe(
        asyncio.BaseProtocol, sys.stdout.buffer
    )

    def _write_line(line: str) -> None:
        data = (line + "\n").encode()
        write_transport.write(data)

    while True:
        try:
            raw_line = await reader.readline()
        except Exception:  # noqa: BLE001
            break

        if not raw_line:
            # stdin closed — exit cleanly
            break

        line_text = raw_line.decode("utf-8", errors="replace").strip()
        if not line_text:
            continue

        # Parse JSON
        try:
            request = json.loads(line_text)
        except json.JSONDecodeError as exc:
            error_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": JSONRPC_PARSE_ERROR,
                    "message": f"Parse error: {exc}",
                },
            }
            _write_line(json.dumps(error_resp))
            continue

        if not isinstance(request, dict):
            error_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": JSONRPC_INVALID_REQUEST,
                    "message": "Invalid Request",
                },
            }
            _write_line(json.dumps(error_resp))
            continue

        response = await _handle_request(request)
        _write_line(json.dumps(response))


if __name__ == "__main__":
    asyncio.run(main())
