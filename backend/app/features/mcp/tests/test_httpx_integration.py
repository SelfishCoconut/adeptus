"""Integration test: assert the ProjectDiscovery httpx binary is runnable.

This test proves that the `httpx` CLI binary (ProjectDiscovery httpx, the fast
HTTP probing tool — NOT the Python httpx library) is installed on PATH and
produces version output when invoked.

Marked ``integration``: excluded from the default ``make test-backend`` run
(addopts = -m 'not integration' in pyproject.toml).  Run explicitly with:

  cd backend && uv run pytest -m integration app/features/mcp/tests/test_httpx_integration.py -v

The test guards with a helper that detects whether the ``httpx`` binary on PATH
is the ProjectDiscovery Go binary (not the Python httpx CLI that may also be
installed as a dev dependency).  When the PD binary is absent the tests skip
cleanly so they are green on dev hosts and CI without the binary (they run
green inside the backend container image where the Dockerfile installs it).
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _pd_httpx_path() -> str | None:
    """Return the path to the ProjectDiscovery httpx binary, or None.

    The Python `httpx` package also installs an `httpx` script on PATH.  We
    distinguish PD httpx from the Python httpx CLI by probing the output: PD
    httpx prints a version string that includes "Current Version" or exits 0
    with version info, while the Python CLI emits a pip-install suggestion when
    its optional CLI deps are missing.

    Returns the binary path when the PD binary is detected, otherwise None.
    """
    binary = shutil.which("httpx")
    if binary is None:
        return None

    # Probe the binary.
    probe = subprocess.run(
        [binary, "-version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    combined = probe.stdout + probe.stderr

    # The Python httpx CLI emits this message when its cli deps are absent.
    if "pip install" in combined and "httpx[cli]" in combined:
        return None

    # PD httpx exits 0 and prints something like "Current Version: 1.x.y"
    # or at minimum does not print a pip-install suggestion.
    if probe.returncode == 0:
        return binary

    # PD httpx may also accept --version
    probe2 = subprocess.run(
        [binary, "--version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if probe2.returncode == 0:
        combined2 = probe2.stdout + probe2.stderr
        if "pip install" not in combined2:
            return binary

    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pd_httpx_binary_is_runnable() -> None:
    """ProjectDiscovery httpx binary on PATH exits 0 and prints version info.

    ProjectDiscovery httpx uses single-dash ``-version``; if that exits non-zero
    the test also tries ``--version`` as fallback.
    """
    pd_httpx = _pd_httpx_path()
    if pd_httpx is None:
        pytest.skip("ProjectDiscovery httpx binary not installed on host")
    assert pd_httpx is not None  # narrow for the type-checker after the skip guard

    proc = await asyncio.create_subprocess_exec(
        pd_httpx,
        "-version",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    exit_code = proc.returncode

    if exit_code != 0:
        proc2 = await asyncio.create_subprocess_exec(
            pd_httpx,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc2.communicate()
        exit_code = proc2.returncode

    combined = (stdout_bytes + stderr_bytes).decode(errors="replace")
    assert exit_code == 0, f"httpx -version exited with {exit_code}; output: {combined!r}"
    # ProjectDiscovery httpx prints something like "Current Version: 1.6.10"
    assert combined.strip(), "httpx -version produced no output"


def test_pd_httpx_binary_sync_runnable() -> None:
    """Synchronous variant using subprocess.run — guards the same binary.

    Kept as a sync fallback so the test file exercises both async and sync
    subprocess invocation patterns that the rest of the MCP suite uses.
    """
    pd_httpx = _pd_httpx_path()
    if pd_httpx is None:
        pytest.skip("ProjectDiscovery httpx binary not installed on host")
    assert pd_httpx is not None  # narrow for the type-checker after the skip guard

    result = subprocess.run(
        [pd_httpx, "-version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    combined = result.stdout + result.stderr

    if result.returncode != 0:
        result = subprocess.run(
            [pd_httpx, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        combined = result.stdout + result.stderr

    assert result.returncode == 0, (
        f"httpx -version exited {result.returncode}; output: {combined!r}"
    )
    assert combined.strip(), "httpx -version produced no output"
