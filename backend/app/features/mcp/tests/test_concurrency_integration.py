"""Integration test: two heavy runs against the SAME sandbox host serialize.

Proves end-to-end:
  - POST two ``run_httpx_heavy`` runs (``hold_seconds=2``) against the same
    sandbox host in ``async_mode=True``.
  - The first run reaches ``status='running'`` quickly.
  - The second run is observed as ``status='queued'`` (via ``GET /tool-queue``).
  - After the first run finishes (``status='completed'``), the second run
    transitions to ``running`` and then ``completed``.
  - Wall-clock ordering: second run's ``started_at`` >= first run's
    ``finished_at`` (serialization proved by timestamps).

Marked ``integration``: excluded from the default ``make test-backend`` run
(``addopts = -m 'not integration'`` in pyproject.toml).  Run explicitly with:

  cd backend && uv run pytest -m integration \\
    app/features/mcp/tests/test_concurrency_integration.py -v

Prerequisites:
  - Postgres reachable at the default compose DSN or ADEPTUS_TEST_DATABASE_URL.
  - Juice Shop (``make sandbox``) running on http://localhost:3000 — the
    ``run_httpx_heavy`` tool hits the sandbox to prove target reachability before
    holding the slot.
  - The ProjectDiscovery ``httpx`` binary installed on PATH (used by the MCP
    subprocess; the test skips if it is absent).
  - The ``httpx`` MCP server.py at ``mcp-servers/httpx/server.py``.

The test skips automatically (via ``pytest.skip``) when any prerequisite is
missing so it is safe to run on hosts without the full stack.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import Base, get_db, get_engine, get_sessionmaker
from app.core.errors import register_error_handlers
from app.features.auth import models as auth_models  # noqa: F401 — register ORM metadata
from app.features.auth import repository as auth_repo
from app.features.auth.router import router as auth_router
from app.features.engagements import models as eng_models  # noqa: F401 — register ORM metadata
from app.features.engagements import repository as eng_repo
from app.features.engagements.router import router as engagements_router
from app.features.mcp import concurrency, subprocess_manager
from app.features.mcp import models as mcp_models  # noqa: F401 — register ORM metadata
from app.features.mcp import registry as mcp_registry
from app.features.mcp import service as mcp_service
from app.features.mcp.models import ToolRun
from app.features.mcp.router import router as mcp_router

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_DSN = "postgresql+asyncpg://adeptus:adeptus@localhost:5432/adeptus"
_MEMBER_PW = "correcthorse"
_MEMBER_HASH = PasswordHasher().hash(_MEMBER_PW)

# SANDBOX host: must match _SANDBOX_HOSTS in service.py
_SANDBOX_TARGET = "http://localhost:3000"

# hold_seconds is small enough to keep the test fast but large enough that the
# second run is observable as queued before the first completes.
_HOLD_SECONDS = 2

# Resolve the httpx server.py path from this test file's location.
# test file: backend/app/features/mcp/tests/test_concurrency_integration.py
# server.py: mcp-servers/httpx/server.py  (5 parents up from tests/, then down)
_REPO_ROOT = Path(__file__).parents[5]
_HTTPX_SERVER = _REPO_ROOT / "mcp-servers" / "httpx" / "server.py"


def _dsn() -> str:
    return os.environ.get("ADEPTUS_TEST_DATABASE_URL") or _DEFAULT_DSN


# ---------------------------------------------------------------------------
# Guard helpers
# ---------------------------------------------------------------------------


def _check_pd_httpx_binary() -> None:
    """Skip if the ProjectDiscovery httpx binary is absent or not runnable."""
    binary = shutil.which("httpx")
    if binary is None:
        pytest.skip(
            "ProjectDiscovery httpx binary not installed on host (required by run_httpx_heavy)"
        )
    assert binary is not None  # narrow str | None → str for mypy

    probe = subprocess.run(
        [binary, "-version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    combined = probe.stdout + probe.stderr
    # Detect the Python httpx CLI (not PD httpx).
    if "pip install" in combined and "httpx[cli]" in combined:
        pytest.skip("Only the Python httpx CLI is installed; need ProjectDiscovery httpx")
    if probe.returncode != 0:
        pytest.skip(f"httpx binary -version exited non-zero: {combined!r}")


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
            timeout=5,
        )
        http_code = result.stdout.strip()
        if http_code not in ("200", "302", "301"):
            pytest.skip(
                f"Sandbox (http://localhost:3000) not reachable — got HTTP {http_code}. "
                "Run `make sandbox` first."
            )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pytest.skip("Cannot reach sandbox http://localhost:3000 (curl failed or timed out)")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def httpx_mcp_config(tmp_path: Path) -> Path:
    """Write a test-local mcp.yaml pointing at the real httpx server.py on the host.

    Includes both ``run_httpx`` (light) and ``run_httpx_heavy`` (heavy) so the
    full concurrency stack is exercised.
    """
    if not _HTTPX_SERVER.exists():
        pytest.skip(f"httpx server.py not found at {_HTTPX_SERVER}")

    config_content = f"""\
