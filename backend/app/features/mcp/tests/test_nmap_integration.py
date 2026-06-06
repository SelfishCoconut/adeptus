"""Integration test for the nmap MCP server (Slice 26).

Spawns the REAL nmap MCP server subprocess via ``subprocess_manager`` and runs an
actual nmap scan against the Juice Shop sandbox (exposed on http://localhost:3000
by ``make sandbox``), asserting a real open-port line comes back through the
JSON-RPC transport.

Gated by the ``integration`` marker (deselected in unit runs). Skips cleanly when
the nmap binary or the sandbox is unavailable, so it is safe on any host and only
exercises a real scan where both are present. The tool NEVER targets anything but
the sandbox (localhost).
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio

from app.features.mcp import concurrency, subprocess_manager
from app.features.mcp import registry as mcp_registry

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[5]
_NMAP_SERVER = _REPO_ROOT / "mcp-servers" / "nmap" / "server.py"

_NMAP_ONLY_CONFIG = """\
servers:
  - name: nmap
    command: python
    args:
      - {server_path}
    tools:
      - name: run_nmap
        weight: heavy
        capability_flags:
          - network
        arg_schema:
          type: object
          required: [target]
          properties:
            target: {{type: string}}
            flags: {{type: array, items: {{type: string}}}}
"""


def _check_nmap_binary() -> None:
    """Skip if no nmap binary is reachable for the spawned server."""
    if not (shutil.which("nmap") or Path("/usr/bin/nmap").exists()):
        pytest.skip("nmap binary not installed on host (required by run_nmap)")


def _check_sandbox_reachable() -> None:
    """Skip if Juice Shop (sandbox) is not reachable on http://localhost:3000."""
    try:
        result = subprocess.run(
            [
                "curl",
                "-s",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "--max-time",
                "3",
                "http://localhost:3000",
            ],
            capture_output=True,
            text=True,
            timeout=6,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover — env-dependent
        pytest.skip("Juice Shop sandbox not reachable on http://localhost:3000")
    if result.stdout.strip() in ("", "000"):
        pytest.skip("Juice Shop sandbox not reachable on http://localhost:3000")


@pytest_asyncio.fixture
async def nmap_manager(tmp_path: Path) -> AsyncGenerator[None, None]:
    """Load an nmap-only registry and spawn the real nmap subprocess."""
    config = tmp_path / "mcp_nmap.yaml"
    config.write_text(_NMAP_ONLY_CONFIG.format(server_path=_NMAP_SERVER))

    mcp_registry._reset_registry()  # noqa: SLF001
    subprocess_manager._reset_manager()  # noqa: SLF001
    concurrency._reset()  # noqa: SLF001

    mcp_registry.load_registry(config_path=str(config))
    await subprocess_manager.startup()
    try:
        yield
    finally:
        await subprocess_manager.shutdown()
        mcp_registry._reset_registry()  # noqa: SLF001
        subprocess_manager._reset_manager()  # noqa: SLF001
        concurrency._reset()  # noqa: SLF001


@pytest.mark.asyncio
async def test_nmap_scans_sandbox_and_returns_open_port(nmap_manager: None) -> None:
    """A real run_nmap against the sandbox returns port 3000 as open."""
    _check_nmap_binary()
    _check_sandbox_reachable()

    result = await subprocess_manager.send_tool_call(
        "nmap",
        "run_nmap",
        {"target": "localhost", "flags": ["-Pn", "-sT", "-p", "3000"]},
        timeout_seconds=60,
    )

    assert result.exit_code == 0, f"nmap failed: {result.stderr}"
    assert "Nmap scan report" in result.stdout
    assert "3000/tcp open" in result.stdout


@pytest.mark.asyncio
async def test_nmap_denylisted_flag_rejected(nmap_manager: None) -> None:
    """A denylisted flag (-iR, random internet targets) is refused by the server."""
    _check_nmap_binary()

    result = await subprocess_manager.send_tool_call(
        "nmap",
        "run_nmap",
        {"target": "localhost", "flags": ["-iR", "10"]},
        timeout_seconds=30,
    )

    assert result.exit_code == 1
    assert "disallowed flag" in result.stderr
