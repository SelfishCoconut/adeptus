"""Router-layer tests for GET /api/v1/tool-runs and GET /api/v1/tool-runs/{id}.

Uses httpx.AsyncClient with ASGITransport against a test FastAPI app backed by
an in-memory SQLite session.  Engagement and EngagementMember rows are seeded
directly to test the real membership chokepoint (§17.1) without mocking the
service layer — this lets us verify the full stack from HTTP to DB.

Status codes tested:
  GET /tool-runs:        200 (member), 404 (non-member), 404 (missing engagement),
                         400 (malformed cursor), pagination (next_cursor + second page)
  GET /tool-runs/{id}:   200 (member), 404 (non-member-owned run), 404 (missing run)
"""

from __future__ import annotations

import datetime
from collections.abc import AsyncGenerator
from typing import cast
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
from app.features.engagements import models as eng_models
from app.features.engagements import repository as eng_repo
from app.features.mcp import models as mcp_models
from app.features.mcp import repository as mcp_repo
from app.features.mcp.models import ToolRun
from app.features.mcp.router import router as mcp_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_hasher = PasswordHasher()


def _now() -> datetime.datetime:
    return datetime.datetime(2026, 6, 1, 12, 0, 0, tzinfo=datetime.UTC)


# ---------------------------------------------------------------------------
# App + DB fixture  (mirrors test_mcp_router.py app_and_db)
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

    yield app, factory

    get_settings.cache_clear()
    await engine.dispose()


# ---------------------------------------------------------------------------
# User + session fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def member_user(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> auth_models.User:
    """Insert a regular user who will be a member of the test engagement."""
    _, factory = app_and_db
    pw_hash = _hasher.hash("memberpass")
    async with factory() as session:
        user = await auth_repo.create_user(
            session, username="member", password_hash=pw_hash, role="user"
        )
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def non_member_user(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> auth_models.User:
    """Insert a regular user who has NO membership in the test engagement."""
    _, factory = app_and_db
    pw_hash = _hasher.hash("outsiderpass")
    async with factory() as session:
        user = await auth_repo.create_user(
            session, username="outsider", password_hash=pw_hash, role="user"
        )
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def member_client(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    member_user: auth_models.User,
) -> AsyncGenerator[AsyncClient, None]:
    """Authenticated AsyncClient logged in as the member user."""
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "member", "password": "memberpass"},
        )
        assert resp.status_code == 200, resp.text
        yield client


@pytest_asyncio.fixture
async def non_member_client(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    non_member_user: auth_models.User,
) -> AsyncGenerator[AsyncClient, None]:
    """Authenticated AsyncClient logged in as a user with no engagement membership."""
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "outsider", "password": "outsiderpass"},
        )
        assert resp.status_code == 200, resp.text
        yield client


# ---------------------------------------------------------------------------
# Engagement + ToolRun seed helpers
# ---------------------------------------------------------------------------


async def _seed_engagement(
    factory: async_sessionmaker[AsyncSession],
    owner_id: UUID,
) -> UUID:
    """Create an engagement owned by owner_id and return its id."""
    async with factory() as session:
        engagement = await eng_repo.create_engagement(
            session,
            name="Test Engagement",
            scope="https://example.com",
            client_info=None,
            owner_id=owner_id,
        )
        await session.commit()
        await session.refresh(engagement)
        return cast(UUID, engagement.id)


async def _seed_tool_run(
    factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
    *,
    started_at: datetime.datetime | None = None,
) -> ToolRun:
    """Insert a completed ToolRun row and return it.

    When ``started_at`` is provided it is written back into the row via a
    direct UPDATE so the keyset-pagination ordering is deterministic in tests
    (SQLite's func.now() has second-level precision, so all rows in a fast
    test run would otherwise share the same timestamp).
    """
    from sqlalchemy import update as sa_update

    async with factory() as session:
        run = await mcp_repo.create_tool_run(
            session,
            engagement_id=engagement_id,
            server_name="shell-exec",
            tool_name="run_command",
            args={"command": "echo hello"},
        )
        finished = started_at or _now()
        run = await mcp_repo.update_tool_run_result(
            session,
            cast(UUID, run.id),
            exit_code=0,
            stdout="hello\n",
            stderr="",
            finished_at=finished,
        )
        if started_at is not None:
            await session.execute(
                sa_update(ToolRun).where(ToolRun.id == run.id).values(started_at=started_at)
            )
            await session.flush()
            await session.refresh(run)
        await session.commit()
        await session.refresh(run)
        return run


# ---------------------------------------------------------------------------
# Tests — GET /api/v1/tool-runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tool_runs_returns_200_for_member(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    member_client: AsyncClient,
    member_user: auth_models.User,
) -> None:
    """A member receives 200 with a ToolRunPage containing their runs."""
    _, factory = app_and_db
    eid = await _seed_engagement(factory, cast(UUID, member_user.id))
    await _seed_tool_run(factory, eid)

    resp = await member_client.get("/api/v1/tool-runs", params={"engagement_id": str(eid)})

    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "next_cursor" in data
    assert len(data["items"]) == 1
    assert data["items"][0]["server_name"] == "shell-exec"
    assert data["next_cursor"] is None


