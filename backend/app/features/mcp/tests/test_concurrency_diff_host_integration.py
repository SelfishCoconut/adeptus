"""Integration test: two heavy runs against DIFFERENT sandbox hosts run concurrently.

Proves that the per-(engagement, target-host) lock does NOT couple runs against
different hosts — both can be in ``status='running'`` simultaneously when the
slot pool is not saturated (slot_limit=3 by default, only 2 slots used).

Marked ``integration``: excluded from the default ``make test-backend`` run
(``addopts = -m 'not integration'`` in pyproject.toml).  Run explicitly with:

  cd backend && uv run pytest -m integration \\
    app/features/mcp/tests/test_concurrency_diff_host_integration.py -v

Prerequisites: same as test_concurrency_integration.py — Postgres, Juice Shop
sandbox on http://localhost:3000 (reachable via both localhost and 127.0.0.1),
and the ProjectDiscovery httpx binary on PATH.

``localhost`` and ``127.0.0.1`` are both in the sandbox allow-list and both
resolve to the same Juice Shop instance; the concurrency model treats them as
distinct hosts (string equality on the resolved hostname after URL parsing) so
two runs against these two names acquire different per-host locks and may
proceed in parallel.
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

# Two sandbox hosts — distinct strings so the concurrency manager treats them
# as different per-host locks.  Both are in the sandbox allow-list.
_SANDBOX_HOST_A = "http://localhost:3000"
_SANDBOX_HOST_B = "http://127.0.0.1:3000"

# Small hold so the test is fast; large enough for both runs to reach 'running'
# before either finishes.
_HOLD_SECONDS = 2

_REPO_ROOT = Path(__file__).parents[5]
_HTTPX_SERVER = _REPO_ROOT / "mcp-servers" / "httpx" / "server.py"


def _dsn() -> str:
    return os.environ.get("ADEPTUS_TEST_DATABASE_URL") or _DEFAULT_DSN


# ---------------------------------------------------------------------------
# Guard helpers  (mirrors test_concurrency_integration.py)
# ---------------------------------------------------------------------------


def _check_pd_httpx_binary() -> None:
    binary = shutil.which("httpx")
    if binary is None:
        pytest.skip("ProjectDiscovery httpx binary not installed on host")
    assert binary is not None  # narrow str | None → str for mypy
    probe = subprocess.run([binary, "-version"], capture_output=True, text=True, timeout=10)
    combined = probe.stdout + probe.stderr
    if "pip install" in combined and "httpx[cli]" in combined:
        pytest.skip("Only the Python httpx CLI is installed; need ProjectDiscovery httpx")
    if probe.returncode != 0:
        pytest.skip(f"httpx binary -version exited non-zero: {combined!r}")


def _check_sandbox_reachable() -> None:
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
# Fixtures  (mirrors test_concurrency_integration.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def httpx_mcp_config(tmp_path: Path) -> Path:
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
            hold_seconds: {{type: number, minimum: 1, maximum: 30, default: 2}}
"""
    config_path = tmp_path / "mcp_test.yaml"
    config_path.write_text(config_content)
    return config_path


@pytest_asyncio.fixture
async def pg_schema_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    monkeypatch.setenv("DATABASE_URL", _dsn())
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin_it")
    monkeypatch.setenv(
        "ADEPTUS_ADMIN_PASSWORD_HASH",
        "$argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$hashhashhashhashhashhashhashhashhashhashhas",
    )
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    schema = f"concurrency_dh_{uuid.uuid4().hex[:12]}"
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
# Helpers  (mirrors test_concurrency_integration.py)
# ---------------------------------------------------------------------------


async def _poll_run_status(
    client: AsyncClient,
    tool_run_id: str,
    *,
    expected_status: str,
    timeout_seconds: float = 20.0,
    poll_interval: float = 0.25,
) -> dict[str, Any]:
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
# Integration test — different hosts run concurrently
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_heavy_runs_different_hosts_are_concurrent(
    pg_schema_factory: async_sessionmaker[AsyncSession],
    httpx_mcp_config: Path,
) -> None:
    """Two heavy runs against different sandbox hosts (localhost vs 127.0.0.1) run concurrently.

    Steps:
      1. Seed member + engagement; log in.
      2. POST two heavy runs in quick succession — one against ``localhost:3000``
         and one against ``127.0.0.1:3000``.
      3. Both runs must reach ``status='running'`` before either finishes.
      4. Both runs must eventually reach ``status='completed'``.

    The concurrency model treats ``localhost`` and ``127.0.0.1`` as different
    target hosts (string comparison on the resolved hostname), so they acquire
    different per-host locks and can run in parallel.
    """
    _check_pd_httpx_binary()
    _check_sandbox_reachable()

    # ---- step 1: seed member + engagement -----------------------------------
    async with pg_schema_factory() as session:
        member = await auth_repo.create_user(
            session,
            username="member_diffhost",
            password_hash=_MEMBER_HASH,
            role="user",
        )
        await session.commit()
        member_id = member.id

    async with pg_schema_factory() as session:
        engagement = await eng_repo.create_engagement(
            session,
            name="Diff-host Concurrency IT",
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
                json={"username": "member_diffhost", "password": _MEMBER_PW},
            )
            assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"

            # ---- step 2: POST both heavy runs in quick succession ------------
            run_a_resp = await client.post(
                "/api/v1/tool-runs",
                json={
                    "engagement_id": engagement_id,
                    "server_name": "httpx",
                    "tool_name": "run_httpx_heavy",
                    "args": {"target": _SANDBOX_HOST_A, "hold_seconds": _HOLD_SECONDS},
                    "async_mode": True,
                },
            )
            assert run_a_resp.status_code == 202, (
                f"Run A POST failed ({run_a_resp.status_code}): {run_a_resp.text}"
            )
            run_a_id = run_a_resp.json()["tool_run_id"]

            run_b_resp = await client.post(
                "/api/v1/tool-runs",
                json={
                    "engagement_id": engagement_id,
                    "server_name": "httpx",
                    "tool_name": "run_httpx_heavy",
                    "args": {"target": _SANDBOX_HOST_B, "hold_seconds": _HOLD_SECONDS},
                    "async_mode": True,
                },
            )
            assert run_b_resp.status_code == 202, (
                f"Run B POST failed ({run_b_resp.status_code}): {run_b_resp.text}"
            )
            run_b_id = run_b_resp.json()["tool_run_id"]

            # ---- step 3: both runs must reach 'running' before either finishes
            # Poll them concurrently via asyncio.gather.
            run_a_running, run_b_running = await asyncio.gather(
                _poll_run_status(client, run_a_id, expected_status="running", timeout_seconds=12.0),
                _poll_run_status(client, run_b_id, expected_status="running", timeout_seconds=12.0),
            )
            assert run_a_running["status"] == "running", (
                f"Expected run A to be running; got {run_a_running['status']!r}"
            )
            assert run_b_running["status"] == "running", (
                f"Expected run B to be running; got {run_b_running['status']!r}"
            )

            # ---- step 4: both runs complete ---------------------------------
            run_a_done, run_b_done = await asyncio.gather(
                _poll_run_status(
                    client, run_a_id, expected_status="completed", timeout_seconds=25.0
                ),
                _poll_run_status(
                    client, run_b_id, expected_status="completed", timeout_seconds=25.0
                ),
            )
            assert run_a_done["status"] == "completed", (
                f"Run A did not complete: {run_a_done['status']!r}"
            )
            assert run_b_done["status"] == "completed", (
                f"Run B did not complete: {run_b_done['status']!r}"
            )
