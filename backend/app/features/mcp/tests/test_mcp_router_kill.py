"""Router-layer tests for the Slice 06 kill + timeout-decision endpoints.

Tests:
  POST /api/v1/tool-runs/{id}/kill
    - 200 for a member (run result returned)
    - 404 for a non-member / unknown run (no existence disclosure §17.1)
    - 200 idempotent on an already-completed run
    - 200 on an awaiting-decision run

  POST /api/v1/tool-runs/{id}/timeout-decision
    - 200 (member, run awaiting; decision forwarded)
    - 404 (non-member)
    - 409 (run not awaiting a decision — service raises TimeoutDecisionConflict)

  POST /api/v1/tool-runs (existing)
    - 409 when engagement is paused (EngagementPaused → inline JSONResponse)

All calls to service.kill_tool_run / service.submit_timeout_decision /
service.execute_tool_run are mocked; the test DB + auth layer are real so the
session cookie and get_current_user dependency exercise the real auth path.
"""

from __future__ import annotations

import datetime
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, Column, ColumnDefault, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import Base, get_db
from app.core.errors import register_error_handlers
from app.features.auth import models as auth_models
from app.features.auth import repository as auth_repo
from app.features.auth.router import router as auth_router
from app.features.engagements import models as eng_models  # noqa: F401 — ORM table registration
from app.features.mcp import concurrency, service
from app.features.mcp import models as mcp_models  # noqa: F401 — ORM table registration
from app.features.mcp.concurrency import EngagementPaused
from app.features.mcp.router import router as mcp_router
from app.features.mcp.schemas import ToolRunResult, ToolRunStatus
from app.features.mcp.service import EngagementNotFound, TimeoutDecisionConflict

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_hasher = PasswordHasher()


def _now() -> datetime.datetime:
    return datetime.datetime(2026, 6, 1, 12, 0, 0, tzinfo=datetime.UTC)


def _make_tool_run_result(
    *,
    engagement_id: UUID | None = None,
    status: ToolRunStatus = "running",
) -> ToolRunResult:
    eid = engagement_id or uuid4()
    return ToolRunResult(
        tool_run_id=uuid4(),
        engagement_id=eid,
        server_name="httpx",
        tool_name="run_httpx",
        exit_code=None,
        stdout="",
        stderr="",
        started_at=_now(),
        finished_at=None,
        status=status,
    )


def _make_completed_result(*, engagement_id: UUID | None = None) -> ToolRunResult:
    eid = engagement_id or uuid4()
    return ToolRunResult(
        tool_run_id=uuid4(),
        engagement_id=eid,
        server_name="httpx",
        tool_name="run_httpx",
        exit_code=0,
        stdout="done",
        stderr="",
        started_at=_now(),
        finished_at=_now(),
        status="completed",
    )


def _make_killed_result(*, engagement_id: UUID | None = None) -> ToolRunResult:
    eid = engagement_id or uuid4()
    return ToolRunResult(
        tool_run_id=uuid4(),
        engagement_id=eid,
        server_name="httpx",
        tool_name="run_httpx",
        exit_code=1,
        stdout="",
        stderr="killed by user",
        started_at=_now(),
        finished_at=_now(),
        status="killed",
    )


