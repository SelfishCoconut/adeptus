"""nmap MCP server.

Reads JSON-RPC 2.0 requests from stdin (newline-delimited), executes the
``nmap`` binary as a subprocess, streams each output line as a ``tools/output``
notification, then writes a final JSON-RPC 2.0 result line.

Wire protocol (identical to the httpx / shell-exec servers)
-----------------------------------------------------------
Request:
    {"jsonrpc":"2.0","id":N,"method":"tools/call",
     "params":{"name":"run_nmap","arguments":{...}}}

Streaming notifications (zero or more, one per output line):
    {"jsonrpc":"2.0","method":"tools/output",
     "params":{"id":N,"type":"stdout","data":"<line text, no trailing newline>"}}
    (use "type":"stderr" for stderr lines)

Final result (exactly one):
    {"jsonrpc":"2.0","id":N,
     "result":{"exit_code":<int>,"stdout":"<full stdout, 1MB-capped>",
               "stderr":"<full stderr, 1MB-capped>"}}

Error shapes:
    unknown method/tool → code -32601
    parse error         → id null, code -32700
    invalid request     → code -32600

Output cap: MAX_OUTPUT_BYTES = 1_048_576 (1 MB). Once a stream buffer reaches the
cap, the sentinel "\\n[output truncated at 1 MB]" is appended and further lines for
that stream are dropped (no unbounded memory growth).

Usage (from subprocess_manager):
    python mcp-servers/nmap/server.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import sys
from collections.abc import Callable
from typing import Any

MAX_OUTPUT_BYTES: int = 1_048_576  # 1 MB
TRUNCATION_SENTINEL: str = "\n[output truncated at 1 MB]"

# Binary resolution -----------------------------------------------------------
# We invoke the real nmap binary by absolute path, never a bare "nmap" via PATH.
# This mirrors the lesson from the httpx server (a venv console-script could
# otherwise shadow the intended binary under ``uv run``). Overridable via
# $ADEPTUS_NMAP_BIN; the backend Dockerfile installs nmap at /usr/bin/nmap.
NMAP_BIN_ENV: str = "ADEPTUS_NMAP_BIN"
DEFAULT_NMAP_BIN: str = "/usr/bin/nmap"


def _resolve_nmap_binary() -> str:
    """Return the path to the nmap binary.

    Resolution order (so a stray ``nmap`` on PATH can never win in the container,
    where ``DEFAULT_NMAP_BIN`` always exists):

    1. ``$ADEPTUS_NMAP_BIN`` if set non-empty — explicit operator override.
    2. ``DEFAULT_NMAP_BIN`` if it exists on disk — the Dockerfile install path.
    3. ``shutil.which("nmap")`` — last resort for bare dev boxes; falls back to
       ``DEFAULT_NMAP_BIN`` (and a clear FileNotFoundError downstream) otherwise.
    """
    override = os.environ.get(NMAP_BIN_ENV)
    if override:
        return override
    if os.path.exists(DEFAULT_NMAP_BIN):
        return DEFAULT_NMAP_BIN
    return shutil.which("nmap") or DEFAULT_NMAP_BIN


# Flags a caller may NOT supply. Two reasons, both load-bearing:
#
# 1. Capability containment (manifest declares network only): any flag that reads
#    or writes the filesystem (-oN/-oX/-oG/-oA/-oS, --append-output, --stylesheet,
#    --datadir, --resume) is rejected before exec.
# 2. Sandbox containment: the single ``target`` arg is guarded by the backend's
#    sandbox guard. Flags that introduce OTHER targets out of band — ``-iL``
#    (target list file), ``--excludefile``, or ``-iR`` (scan RANDOM internet
#    hosts!) — would bypass that guard, so they are forbidden.
# 3. NSE control: ``--script*`` is forbidden so a caller cannot pull in
#    exploit/brute/dos categories that change the risk class beyond "scan". The
#    presets deliberately do NOT use NSE; a curated allowlist can come later.
#
# Compared case-insensitively on the bare flag name (the part before any "=").
DENYLISTED_FLAGS: frozenset[str] = frozenset(
    {
        # filesystem output
        "-on",
        "-ox",
        "-og",
        "-oa",
        "-os",
        "--append-output",
        "--stylesheet",
        "--webxml",
        # arbitrary data / state files
        "--datadir",
        "--resume",
        # out-of-band / alternate targets (would bypass the sandbox guard)
        "-il",
        "-ir",
        "--excludefile",
        # egress / pivot redirection (route the scan through a third host):
        # --proxies is an exfil/SSRF vector (cf. httpx -proxy); -b is an FTP-bounce
        # scan that originates from and connects to an arbitrary relay host.
        "--proxies",
        "-b",
        # NSE scripting (risk-class escalation)
        "--script",
        "--script-args",
        "--script-args-file",
        "--script-help",
        "--script-updatedb",
        "--script-trace",
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

    If the buffer's UTF-8 encoding exceeds MAX_OUTPUT_BYTES, truncate to the cap,
    append the sentinel, and return is_capped=True so the caller stops accumulating.
    """
    encoded = buf.encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        truncated = encoded[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        return truncated + TRUNCATION_SENTINEL, True
    return buf, False


# ---------------------------------------------------------------------------
# Streaming run
# ---------------------------------------------------------------------------


async def _run_nmap(
    arguments: dict[str, Any],
    write_line: Callable[[str], None],
    req_id: Any,
) -> dict[str, Any]:
    """Execute nmap and stream output; return the final result dict.

    Parameters
    ----------
    arguments:
        Parsed ``arguments`` from the JSON-RPC params. Required: ``target``
        (str, non-empty). Optional: ``flags`` (list[str]), ``timeout_seconds``
        (number, default 120).
    write_line:
        Callable that writes a JSON string to stdout (the caller appends "\\n").
    req_id:
        The JSON-RPC request id echoed into notification ``params.id``.
    """
    target = arguments.get("target")
    if not isinstance(target, str) or not target:
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": "run_nmap requires a non-empty 'target' string argument",
        }

    flags_raw = arguments.get("flags", [])
    if not isinstance(flags_raw, list):
        flags_raw = []
    # Ensure every flag is a string; silently drop non-strings.
    flags: list[str] = [f for f in flags_raw if isinstance(f, str)]

    # Reject capability-breaching / sandbox-bypassing / NSE flags (see DENYLISTED_FLAGS).
    # Compared case-insensitively on the bare flag name (before any "=").
    denied = [f for f in flags if f.split("=", 1)[0].lower() in DENYLISTED_FLAGS]
    if denied:
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": f"run_nmap rejected disallowed flag(s): {' '.join(denied)}",
        }

    timeout_seconds: float = float(arguments.get("timeout_seconds", 120))

    # nmap takes its target(s) as positional args. stdin is routed from /dev/null
    # so the child can never block on (or read) the MCP server's JSON-RPC stdin.
    argv: list[str] = [_resolve_nmap_binary(), *flags, target]

    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError:
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": (
                f"nmap binary not found at {_resolve_nmap_binary()!r}; install nmap "
                f"or set ${NMAP_BIN_ENV}"
            ),
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
# JSON-RPC dispatch
# ---------------------------------------------------------------------------


async def _handle_request(
    request: dict[str, Any],
    write_line: Callable[[str], None],
) -> dict[str, Any]:
    """Dispatch a single JSON-RPC 2.0 request and return a final response dict.

    For ``run_nmap`` the function also writes streaming notifications via
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

    if tool_name == "run_nmap":
        result = await _run_nmap(arguments, write_line, req_id)
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
