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
import re
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


# Flag handling is an ALLOWLIST, not a denylist. A denylist is unsafe for nmap for
# two reasons proven in security review:
#
#   1. nmap (GNU getopt) accepts any UNAMBIGUOUS ABBREVIATION of a long option, so a
#      denylist of full names (``--proxies``) is trivially bypassed (``--proxi``).
#   2. nmap treats ANY bare positional token as an additional scan target. Since the
#      backend sandbox guard only inspects ``args["target"]``, a bare host smuggled in
#      ``flags`` (``flags=["scanme.example"]``) would be scanned without ever being
#      guarded — a full sandbox-guard bypass.
#
# So: every token in ``flags`` must be either an exact allowed bare flag, or an allowed
# value-flag followed by a value matching its validator. Anything else — unknown/
# abbreviated flags, NSE (``-sC``/``--script*``), aggregate ``-A``, privileged scans
# (``-sS``/``-sU``/``-O``), file output (``-oN``…), egress/pivot (``--proxies``/``-b``),
# and **bare positional targets** — is rejected before exec. Matching is case-sensitive
# (nmap flags are: ``-sT`` connect ≠ ``-sS`` SYN), which is the safe direction.

# Allowed flags that take NO value.
_ALLOWED_BARE_FLAGS: frozenset[str] = frozenset(
    {
        "-Pn",  # skip host discovery
        "-n",  # no DNS resolution
        "-6",  # IPv6
        "-sT",  # TCP connect scan (unprivileged) — the ONLY allowed scan type
        "-sV",  # service/version detection
        "-F",  # fast (fewer ports)
        "--open",  # only show open ports
        "--reason",  # show reason for state
        "-T0",
        "-T1",
        "-T2",
        "-T3",
        "-T4",
        "-T5",  # timing templates
        "-v",
        "-vv",
        "-d",  # verbosity / debug
    }
)

# Allowed flags that consume the NEXT token (or an ``=value``) as a value; the value
# must match the validator so a hostname can never be smuggled in as a "value".
_ALLOWED_VALUE_FLAGS: dict[str, re.Pattern[str]] = {
    "-p": re.compile(r"\A[0-9,\-]+\Z"),  # port spec: digits, commas, dashes
    "--top-ports": re.compile(r"\A[0-9]+\Z"),
    "--version-intensity": re.compile(r"\A[0-9]\Z"),
    "--max-retries": re.compile(r"\A[0-9]+\Z"),
    "--min-rate": re.compile(r"\A[0-9]+\Z"),
    "--max-rate": re.compile(r"\A[0-9]+\Z"),
    "--host-timeout": re.compile(r"\A[0-9]+(ms|s|m|h)?\Z"),
}

_MAX_TIMEOUT_SECONDS: float = 600.0
_MIN_TIMEOUT_SECONDS: float = 1.0


def _validate_flags(flags: list[str]) -> tuple[list[str], str | None]:
    """Validate caller flags against the allowlist.

    Returns ``(validated_flags, None)`` on success or ``([], error_message)`` on the
    first offending token. Because every token must be a known flag or a validated
    value, no bare positional can pass — so a caller cannot smuggle a second nmap
    target past the single-``target`` sandbox guard.
    """
    validated: list[str] = []
    i = 0
    n = len(flags)
    while i < n:
        tok = flags[i]
        if tok in _ALLOWED_BARE_FLAGS:
            validated.append(tok)
            i += 1
            continue
        # Long-option "--flag=value" form.
        if tok.startswith("--") and "=" in tok:
            name, _, val = tok.partition("=")
            pattern = _ALLOWED_VALUE_FLAGS.get(name)
            if pattern is not None and pattern.match(val):
                validated.append(tok)
                i += 1
                continue
            return [], f"disallowed or malformed flag: {tok!r}"
        # Two-token "--flag value" / "-p value" form.
        if tok in _ALLOWED_VALUE_FLAGS:
            if i + 1 >= n:
                return [], f"flag {tok!r} is missing its value"
            val = flags[i + 1]
            if not _ALLOWED_VALUE_FLAGS[tok].match(val):
                return [], f"flag {tok!r} has a disallowed value: {val!r}"
            validated.extend([tok, val])
            i += 2
            continue
        # Unknown flag, abbreviation, or a bare positional target.
        return [], f"disallowed flag or argument: {tok!r}"
    return validated, None


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

    # Allowlist-validate every flag (see _validate_flags). This both blocks
    # capability/egress/NSE escape and prevents a bare positional from smuggling a
    # second nmap target past the single-``target`` sandbox guard.
    validated_flags, flag_error = _validate_flags(flags)
    if flag_error is not None:
        return {
            "exit_code": 1,
            "stdout": "",
            "stderr": f"run_nmap rejected {flag_error}",
        }

    # Clamp the timeout (defense in depth — the manifest schema caps it, but a direct
    # JSON-RPC caller could pass any value).
    raw_timeout = float(arguments.get("timeout_seconds", 120))
    timeout_seconds: float = max(_MIN_TIMEOUT_SECONDS, min(_MAX_TIMEOUT_SECONDS, raw_timeout))

    # nmap takes its single target as the trailing positional arg; all flags are
    # allowlist-validated above, so no extra target can be present. stdin is routed
    # from /dev/null so the child can never block on (or read) the MCP server's stdin.
    nmap_bin = _resolve_nmap_binary()
    argv: list[str] = [nmap_bin, *validated_flags, target]

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
                f"nmap binary not found at {nmap_bin!r}; install nmap or set ${NMAP_BIN_ENV}"
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
