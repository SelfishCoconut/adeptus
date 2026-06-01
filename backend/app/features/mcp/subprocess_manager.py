"""MCP subprocess manager.

On startup, spawns one asyncio subprocess per configured MCP server using
stdio transport (JSON-RPC 2.0, newline-delimited).  Exposes:

    await startup()   — spawn all configured servers
    await shutdown()  — terminate all subprocesses cleanly
    await send_tool_call(server_name, tool_name, args, timeout_seconds)
                      — send a JSON-RPC 2.0 request and return McpRawResult
    get_server_status(server_name) -> "running" | "stopped"

JSON-RPC framing (Risk 2 from slice spec):
  - Write exactly one JSON line to stdin, ending with ``\\n``.
  - Read exactly one JSON line from stdout.

Domain exceptions raised:
  - McpServerNotFound  — server name is not in the registry
  - McpServerDown      — subprocess not running / died / timed out
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import AdeptusError
from app.features.mcp.registry import McpServerConfig, get_registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class McpServerNotFound(AdeptusError):
    """Raised when the requested server name is not in the registry."""

    def __init__(self, message: str = "MCP server not found") -> None:
        super().__init__(message)


class McpServerDown(AdeptusError):
    """Raised when the subprocess is not running, has died, or timed out."""

    def __init__(self, message: str = "MCP server is down") -> None:
        super().__init__(message)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class McpRawResult:
    """Raw result returned by an MCP tool call.

    ``exit_code`` mirrors the JSON-RPC result field ``exit_code`` that the
    shell-exec server embeds inside the result payload.  When the call itself
    succeeds at the transport level but the tool reports a non-zero exit, the
    caller (service layer) decides how to surface that.
    """

    exit_code: int
    stdout: str
    stderr: str


# ---------------------------------------------------------------------------
# Internal per-server state
# ---------------------------------------------------------------------------


@dataclass
class _ServerHandle:
    config: McpServerConfig
    process: asyncio.subprocess.Process | None = field(default=None)
    _next_id: int = field(default=1, init=False)

    @property
    def status(self) -> str:
        """Return ``'running'`` or ``'stopped'``."""
        if self.process is None:
            return "stopped"
        if self.process.returncode is not None:
            return "stopped"
        return "running"

    def next_id(self) -> int:
        id_ = self._next_id
        self._next_id += 1
        return id_


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_handles: dict[str, _ServerHandle] = {}


def _reset_manager() -> None:
    """Clear module-level state.  For use in tests only."""
    _handles.clear()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def startup() -> None:
    """Spawn one subprocess per server declared in the registry.

    Safe to call multiple times — already-running servers are skipped.

    Raises:
        ConfigError: If the registry has not been loaded yet.
    """
    registry = get_registry()
    for name, config in registry.items():
        if name in _handles and _handles[name].status == "running":
            logger.debug("MCP server %r already running, skipping spawn", name)
            continue
        handle = _ServerHandle(config=config)
        _handles[name] = handle
        await _spawn(handle)


async def shutdown() -> None:
    """Terminate all running subprocesses and clear the handle table."""
    for name, handle in list(_handles.items()):
        if handle.process is not None and handle.status == "running":
            logger.info("Terminating MCP server %r (pid=%s)", name, handle.process.pid)
            try:
                handle.process.terminate()
                await asyncio.wait_for(handle.process.wait(), timeout=5.0)
            except (TimeoutError, ProcessLookupError):
                logger.warning("Force-killing MCP server %r", name)
                try:
                    handle.process.kill()
                except ProcessLookupError:
                    pass
    _handles.clear()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_server_status(server_name: str) -> str:
    """Return ``'running'`` or ``'stopped'`` for a named server.

    The server name must exist in the registry (i.e. ``startup()`` must have
    been called first), otherwise ``'stopped'`` is returned.
    """
    handle = _handles.get(server_name)
    if handle is None:
        return "stopped"
    return handle.status


async def send_tool_call(
    server_name: str,
    tool_name: str,
    args: dict[str, Any],
    timeout_seconds: float,
) -> McpRawResult:
    """Send a JSON-RPC 2.0 tool call to the named server and return the result.

    Args:
        server_name: Must match a key in the registry.
        tool_name:   Name of the MCP tool to invoke.
        args:        Keyword arguments forwarded to the tool.
        timeout_seconds: Total wall-clock budget for the round-trip.

    Returns:
        McpRawResult with exit_code, stdout, stderr extracted from the
        JSON-RPC result payload.

    Raises:
        McpServerNotFound: ``server_name`` not in the registry.
        McpServerDown:     Subprocess not running, died, or timed out.
    """
    registry = get_registry()
    if server_name not in registry:
        raise McpServerNotFound(f"MCP server {server_name!r} is not in the registry")

    handle = _handles.get(server_name)
    if handle is None or handle.status != "running":
        raise McpServerDown(
            f"MCP server {server_name!r} is not running (call startup() before send_tool_call)"
        )

    process = handle.process
    assert process is not None  # guaranteed by status check above

    if process.stdin is None or process.stdout is None:
        raise McpServerDown(f"MCP server {server_name!r} has no stdio pipes")

    request_id = handle.next_id()
    request = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": args},
    }
    request_line = json.dumps(request) + "\n"

    try:
        process.stdin.write(request_line.encode())
        await process.stdin.drain()
    except (BrokenPipeError, ConnectionResetError) as exc:
        _mark_stopped(server_name)
        raise McpServerDown(f"MCP server {server_name!r} stdin pipe broken: {exc}") from exc

    # ---- read one response line with timeout --------------------------------
    try:
        raw_line = await asyncio.wait_for(process.stdout.readline(), timeout=timeout_seconds)
    except TimeoutError as exc:
        _mark_stopped(server_name)
        raise McpServerDown(
            f"MCP server {server_name!r} timed out after {timeout_seconds}s"
            f" waiting for tool {tool_name!r} response"
        ) from exc
    except (BrokenPipeError, ConnectionResetError) as exc:
        _mark_stopped(server_name)
        raise McpServerDown(f"MCP server {server_name!r} stdout pipe broken: {exc}") from exc

    # ---- check for subprocess death after read returned empty bytes ----------
    if not raw_line:
        _mark_stopped(server_name)
        raise McpServerDown(f"MCP server {server_name!r} closed stdout (process likely died)")

    # ---- parse JSON-RPC response --------------------------------------------
    try:
        response = json.loads(raw_line.decode())
    except json.JSONDecodeError as exc:
        raise McpServerDown(
            f"MCP server {server_name!r} returned non-JSON response: {raw_line!r}"
        ) from exc

    if "error" in response:
        error = response["error"]
        raise McpServerDown(f"MCP server {server_name!r} returned JSON-RPC error: {error}")

    result = response.get("result", {})
    return McpRawResult(
        exit_code=int(result.get("exit_code", 0)),
        stdout=str(result.get("stdout", "")),
        stderr=str(result.get("stderr", "")),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _spawn(handle: _ServerHandle) -> None:
    """Start the subprocess for the given handle and assign it."""
    config = handle.config
    cmd = [config.command, *config.args]
    logger.info("Spawning MCP server %r: %s", config.name, " ".join(cmd))
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        logger.error("MCP server %r command not found: %s", config.name, exc)
        # Leave handle.process as None; status will be 'stopped'.
        return
    except OSError as exc:
        logger.error("MCP server %r failed to start: %s", config.name, exc)
        return
    handle.process = process
    logger.info(
        "MCP server %r started (pid=%s)",
        config.name,
        process.pid,
    )


def _mark_stopped(server_name: str) -> None:
    """Record that a server has died by nulling its process handle.

    We do NOT remove the handle from ``_handles`` so callers can still
    query the status.
    """
    handle = _handles.get(server_name)
    if handle is not None:
        handle.process = None