# ---------------------------------------------------------------------------
# App + DB fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app_and_db(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[FastAPI, async_sessionmaker[AsyncSession]], None]:
    """Test FastAPI app backed by a fresh SQLite in-memory database."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin")
    monkeypatch.setenv(
        "ADEPTUS_ADMIN_PASSWORD_HASH",
        "$argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )

    get_settings.cache_clear()

    # Patch Postgres-specific column types for SQLite compatibility.
    user_id_col: Column = auth_models.User.__table__.c.id  # type: ignore[assignment]
    user_id_col.default = ColumnDefault(uuid4)

    ip_col: Column = auth_models.Session.__table__.c.ip  # type: ignore[assignment]
    ip_col.type = Text()

    eng_id_col: Column = eng_models.Engagement.__table__.c.id  # type: ignore[assignment]
    eng_id_col.default = ColumnDefault(uuid4)

    tool_run_id_col: Column = mcp_models.ToolRun.__table__.c.id  # type: ignore[assignment]
    tool_run_id_col.default = ColumnDefault(uuid4)

    args_col: Column = mcp_models.ToolRun.__table__.c.args  # type: ignore[assignment]
    args_col.type = JSON()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(mcp_router)
    register_error_handlers(app)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    # Reset in-process concurrency + pub-sub state before each test.
    concurrency._reset()
    service._reset_channels()

    yield app, factory

    # Teardown: reset again to avoid state bleed.
    concurrency._reset()
    service._reset_channels()

    get_settings.cache_clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def regular_user(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> auth_models.User:
    """Insert a regular (non-admin) user directly into the test DB."""
    _, factory = app_and_db
    pw_hash = _hasher.hash("secretpass")
    async with factory() as session:
        user = await auth_repo.create_user(
            session,
            username="regular",
            password_hash=pw_hash,
            role="user",
        )
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def regular_client(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    regular_user: auth_models.User,
) -> AsyncGenerator[AsyncClient, None]:
    """Authenticated AsyncClient logged in as a regular (non-admin) user."""
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "regular", "password": "secretpass"},
        )
        assert resp.status_code == 200, resp.text
        yield client


# ---------------------------------------------------------------------------
# Tests — POST /api/v1/tool-runs/{id}/kill
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_200_for_member(
    regular_client: AsyncClient,
) -> None:
    """A member receives 200 with the ToolRunResult when killing a running run."""
    run_id = uuid4()
    result = _make_tool_run_result(status="running")

    with patch(
        "app.features.mcp.router.service.kill_tool_run",
        new=AsyncMock(return_value=result),
    ) as mock_kill:
        resp = await regular_client.post(f"/api/v1/tool-runs/{run_id}/kill")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    mock_kill.assert_called_once()
    call_kwargs = mock_kill.call_args.kwargs
    assert call_kwargs["tool_run_id"] == run_id


@pytest.mark.asyncio
async def test_kill_404_for_non_member(
    regular_client: AsyncClient,
) -> None:
    """A non-member (or unknown run) receives 404 — no existence disclosure (§17.1)."""
    run_id = uuid4()

    with patch(
        "app.features.mcp.router.service.kill_tool_run",
        new=AsyncMock(side_effect=EngagementNotFound("Tool run not found")),
    ):
        resp = await regular_client.post(f"/api/v1/tool-runs/{run_id}/kill")

    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_kill_200_idempotent_on_completed_run(
    regular_client: AsyncClient,
) -> None:
    """Killing an already-terminal (completed) run returns 200 with the current state.

    The endpoint is idempotent: no error is raised for already-terminal runs.
    """
    run_id = uuid4()
    result = _make_completed_result()

    with patch(
        "app.features.mcp.router.service.kill_tool_run",
        new=AsyncMock(return_value=result),
    ):
        resp = await regular_client.post(f"/api/v1/tool-runs/{run_id}/kill")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"


@pytest.mark.asyncio
async def test_kill_200_on_awaiting_decision_run(
    regular_client: AsyncClient,
) -> None:
    """Killing a run in awaiting_decision state returns 200.

    The service resolves the rendezvous as 'kill'; the parked task persists
    'killed'.  The endpoint returns the current row (which may still show
    'awaiting_decision' briefly while the task acts on the decision).
    """
    run_id = uuid4()
    result = _make_tool_run_result(status="awaiting_decision")

    with patch(
        "app.features.mcp.router.service.kill_tool_run",
        new=AsyncMock(return_value=result),
    ):
        resp = await regular_client.post(f"/api/v1/tool-runs/{run_id}/kill")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "awaiting_decision"


@pytest.mark.asyncio
async def test_kill_returns_killed_result(
    regular_client: AsyncClient,
) -> None:
    """Killing a queued run returns the killed row after service convergence."""
    run_id = uuid4()
    result = _make_killed_result()

    with patch(
        "app.features.mcp.router.service.kill_tool_run",
        new=AsyncMock(return_value=result),
    ):
        resp = await regular_client.post(f"/api/v1/tool-runs/{run_id}/kill")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "killed"


# ---------------------------------------------------------------------------
# Tests — POST /api/v1/tool-runs/{id}/timeout-decision
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_decision_200_for_member(
    regular_client: AsyncClient,
) -> None:
    """A member receives 200 when submitting a timeout decision for an awaiting run."""
    run_id = uuid4()
    result = _make_tool_run_result(status="awaiting_decision")

    with patch(
        "app.features.mcp.router.service.submit_timeout_decision",
        new=AsyncMock(return_value=result),
    ) as mock_submit:
        resp = await regular_client.post(
            f"/api/v1/tool-runs/{run_id}/timeout-decision",
            json={"decision": "kill"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "awaiting_decision"
    mock_submit.assert_called_once()
    call_kwargs = mock_submit.call_args.kwargs
    assert call_kwargs["tool_run_id"] == run_id
    assert call_kwargs["decision"] == "kill"


@pytest.mark.asyncio
async def test_timeout_decision_extends_with_extend_seconds(
    regular_client: AsyncClient,
) -> None:
    """The router passes extend_seconds from the request body to the service."""
    run_id = uuid4()
    result = _make_tool_run_result(status="awaiting_decision")

    with patch(
        "app.features.mcp.router.service.submit_timeout_decision",
        new=AsyncMock(return_value=result),
    ) as mock_submit:
        resp = await regular_client.post(
            f"/api/v1/tool-runs/{run_id}/timeout-decision",
            json={"decision": "extend", "extend_seconds": 60},
        )

    assert resp.status_code == 200
    call_kwargs = mock_submit.call_args.kwargs
    assert call_kwargs["decision"] == "extend"
    assert call_kwargs["extend_seconds"] == 60


@pytest.mark.asyncio
async def test_timeout_decision_404_for_non_member(
    regular_client: AsyncClient,
) -> None:
    """A non-member (or unknown run) receives 404 — no existence disclosure (§17.1)."""
    run_id = uuid4()

    with patch(
        "app.features.mcp.router.service.submit_timeout_decision",
        new=AsyncMock(side_effect=EngagementNotFound("Tool run not found")),
    ):
        resp = await regular_client.post(
            f"/api/v1/tool-runs/{run_id}/timeout-decision",
            json={"decision": "wait"},
        )

    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_timeout_decision_409_when_not_awaiting(
    regular_client: AsyncClient,
) -> None:
    """409 when the run is not currently awaiting a decision.

    Covers: already resolved, wrong state, or resolved by another concurrent member.
    The service raises TimeoutDecisionConflict; the router translates to 409 inline
    (same pattern as ToolQueueFullError → 429 and McpServerDown → 503).
    """
    run_id = uuid4()

    with patch(
        "app.features.mcp.router.service.submit_timeout_decision",
        new=AsyncMock(
            side_effect=TimeoutDecisionConflict("Run is not currently awaiting a timeout decision")
        ),
    ):
        resp = await regular_client.post(
            f"/api/v1/tool-runs/{run_id}/timeout-decision",
            json={"decision": "kill"},
        )

    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "conflict"


@pytest.mark.asyncio
async def test_timeout_decision_wait_200(
    regular_client: AsyncClient,
) -> None:
    """A 'wait' decision is forwarded correctly and returns 200."""
    run_id = uuid4()
    result = _make_tool_run_result(status="awaiting_decision")

    with patch(
        "app.features.mcp.router.service.submit_timeout_decision",
        new=AsyncMock(return_value=result),
    ) as mock_submit:
        resp = await regular_client.post(
            f"/api/v1/tool-runs/{run_id}/timeout-decision",
            json={"decision": "wait"},
        )

    assert resp.status_code == 200
    call_kwargs = mock_submit.call_args.kwargs
    assert call_kwargs["decision"] == "wait"


# ---------------------------------------------------------------------------
# Tests — POST /api/v1/tool-runs while paused → 409
# ---------------------------------------------------------------------------

_TOOL_RUN_BODY = {
    "engagement_id": str(uuid4()),
    "server_name": "httpx",
    "tool_name": "run_httpx",
    "args": {"target": "http://localhost:3000"},
    "timeout_seconds": 30,
}


@pytest.mark.asyncio
async def test_execute_tool_run_409_when_paused(
    regular_client: AsyncClient,
) -> None:
    """POST /tool-runs while the engagement is paused returns 409.

    EngagementPaused is raised by service.execute_tool_run before any DB row is
    created (Slice 06 Task 4).  The router translates it to 409 inline, matching
    the ToolQueueFullError → 429 and McpServerDown → 503 pattern.
    """
    with patch(
        "app.features.mcp.router.service.execute_tool_run",
        new=AsyncMock(side_effect=EngagementPaused("Engagement is currently paused")),
    ):
        resp = await regular_client.post("/api/v1/tool-runs", json=_TOOL_RUN_BODY)

    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "conflict"
    # Confirm the error message is human-readable (not a stack trace).
    assert "paused" in body["error"]["message"].lower()


@pytest.mark.asyncio
async def test_execute_tool_run_404_for_non_member_on_paused_engagement(
    regular_client: AsyncClient,
) -> None:
    """C-2: A non-member POSTing /tool-runs to a paused engagement gets 404, not 409.

    The membership gate runs BEFORE the pause gate (§17.1 — no existence disclosure).
    If pause were checked first, a non-member would see 409, leaking that the
    engagement exists AND is paused.  The correct behaviour is 404 — same as if the
    engagement did not exist at all.
    """
    with patch(
        "app.features.mcp.router.service.execute_tool_run",
        new=AsyncMock(side_effect=EngagementNotFound("Engagement not found")),
    ):
        resp = await regular_client.post("/api/v1/tool-runs", json=_TOOL_RUN_BODY)

    assert resp.status_code == 404, (
        "Non-member submitting to a paused engagement must receive 404, not 409 "
        "(§17.1 — no existence/state disclosure)"
    )
    body = resp.json()
    assert body["error"]["code"] == "not_found"
