"""httpx MCP server.

Reads JSON-RPC 2.0 requests from stdin (newline-delimited), executes the
``httpx`` binary as a subprocess, streams each output line as a
``tools/output`` notification, then writes a final JSON-RPC 2.0 result line.

Wire protocol
-------------
Request (unchanged from shell-exec):
    {"jsonrpc":"2.0","id":N,"method":"tools/call",
     "params":{"name":"run_httpx","arguments":{...}}}

Streaming notifications (zero or more, one per output line):
    {"jsonrpc":"2.0","method":"tools/output",
     "params":{"id":N,"type":"stdout","data":"<line text, no trailing newline>"}}
    (use "type":"stderr" for stderr lines)

Final result (exactly one, same shape as shell-exec):
    {"jsonrpc":"2.0","id":N,
     "result":{"exit_code":<int>,"stdout":"<full stdout, 1MB-capped>",
               "stderr":"<full stderr, 1MB-capped>"}}

Error shapes (same as shell-exec):
    unknown method/tool → code -32601
    parse error         → id null, code -32700
    invalid request     → code -32600

Output cap: MAX_OUTPUT_BYTES = 1_048_576 (1 MB). Once a stream buffer reaches
the cap, the sentinel "\\n[output truncated at 1 MB]" is appended and further
lines for that stream are dropped (no unbounded memory growth).

Usage (from subprocess_manager):
    python mcp-servers/httpx/server.py
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from collections.abc import Callable
from typing import Any

MAX_OUTPUT_BYTES: int = 1_048_576  # 1 MB
TRUNCATION_SENTINEL: str = "\n[output truncated at 1 MB]"

# Flags that would let a caller exceed the server's declared capabilities
# (capability_flags: [network]) or open an exfiltration path. The manifest
# declares network access only, so flags that write to the filesystem or route
# traffic through a caller-supplied proxy/config are rejected before exec. This
# is defense-in-depth on the light path; full per-command approval-gating for
# dangerous invocations lands in Slice 16.
DENYLISTED_FLAGS: frozenset[str] = frozenset(
    {
        "-o",
        "-output",
        "--output",
        "-sr",
        "-store-response",
        "--store-response",
        "-srd",
        "-store-response-dir",
        "--store-response-dir",
        "-config",
        "--config",
        "-proxy",
        "-http-proxy",
        "--proxy",
        "-resolvers",
        "-r",
        "--resolvers",
    }
)

JSONRPC_METHOD_NOT_FOUND: int = -32601
JSONRPC_PARSE_ERROR: int = -32700
JSONRPC_INVALID_REQUEST: int = -32600


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _cap_buffer(buf: str) -> tuple[str, bool]:
    """Return (possibly-truncated buffer, is_capped).

    If the buffer's UTF-8 encoding exceeds MAX_OUTPUT_BYTES, truncate to the
    cap, append the sentinel, and return is_capped=True so the caller knows
    to stop accumulating.
    """
    encoded = buf.encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        truncated = encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        return truncated + TRUNCATION_SENTINEL, True
    return buf, False


# ---------------------------------------------------------------------------
# Streaming run
# ---------------------------------------------------------------------------


async def _run_httpx(
    arguments: dict[str, Any],
    write_line: Callable[[str], None],
    req_id: Any,
) -> dict[str, Any]:
    """Execute httpx and stream output; return the final result dict.

    Parameters
    ----------
    arguments:
        Parsed ``arguments`` from the JSON-RPC params.
    write_line:
        Callable that writes a JSON string to stdout (no newline needed —
        the caller appends it).
    req_id:
        The JSON-RPC request id echoed into notification ``params.id``.
    """
    target = arguments.get("target")
    if not isinstance(target, str) or not target:
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": "run_httpx requires a non-empty 'target' string argument",
        }

    flags_raw = arguments.get("flags", [])
    if not isinstance(flags_raw, list):
        flags_raw = []
    # Ensure every flag is a string; silently drop non-strings.
    flags: list[str] = [f for f in flags_raw if isinstance(f, str)]

    # Reject flags that would breach the declared capability set (network only):
    # filesystem-write (-o/-sr/...), arbitrary config, or a caller-supplied proxy
    # (an exfiltration vector). Compared case-insensitively on the bare flag name.
    denied = [f for f in flags if f.split("=", 1)[0].lower() in DENYLISTED_FLAGS]
    if denied:
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": f"run_httpx rejected disallowed flag(s): {' '.join(denied)}",
        }

    timeout_seconds: float = float(arguments.get("timeout_seconds", 30))

    argv: list[str] = ["httpx", *flags, target]

    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError:
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": "httpx binary not found; ensure httpx is installed and on PATH",
        }
    except OSError as exc:
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": f"Failed to start subprocess: {exc}",
        }

    stdout_buf: str = ""
    stderr_buf: str = ""
    stdout_capped: bool = False
    stderr_capped: bool = False

    async def _drain_stream(
        stream: asyncio.StreamReader,
        stream_type: str,
    ) -> None:
        """Read lines from *stream*, emit notifications, accumulate buffer."""
        nonlocal stdout_buf, stderr_buf, stdout_capped, stderr_capped

        async for raw_line in stream:
            line_text = raw_line.decode("utf-8", errors="replace").rstrip("\n")

            if stream_type == "stdout":
                if not stdout_capped:
                    new_buf = stdout_buf + line_text + "\n"
                    new_buf, capped = _cap_buffer(new_buf)
                    stdout_buf = new_buf
                    stdout_capped = capped
                    # Emit notification for this line.
                    notification = json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "method": "tools/output",
                            "params": {
                                "id": req_id,
                                "type": "stdout",
                                "data": line_text,
                            },
                        }
                    )
                    write_line(notification)
                # If already capped, stop emitting notifications too.
            else:  # stderr
                if not stderr_capped:
                    new_buf = stderr_buf + line_text + "\n"
                    new_buf, capped = _cap_buffer(new_buf)
                    stderr_buf = new_buf
                    stderr_capped = capped
                    notification = json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "method": "tools/output",
                            "params": {
                                "id": req_id,
                                "type": "stderr",
                                "data": line_text,
                            },
                        }
                    )
                    write_line(notification)

    assert process.stdout is not None  # guaranteed by PIPE
    assert process.stderr is not None  # guaranteed by PIPE

    stdout_task = asyncio.create_task(_drain_stream(process.stdout, "stdout"))
    stderr_task = asyncio.create_task(_drain_stream(process.stderr, "stderr"))

    async def _wait_all() -> None:
        await asyncio.gather(stdout_task, stderr_task)
        await process.wait()

    try:
        await asyncio.wait_for(_wait_all(), timeout=timeout_seconds)
    except TimeoutError:
        # Kill the entire process group so any children are also reaped.
        try:
            pgid = os.getpgid(process.pid)
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception:  # noqa: BLE001
            try:
                process.kill()
            except ProcessLookupError:
                pass
        try:
            await process.wait()
        except Exception:  # noqa: BLE001
            pass
        # Cancel the drain tasks to avoid resource leaks.
        stdout_task.cancel()
        stderr_task.cancel()
        return {
            "exit_code": 124,
            "stdout": stdout_buf,
            "stderr": stderr_buf if stderr_buf else f"Command timed out after {timeout_seconds}s",
        }

    exit_code = process.returncode if process.returncode is not None else 1

    return {
        "exit_code": exit_code,
        "stdout": stdout_buf,
        "stderr": stderr_buf,
    }


# ---------------------------------------------------------------------------
# Heavy demo/test tool — run_httpx_heavy
# ---------------------------------------------------------------------------
# THROWAWAY DEMO TOOL — Slice 05 only.
#
# Purpose: make the per-engagement heavy-tool concurrency model observable
# end-to-end.  Two ``run_httpx_heavy`` calls against the same sandbox host
# will visibly serialize because the backend admission manager holds the
# per-host lock for the full duration of ``hold_seconds``.
#
# This tool is superseded by real heavy tools (nmap, gobuster, etc.) that
# land in Slice 26.  Remove or demote it to a test-only preset then.
#
# Design:
#   1. Validate ``target`` (same shape/check as ``run_httpx``).
#   2. Clamp ``hold_seconds`` to the range [1, 30] so no caller can wedge
#      the slot for more than 30 s.
#   3. Make a single httpx request to the target (same subprocess as the
#      light tool) to prove the target is reachable.
#   4. Sleep for the (clamped) ``hold_seconds`` while the slot is held.
#
# Sandbox-gating: ``target`` is guarded by the backend's
# ``_enforce_sandbox_guard`` in ``service.execute_tool_run`` before this
# tool handler is ever invoked.  The guard applies to every tool that carries
# a ``target`` arg (generic guard at the service layer).  This tool NEVER
# needs to call the guard itself — it is covered centrally.

_HOLD_SECONDS_MIN: int = 1
_HOLD_SECONDS_MAX: int = 30
_HOLD_SECONDS_DEFAULT: int = 2


async def _run_httpx_heavy(
    arguments: dict[str, Any],
    write_line: Callable[[str], None],
    req_id: Any,
) -> dict[str, Any]:
    """Execute a bounded httpx request then hold the slot for ``hold_seconds``.

    Parameters
    ----------
    arguments:
        Parsed ``arguments`` from the JSON-RPC params.  Required fields:
        ``target`` (str, non-empty).  Optional: ``hold_seconds`` (number,
        clamped to [1, 30]; default 2).
    write_line:
        Same notification callback as ``_run_httpx``.
    req_id:
        The JSON-RPC request id echoed into notification ``params.id``.

    Returns
    -------
    dict with ``exit_code``, ``stdout``, ``stderr`` — same shape as
    ``_run_httpx``.
    """
    target = arguments.get("target")
    if not isinstance(target, str) or not target:
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": "run_httpx_heavy requires a non-empty 'target' string argument",
        }

    # Clamp hold_seconds to [_HOLD_SECONDS_MIN, _HOLD_SECONDS_MAX].
    raw_hold = arguments.get("hold_seconds", _HOLD_SECONDS_DEFAULT)
    try:
        hold_seconds: float = float(raw_hold)
    except (TypeError, ValueError):
        hold_seconds = float(_HOLD_SECONDS_DEFAULT)
    hold_seconds = max(_HOLD_SECONDS_MIN, min(_HOLD_SECONDS_MAX, hold_seconds))

    # Step 1: run the httpx probe to verify the target is reachable.
    probe_result = await _run_httpx(
        {"target": target, "flags": ["-sc", "-title"]},
        write_line,
        req_id,
    )

    if probe_result["exit_code"] != 0:
        # Return the probe failure immediately — no hold needed.
        return probe_result

    # Step 2: emit a notification that we are entering the hold phase.
    hold_notification = {
        "jsonrpc": "2.0",
        "method": "tools/output",
        "params": {
            "id": req_id,
            "type": "stdout",
            "data": f"[run_httpx_heavy] holding slot for {hold_seconds:.1f}s ...",
        },
    }
    write_line(json.dumps(hold_notification))

    # Step 3: hold for the bounded duration so the slot is visibly occupied.
    await asyncio.sleep(hold_seconds)

    return {
        "exit_code": probe_result["exit_code"],
        "stdout": probe_result["stdout"],
        "stderr": probe_result["stderr"],
    }


# ---------------------------------------------------------------------------
# JSON-RPC dispatch
# ---------------------------------------------------------------------------


async def _handle_request(
    request: dict[str, Any],
    write_line: Callable[[str], None],
) -> dict[str, Any]:
    """Dispatch a single JSON-RPC 2.0 request and return a final response dict.

    For ``run_httpx`` the function also writes streaming notifications via
    *write_line* before returning the final response dict.
    """
    req_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {})

    if not isinstance(params, dict):
        params = {}

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

    if tool_name == "run_httpx":
        result = await _run_httpx(arguments, write_line, req_id)
    elif tool_name == "run_httpx_heavy":
        result = await _run_httpx_heavy(arguments, write_line, req_id)
    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": JSONRPC_METHOD_NOT_FOUND,
                "message": f"Tool not found: {tool_name!r}",
            },
        }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "result": result,
    }


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


async def main() -> None:
    """Read JSON-RPC requests from stdin; write responses (and notifications) to stdout."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

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
            break

        line_text = raw_line.decode("utf-8", errors="replace").strip()
        if not line_text:
            continue

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

        response = await _handle_request(request, _write_line)
        _write_line(json.dumps(response))


if __name__ == "__main__":
    asyncio.run(main())
