"""Integration test: engagement-wide pause kills in-flight runs and blocks new ones.

Proves end-to-end:

  1. Start one running heavy run + one queued heavy run (same host) against the
     sandbox.
  2. POST ``/api/v1/engagements/{id}/pause`` with ``{paused: true}``.
  3. Assert the response body reports ``killed_running >= 1`` and ``dequeued >= 1``
     and ``paused == true``.
  4. Assert both runs reach ``status='killed'``.
  5. Assert a subsequent POST ``/api/v1/tool-runs`` returns **409** (engagement
     paused — new runs blocked).
  6. POST ``/api/v1/engagements/{id}/pause`` with ``{paused: false}`` to resume.
  7. Assert a new heavy run POSTed after resume succeeds (202, not 409).

Marked ``integration``: excluded from the default ``make test-backend`` run
(``addopts = -m 'not integration'`` in pyproject.toml).  Run explicitly with:

  cd backend && uv run pytest -m integration \\
    app/features/mcp/tests/test_pause_integration.py -v

Prerequisites:
  - Postgres reachable at the default compose DSN or ADEPTUS_TEST_DATABASE_URL.
  - Juice Shop (``make sandbox``) running on http://localhost:3000.
  - ProjectDiscovery ``httpx`` binary installed on PATH.
  - ``mcp-servers/httpx/server.py`` present in the repo.

The test skips automatically (via ``pytest.skip``) when any prerequisite is
missing.
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
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
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
from app.features.mcp.router import router as mcp_router

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_DSN = "postgresql+asyncpg://adeptus:adeptus@localhost:5432/adeptus"
_MEMBER_PW = "correcthorse"
_MEMBER_HASH = PasswordHasher().hash(_MEMBER_PW)

_SANDBOX_TARGET = "http://localhost:3000"

# Long enough that neither run completes on its own during the test.
_HOLD_SECONDS = 20

_REPO_ROOT = Path(__file__).parents[5]
_HTTPX_SERVER = _REPO_ROOT / "mcp-servers" / "httpx" / "server.py"


def _dsn() -> str:
    return os.environ.get("ADEPTUS_TEST_DATABASE_URL") or _DEFAULT_DSN


# ---------------------------------------------------------------------------
# Guard helpers (mirrors test_concurrency_integration.py)
# ---------------------------------------------------------------------------


def _check_pd_httpx_binary() -> None:
    """Skip if the ProjectDiscovery httpx binary is absent or not runnable."""
    binary = shutil.which("httpx")
    if binary is None:
        pytest.skip(
            "ProjectDiscovery httpx binary not installed on host (required by run_httpx_heavy)"
        )
    assert binary is not None
    probe = subprocess.run([binary, "-version"], capture_output=True, text=True, timeout=10)
    combined = probe.stdout + probe.stderr
    if "pip install" in combined and "httpx[cli]" in combined:
        pytest.skip("Only the Python httpx CLI is installed; need ProjectDiscovery httpx")
    if probe.returncode != 0:
        pytest.skip(f"httpx binary -version exited non-zero: {combined!r}")


def _check_sandbox_reachable() -> None:
    """Skip if Juice Shop is not reachable on http://localhost:3000."""
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
    """Write a test-local mcp.yaml pointing at the real httpx server.py."""
    if not _HTTPX_SERVER.exists():
        pytest.skip(f"httpx server.py not found at {_HTTPX_SERVER}")

    config_content = f"""\
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
            hold_seconds: {{type: number, minimum: 1, maximum: 60, default: 2}}
"""
    config_path = tmp_path / "mcp_test.yaml"
    config_path.write_text(config_content)
    return config_path