# Test-only MCP config generated by test_concurrency_integration.py.
# Uses sys.executable + absolute path so the test runs correctly on the host.
servers:
  - name: httpx
    command: {sys.executable}
    args:
      - {_HTTPX_SERVER}
    tools:
      - name: run_httpx
        weight: light
        capability_flags:
          - network
      - name: run_httpx_heavy
        weight: heavy
        capability_flags:
          - network
        arg_schema:
          type: object
          required: [target]
          properties:
            target: {{type: string}}
            hold_seconds: {{type: number, minimum: 1, maximum: 30, default: 2}}
"""
    config_path = tmp_path / "mcp_test.yaml"
    config_path.write_text(config_content)
    return config_path


@pytest_asyncio.fixture
async def pg_schema_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Session factory scoped to a throwaway Postgres schema.

    Mirrors the pattern from test_mcp_integration.py.  Skips if Postgres is
    not reachable.
    """
    monkeypatch.setenv("DATABASE_URL", _dsn())
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin_it")
    monkeypatch.setenv(
        "ADEPTUS_ADMIN_PASSWORD_HASH",
        "$argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$hashhashhashhashhashhashhashhashhashhashhas",
    )
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    schema = f"concurrency_it_{uuid.uuid4().hex[:12]}"
    admin_engine = create_async_engine(_dsn(), isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception as exc:  # noqa: BLE001
        await admin_engine.dispose()
        pytest.skip(f"Postgres not available for integration tests: {exc}")

    engine = create_async_engine(
        _dsn(),
        connect_args={"server_settings": {"search_path": schema}},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()
        get_settings.cache_clear()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()


def _make_mcp_app(
    factory: async_sessionmaker[AsyncSession],
    config_path: Path,
) -> FastAPI:
    """Build a minimal FastAPI app with auth + engagements + mcp routers.

    Custom lifespan: loads the registry from the test mcp.yaml, spawns the real
    httpx subprocess, and resets all in-process state on teardown.
    """

    @asynccontextmanager
    async def test_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        mcp_registry._reset_registry()  # noqa: SLF001
        subprocess_manager._reset_manager()  # noqa: SLF001
        concurrency._reset()  # noqa: SLF001
        mcp_service._reset_channels()  # noqa: SLF001

        mcp_registry.load_registry(config_path=str(config_path))
        await subprocess_manager.startup()

        yield

        await subprocess_manager.shutdown()
        mcp_registry._reset_registry()  # noqa: SLF001
        subprocess_manager._reset_manager()  # noqa: SLF001
        concurrency._reset()  # noqa: SLF001
        mcp_service._reset_channels()  # noqa: SLF001

    app = FastAPI(lifespan=test_lifespan)
    register_error_handlers(app)
    app.include_router(auth_router)
    app.include_router(engagements_router)
    app.include_router(mcp_router)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _poll_run_status(
    client: AsyncClient,
    tool_run_id: str,
    *,
    expected_status: str,
    timeout_seconds: float = 20.0,
    poll_interval: float = 0.25,
) -> dict[str, Any]:
    """Poll ``GET /api/v1/tool-runs/{id}`` until status matches ``expected_status``."""
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    last_body: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/api/v1/tool-runs/{tool_run_id}")
        assert resp.status_code == 200, f"GET tool-run failed: {resp.status_code} {resp.text}"
        body = cast(dict[str, Any], resp.json())
        last_body = body
        if body["status"] == expected_status:
            return body
        await asyncio.sleep(poll_interval)
    raise AssertionError(
        f"Timed out waiting for status={expected_status!r} on run {tool_run_id}; "
        f"last status={last_body.get('status')!r}"
    )


async def _poll_tool_queue(
    client: AsyncClient,
    engagement_id: str,
    *,
    expected_queued_count: int,
    timeout_seconds: float = 10.0,
    poll_interval: float = 0.2,
) -> dict[str, Any]:
    """Poll ``GET /tool-queue`` until ``queued_count`` matches."""
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    last_body: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/api/v1/engagements/{engagement_id}/tool-queue")
        assert resp.status_code == 200, f"GET tool-queue failed: {resp.status_code} {resp.text}"
        body = cast(dict[str, Any], resp.json())
        last_body = body
        if body["queued_count"] == expected_queued_count:
            return body
        await asyncio.sleep(poll_interval)
    raise AssertionError(
        f"Timed out waiting for queued_count={expected_queued_count} on engagement "
        f"{engagement_id}; last body={last_body!r}"
    )


# ---------------------------------------------------------------------------
# Integration test — same host serializes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_heavy_runs_same_host_serialize(
    pg_schema_factory: async_sessionmaker[AsyncSession],
    httpx_mcp_config: Path,
) -> None:
    """Two heavy runs against the same sandbox host run serially.

    Steps:
      1. Seed a member user; create an engagement; log in.
      2. POST first heavy run (async_mode=True) → should reach 'running'.
      3. POST second heavy run (async_mode=True) → should be observed as 'queued'
         via GET /tool-queue (queued_count == 1).
      4. Wait for first run to reach 'completed'.
      5. Wait for second run to reach 'completed'.
      6. Assert wall-clock ordering: second.started_at >= first.finished_at.
    """
    _check_pd_httpx_binary()
    _check_sandbox_reachable()

    # ---- step 1: seed member + engagement -----------------------------------
    async with pg_schema_factory() as session:
        member = await auth_repo.create_user(
            session, username="member_concurrency", password_hash=_MEMBER_HASH, role="user"
        )
        await session.commit()
        member_id = member.id

    async with pg_schema_factory() as session:
        engagement = await eng_repo.create_engagement(
            session,
            name="Concurrency Integration Test",
            scope="127.0.0.1/32",
            client_info=None,
            owner_id=member_id,  # type: ignore[arg-type]
        )
        await session.commit()
        engagement_id = str(engagement.id)

    # ---- build app and run the lifespan ------------------------------------
    app = _make_mcp_app(pg_schema_factory, httpx_mcp_config)

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
            # ---- login -------------------------------------------------------
            login_resp = await client.post(
                "/api/v1/auth/login",
                json={"username": "member_concurrency", "password": _MEMBER_PW},
            )
            assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"

            # ---- step 2: POST first heavy run --------------------------------
            run1_resp = await client.post(
                "/api/v1/tool-runs",
                json={
                    "engagement_id": engagement_id,
                    "server_name": "httpx",
                    "tool_name": "run_httpx_heavy",
                    "args": {"target": _SANDBOX_TARGET, "hold_seconds": _HOLD_SECONDS},
                    "async_mode": True,
                },
            )
            assert run1_resp.status_code == 202, (
                f"First heavy run POST failed ({run1_resp.status_code}): {run1_resp.text}"
            )
            run1_id = run1_resp.json()["tool_run_id"]

            # Wait for run 1 to reach 'running' (admitted to the pool).
            run1_running = await _poll_run_status(
                client, run1_id, expected_status="running", timeout_seconds=10.0
            )
            assert run1_running["status"] == "running", (
                f"Expected run1 to be running; got {run1_running['status']!r}"
            )

            # ---- step 3: POST second heavy run against the SAME host ---------
            run2_resp = await client.post(
                "/api/v1/tool-runs",
                json={
                    "engagement_id": engagement_id,
                    "server_name": "httpx",
                    "tool_name": "run_httpx_heavy",
                    "args": {"target": _SANDBOX_TARGET, "hold_seconds": _HOLD_SECONDS},
                    "async_mode": True,
                },
            )
            assert run2_resp.status_code == 202, (
                f"Second heavy run POST failed ({run2_resp.status_code}): {run2_resp.text}"
            )
            run2_id = run2_resp.json()["tool_run_id"]

            # The second run should be queued because run1 holds the per-host lock.
            run2_queued = await _poll_run_status(
                client, run2_id, expected_status="queued", timeout_seconds=8.0
            )
            assert run2_queued["status"] == "queued", (
                f"Expected run2 to be queued; got {run2_queued['status']!r}"
            )

            # ---- verify via GET /tool-queue ----------------------------------
            queue_snapshot = await _poll_tool_queue(
                client,
                engagement_id,
                expected_queued_count=1,
                timeout_seconds=5.0,
            )
            assert queue_snapshot["running_count"] >= 1, (
                f"Expected at least 1 running; got {queue_snapshot['running_count']}"
            )
            assert queue_snapshot["queued_count"] == 1, (
                f"Expected 1 queued; got {queue_snapshot['queued_count']}"
            )
            assert len(queue_snapshot["queued"]) == 1
            queued_item = queue_snapshot["queued"][0]
            assert queued_item["tool_run_id"] == run2_id
            assert queued_item["position"] == 1
            assert queued_item["reason"] in ("target_locked", "slot_full"), (
                f"Unexpected reason: {queued_item['reason']!r}"
            )

            # ---- step 4: wait for run 1 to complete -------------------------
            # hold_seconds=2 + httpx probe time; 20 s is generous.
            run1_done = await _poll_run_status(
                client, run1_id, expected_status="completed", timeout_seconds=20.0
            )
            assert run1_done["status"] == "completed"
            assert run1_done["finished_at"] is not None

            # ---- step 5: wait for run 2 to complete -------------------------
            # Run 2 should have been admitted once run 1 released the lock.
            run2_done = await _poll_run_status(
                client, run2_id, expected_status="completed", timeout_seconds=20.0
            )
            assert run2_done["status"] == "completed"
            assert run2_done["started_at"] is not None
            assert run2_done["finished_at"] is not None

    # ---- step 6: assert wall-clock serialization (outside lifespan) ---------
    # Verify from DB rows that second run started AFTER first run finished.
    async with pg_schema_factory() as session:
        row1 = (
            await session.execute(select(ToolRun).where(ToolRun.id == uuid.UUID(run1_id)))
        ).scalar_one_or_none()
        row2 = (
            await session.execute(select(ToolRun).where(ToolRun.id == uuid.UUID(run2_id)))
        ).scalar_one_or_none()

    assert row1 is not None, f"No tool_runs row for run1 id={run1_id}"
    assert row2 is not None, f"No tool_runs row for run2 id={run2_id}"
    assert row1.finished_at is not None, "run1.finished_at should be set"
    assert row2.started_at is not None, "run2.started_at should be set (admission time)"

    # Normalise to UTC-aware datetimes for comparison.
    run1_finished: datetime = cast(datetime, row1.finished_at)
    run2_started: datetime = cast(datetime, row2.started_at)
    if run1_finished.tzinfo is None:
        run1_finished = run1_finished.replace(tzinfo=UTC)
    if run2_started.tzinfo is None:
        run2_started = run2_started.replace(tzinfo=UTC)

    assert run2_started >= run1_finished, (
        f"Serialization violated: run2 started_at={run2_started.isoformat()} is BEFORE "
        f"run1 finished_at={run1_finished.isoformat()}"
    )