@pytest.mark.asyncio
async def test_list_tool_runs_returns_404_for_non_member(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    non_member_client: AsyncClient,
    member_user: auth_models.User,
) -> None:
    """A non-member receives 404 — existence is hidden per §17.1 (not a 403)."""
    _, factory = app_and_db
    eid = await _seed_engagement(factory, cast(UUID, member_user.id))

    resp = await non_member_client.get("/api/v1/tool-runs", params={"engagement_id": str(eid)})

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_list_tool_runs_returns_404_for_missing_engagement(
    non_member_client: AsyncClient,
) -> None:
    """A request for a non-existent engagement also returns 404 (no disclosure)."""
    resp = await non_member_client.get("/api/v1/tool-runs", params={"engagement_id": str(uuid4())})

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_list_tool_runs_returns_400_for_malformed_cursor(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    member_client: AsyncClient,
    member_user: auth_models.User,
) -> None:
    """A malformed (non-empty) cursor string returns 400 Bad Request."""
    _, factory = app_and_db
    eid = await _seed_engagement(factory, cast(UUID, member_user.id))

    resp = await member_client.get(
        "/api/v1/tool-runs",
        params={"engagement_id": str(eid), "cursor": "not-valid-base64!!"},
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


@pytest.mark.asyncio
async def test_list_tool_runs_pagination_first_page_has_next_cursor(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    member_client: AsyncClient,
    member_user: auth_models.User,
) -> None:
    """When more rows exist than limit, next_cursor is populated."""
    _, factory = app_and_db
    eid = await _seed_engagement(factory, cast(UUID, member_user.id))

    # Seed 3 tool runs.
    for i in range(3):
        await _seed_tool_run(
            factory,
            eid,
            started_at=datetime.datetime(2026, 1, 1, 12, i, 0, tzinfo=datetime.UTC),
        )

    resp = await member_client.get(
        "/api/v1/tool-runs",
        params={"engagement_id": str(eid), "limit": 2},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["next_cursor"] is not None


@pytest.mark.asyncio
async def test_list_tool_runs_pagination_second_page_no_overlap(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    member_client: AsyncClient,
    member_user: auth_models.User,
) -> None:
    """Second page (cursor applied) returns the rest with no overlap."""
    _, factory = app_and_db
    eid = await _seed_engagement(factory, cast(UUID, member_user.id))

    for i in range(4):
        await _seed_tool_run(
            factory,
            eid,
            started_at=datetime.datetime(2026, 1, 1, 12, i, 0, tzinfo=datetime.UTC),
        )

    resp1 = await member_client.get(
        "/api/v1/tool-runs",
        params={"engagement_id": str(eid), "limit": 2},
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert len(data1["items"]) == 2
    cursor = data1["next_cursor"]
    assert cursor is not None

    resp2 = await member_client.get(
        "/api/v1/tool-runs",
        params={"engagement_id": str(eid), "limit": 2, "cursor": cursor},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["items"]) == 2
    assert data2["next_cursor"] is None

    # No overlap between pages.
    page1_ids = {item["tool_run_id"] for item in data1["items"]}
    page2_ids = {item["tool_run_id"] for item in data2["items"]}
    assert page1_ids.isdisjoint(page2_ids)
    # Together they cover all 4 rows.
    assert len(page1_ids | page2_ids) == 4


# ---------------------------------------------------------------------------
# Tests — GET /api/v1/tool-runs/{tool_run_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tool_run_returns_200_for_member(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    member_client: AsyncClient,
    member_user: auth_models.User,
) -> None:
    """A member can fetch their own tool run and receives 200."""
    _, factory = app_and_db
    eid = await _seed_engagement(factory, cast(UUID, member_user.id))
    run = await _seed_tool_run(factory, eid)

    resp = await member_client.get(f"/api/v1/tool-runs/{run.id}")

    assert resp.status_code == 200
    data = resp.json()
    assert data["tool_run_id"] == str(run.id)
    assert data["server_name"] == "shell-exec"
    assert data["exit_code"] == 0


@pytest.mark.asyncio
async def test_get_tool_run_returns_404_for_non_member(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    non_member_client: AsyncClient,
    member_user: auth_models.User,
) -> None:
    """A user who is not a member of the run's engagement receives 404 (§17.1)."""
    _, factory = app_and_db
    eid = await _seed_engagement(factory, cast(UUID, member_user.id))
    run = await _seed_tool_run(factory, eid)

    resp = await non_member_client.get(f"/api/v1/tool-runs/{run.id}")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_get_tool_run_returns_404_for_missing_run(
    member_client: AsyncClient,
) -> None:
    """A request for a non-existent tool run returns 404 (no existence disclosure)."""
    resp = await member_client.get(f"/api/v1/tool-runs/{uuid4()}")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