@pytest_asyncio.fixture
async def pg_schema_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Session factory scoped to a throwaway Postgres schema."""
    monkeypatch.setenv("DATABASE_URL", _dsn())
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin_it")
    monkeypatch.setenv(
        "ADEPTUS_ADMIN_PASSWORD_HASH",
        "$argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$hashhashhashhashhashhashhashhashhashhashhas",
    )
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    schema = f"pause_it_{uuid.uuid4().hex[:12]}"
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
    """Build a minimal FastAPI app with auth + engagements + mcp routers."""

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
    """Poll GET /api/v1/tool-runs/{id} until status matches expected_status."""
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


# ---------------------------------------------------------------------------
# Integration test — pause kills running + queued runs and blocks new ones
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_kills_running_and_queued_and_blocks_new_runs(
    pg_schema_factory: async_sessionmaker[AsyncSession],
    httpx_mcp_config: Path,
) -> None:
    """Engagement pause kills in-flight runs, blocks new ones, and resume restores.

    Steps:
      1. Seed member + engagement; log in.
      2. POST first heavy run → wait for 'running'.
      3. POST second heavy run (same host) → wait for 'queued'.
      4. POST /engagements/{id}/pause {paused: true} → assert 200 with
         killed_running >= 1, dequeued >= 1, paused == true.
      5. Assert both runs reach 'killed'.
      6. POST /tool-runs → assert 409 (engagement is paused).
      7. POST /engagements/{id}/pause {paused: false} → assert 200, paused == false.
      8. POST /tool-runs again → assert 202 (resume worked).
    """
    _check_pd_httpx_binary()
    _check_sandbox_reachable()

    # ---- step 1: seed member + engagement ------------------------------------
    async with pg_schema_factory() as session:
        member = await auth_repo.create_user(
            session, username="member_pause_it", password_hash=_MEMBER_HASH, role="user"
        )
        await session.commit()
        member_id = member.id

    async with pg_schema_factory() as session:
        engagement = await eng_repo.create_engagement(
            session,
            name="Pause Integration Test",
            scope="127.0.0.1/32",
            client_info=None,
            owner_id=member_id,  # type: ignore[arg-type]
        )
        await session.commit()
        engagement_id = str(engagement.id)

    app = _make_mcp_app(pg_schema_factory, httpx_mcp_config)

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
            # ---- login -------------------------------------------------------
            login_resp = await client.post(
                "/api/v1/auth/login",
                json={"username": "member_pause_it", "password": _MEMBER_PW},
            )
            assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"

            # ---- step 2: POST first heavy run (long hold) --------------------
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

            await _poll_run_status(client, run1_id, expected_status="running", timeout_seconds=12.0)

            # ---- step 3: POST second heavy run → should queue ----------------
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

            await _poll_run_status(client, run2_id, expected_status="queued", timeout_seconds=8.0)

            # ---- step 4: pause the engagement --------------------------------
            pause_resp = await client.post(
                f"/api/v1/engagements/{engagement_id}/pause",
                json={"paused": True},
            )
            assert pause_resp.status_code == 200, (
                f"Pause POST failed ({pause_resp.status_code}): {pause_resp.text}"
            )
            pause_body = cast(dict[str, Any], pause_resp.json())
            assert pause_body["paused"] is True, (
                f"Expected paused=True; got {pause_body['paused']!r}"
            )
            assert pause_body["killed_running"] >= 1, (
                f"Expected killed_running >= 1 (the running run); "
                f"got {pause_body['killed_running']!r}"
            )
            assert pause_body["dequeued"] >= 1, (
                f"Expected dequeued >= 1 (the queued run); got {pause_body['dequeued']!r}"
            )

            # ---- step 5: assert both runs reach 'killed' ---------------------
            run1_killed = await _poll_run_status(
                client, run1_id, expected_status="killed", timeout_seconds=10.0
            )
            assert run1_killed["status"] == "killed", (
                f"Expected run1 to be 'killed' after pause; got {run1_killed['status']!r}"
            )

            run2_killed = await _poll_run_status(
                client, run2_id, expected_status="killed", timeout_seconds=10.0
            )
            assert run2_killed["status"] == "killed", (
                f"Expected run2 to be 'killed' after pause (dequeued); "
                f"got {run2_killed['status']!r}"
            )

            # ---- step 6: assert a new run is rejected 409 while paused ------
            new_run_paused_resp = await client.post(
                "/api/v1/tool-runs",
                json={
                    "engagement_id": engagement_id,
                    "server_name": "httpx",
                    "tool_name": "run_httpx_heavy",
                    "args": {"target": _SANDBOX_TARGET, "hold_seconds": 2},
                    "async_mode": True,
                },
            )
            assert new_run_paused_resp.status_code == 409, (
                f"Expected 409 while paused; got {new_run_paused_resp.status_code}: "
                f"{new_run_paused_resp.text}"
            )

            # ---- step 7: resume the engagement -------------------------------
            resume_resp = await client.post(
                f"/api/v1/engagements/{engagement_id}/pause",
                json={"paused": False},
            )
            assert resume_resp.status_code == 200, (
                f"Resume POST failed ({resume_resp.status_code}): {resume_resp.text}"
            )
            resume_body = cast(dict[str, Any], resume_resp.json())
            assert resume_body["paused"] is False, (
                f"Expected paused=False after resume; got {resume_body['paused']!r}"
            )

            # ---- step 8: assert a new run succeeds after resume --------------
            new_run_resumed_resp = await client.post(
                "/api/v1/tool-runs",
                json={
                    "engagement_id": engagement_id,
                    "server_name": "httpx",
                    "tool_name": "run_httpx_heavy",
                    "args": {"target": _SANDBOX_TARGET, "hold_seconds": 2},
                    "async_mode": True,
                },
            )
            assert new_run_resumed_resp.status_code == 202, (
                f"Expected 202 after resume; got {new_run_resumed_resp.status_code}: "
                f"{new_run_resumed_resp.text}"
            )
