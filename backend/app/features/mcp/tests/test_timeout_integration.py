"""Integration test: timeout enters awaiting_decision and frees the slot.

Proves the Q1 invariant end-to-end (Decision 6 / Risk 7):

  - A heavy run whose ``hold_seconds`` exceeds its ``timeout_seconds`` enters
    ``awaiting_decision`` — *and* releases its concurrency slot + host lock
    back to the FIFO queue so that a same-host run queued behind it can advance
    to ``running`` while the human's prompt is still open.
  - Posting a ``kill`` timeout-decision ends the run as ``killed``.

Specifically:
  1. POST a heavy run with timeout_seconds=3, hold_seconds=20 → wait for
     ``awaiting_decision`` (timeout fires before hold completes).
  2. Concurrently, POST a second same-host heavy run that was queued behind the
     first → assert it transitions to ``running`` while run 1 is still in
     ``awaiting_decision`` (proves the slot was released without the queue being
     blocked on the human's answer).
  3. POST ``/api/v1/tool-runs/{id}/timeout-decision`` {decision: "kill"} on run 1
     → assert 200.
  4. Assert run 1 reaches ``status='killed'``.

(Extend/wait re-acquire is covered by the fake-clock unit tests in
test_mcp_service_timeout.py; this integration test focuses on the Q1
slot-release-does-not-block-queue round-trip, which is the invariant the human
stressed.)

Marked ``integration``: excluded from the default ``make test-backend`` run
(``addopts = -m 'not integration'`` in pyproject.toml).  Run explicitly with:

  cd backend && uv run pytest -m integration \\
    app/features/mcp/tests/test_timeout_integration.py -v

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

# Run 1: times out before hold completes → enters awaiting_decision.
# timeout_seconds must be less than hold_seconds to trigger the timeout path.
# Using 4 s timeout / 30 s hold to give the httpx probe time to start while
# being reliably shorter than the hold.
_RUN1_TIMEOUT_SECONDS = 4
_HOLD_SECONDS = 30

# Run 2: just needs to be long enough to stay running while we check run 1.
_RUN2_HOLD_SECONDS = 20

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

    schema = f"timeout_it_{uuid.uuid4().hex[:12]}"
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
    timeout_seconds: float = 30.0,
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
# Integration test — timeout releases slot; queued run advances; kill resolves
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_releases_slot_queued_run_advances_then_kill(
    pg_schema_factory: async_sessionmaker[AsyncSession],
    httpx_mcp_config: Path,
) -> None:
    """Timeout enters awaiting_decision, frees slot (queued run advances), kill ends it.

    This test proves the Q1 invariant (Decision 6):
      - A run that times out RELEASES its concurrency slot + host lock so the
        FIFO queue can advance — waiting on a human answer must never block the
        queue.
      - The run enters 'awaiting_decision' and stays there until answered.
      - A same-host run queued behind it advances to 'running' while it waits.
      - Posting a 'kill' timeout-decision ends the timed-out run as 'killed'.

    Steps:
      1. Seed member + engagement; log in.
      2. POST run 1 with timeout_seconds=4, hold_seconds=30 → wait for 'running'.
      3. POST run 2 (same host, longer hold) → it queues because run 1 holds the
         host lock.
      4. Wait for run 1 to hit 'awaiting_decision' (timeout fires after 4 s).
      5. Assert run 2 transitions to 'running' while run 1 is still
         'awaiting_decision' — this is the key Q1 assertion.
      6. POST /tool-runs/{run1_id}/timeout-decision {decision: "kill"} → 200.
      7. Assert run 1 reaches 'killed'.
    """
    _check_pd_httpx_binary()
    _check_sandbox_reachable()

    # ---- step 1: seed member + engagement ------------------------------------
    async with pg_schema_factory() as session:
        member = await auth_repo.create_user(
            session, username="member_timeout_it", password_hash=_MEMBER_HASH, role="user"
        )
        await session.commit()
        member_id = member.id

    async with pg_schema_factory() as session:
        engagement = await eng_repo.create_engagement(
            session,
            name="Timeout Integration Test",
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
                json={"username": "member_timeout_it", "password": _MEMBER_PW},
            )
            assert login_resp.status_code == 200, f"Login failed: {login_resp.text}"

            # ---- step 2: POST run 1 (will time out) --------------------------
            run1_resp = await client.post(
                "/api/v1/tool-runs",
                json={
                    "engagement_id": engagement_id,
                    "server_name": "httpx",
                    "tool_name": "run_httpx_heavy",
                    "args": {"target": _SANDBOX_TARGET, "hold_seconds": _HOLD_SECONDS},
                    "timeout_seconds": _RUN1_TIMEOUT_SECONDS,
                    "async_mode": True,
                },
            )
            assert run1_resp.status_code == 202, (
                f"Run 1 POST failed ({run1_resp.status_code}): {run1_resp.text}"
            )
            run1_id = run1_resp.json()["tool_run_id"]

            # Wait for run 1 to be admitted and running.
            await _poll_run_status(client, run1_id, expected_status="running", timeout_seconds=12.0)

            # ---- step 3: POST run 2 → should queue ----------------------------
            run2_resp = await client.post(
                "/api/v1/tool-runs",
                json={
                    "engagement_id": engagement_id,
                    "server_name": "httpx",
                    "tool_name": "run_httpx_heavy",
                    "args": {"target": _SANDBOX_TARGET, "hold_seconds": _RUN2_HOLD_SECONDS},
                    "async_mode": True,
                },
            )
            assert run2_resp.status_code == 202, (
                f"Run 2 POST failed ({run2_resp.status_code}): {run2_resp.text}"
            )
            run2_id = run2_resp.json()["tool_run_id"]

            # Run 2 should queue because run 1 holds the host lock.
            await _poll_run_status(client, run2_id, expected_status="queued", timeout_seconds=8.0)

            # ---- step 4: wait for run 1 to hit awaiting_decision --------------
            # The timeout fires after _RUN1_TIMEOUT_SECONDS seconds.
            # Give generous headroom for the httpx probe startup time.
            run1_awaiting = await _poll_run_status(
                client,
                run1_id,
                expected_status="awaiting_decision",
                timeout_seconds=30.0,
            )
            assert run1_awaiting["status"] == "awaiting_decision", (
                f"Expected run1 to be 'awaiting_decision' after timeout; "
                f"got {run1_awaiting['status']!r}"
            )

            # ---- step 5: assert run 2 advances to 'running' ------------------
            # Key Q1 assertion: the slot + host lock were released when run 1
            # entered awaiting_decision, so run 2 can now be admitted — EVEN
            # THOUGH run 1's timeout prompt is still open and unanswered.
            run2_running = await _poll_run_status(
                client,
                run2_id,
                expected_status="running",
                timeout_seconds=20.0,
            )
            assert run2_running["status"] == "running", (
                f"Expected run2 to advance to 'running' while run1 is awaiting_decision; "
                f"got {run2_running['status']!r} — this means the slot was NOT released "
                f"when run1 entered awaiting_decision (Q1 invariant violated)"
            )

            # Confirm run 1 is still awaiting (the prompt is open indefinitely).
            run1_check = cast(
                dict[str, Any],
                (await client.get(f"/api/v1/tool-runs/{run1_id}")).json(),
            )
            assert run1_check["status"] == "awaiting_decision", (
                f"Expected run1 to still be 'awaiting_decision' while run2 is running; "
                f"got {run1_check['status']!r}"
            )

            # ---- step 6: POST kill timeout-decision on run 1 -----------------
            decision_resp = await client.post(
                f"/api/v1/tool-runs/{run1_id}/timeout-decision",
                json={"decision": "kill"},
            )
            assert decision_resp.status_code == 200, (
                f"timeout-decision POST failed ({decision_resp.status_code}): {decision_resp.text}"
            )

            # ---- step 7: assert run 1 reaches 'killed' -----------------------
            run1_killed = await _poll_run_status(
                client, run1_id, expected_status="killed", timeout_seconds=10.0
            )
            assert run1_killed["status"] == "killed", (
                f"Expected run1 to be 'killed' after kill decision; got {run1_killed['status']!r}"
            )
