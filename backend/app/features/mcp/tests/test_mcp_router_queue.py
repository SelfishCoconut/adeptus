"""Router + service tests for GET /api/v1/engagements/{engagement_id}/tool-queue.

Uses httpx.AsyncClient with ASGITransport against a test FastAPI app backed by
an in-memory SQLite session.  Engagement and EngagementMember rows are seeded
directly, mirroring the pattern in test_mcp_router_list.py.

The in-process concurrency state (concurrency._states) is populated by driving
the admission manager directly (concurrency.acquire / concurrency.release), with
mocked subprocess interaction — no real subprocesses are spawned.

Membership gate (§17.1/§4) tests:
  GET /tool-queue:  200 for a member (correct counts, slot_limit from DB)
                    404 for a non-member (no body distinguishes "not a member"
                        from "does not exist")
                    empty snapshot shape when nothing is running (counts 0,
                        queued list empty)
                    slot_limit read from the DB engagement row (not in-process default)

Reset:
  concurrency._reset() and service._reset_channels() are called in every fixture
  or test teardown so in-process state does not leak between tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, Column, ColumnDefault, Text
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import Base, get_db
from app.core.errors import register_error_handlers
from app.features.auth import models as auth_models
from app.features.auth import repository as auth_repo
from app.features.auth.router import router as auth_router
from app.features.engagements import models as eng_models
from app.features.engagements import repository as eng_repo
from app.features.mcp import concurrency, service
from app.features.mcp import models as mcp_models
from app.features.mcp.router import router as mcp_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_hasher = PasswordHasher()


# ---------------------------------------------------------------------------
# App + DB fixture  (mirrors test_mcp_router_list.py app_and_db)
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

    # Teardown: reset again to avoid state bleed into subsequent tests.
    concurrency._reset()
    service._reset_channels()

    get_settings.cache_clear()
    await engine.dispose()


# ---------------------------------------------------------------------------
# User fixtures
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
    """Insert a regular user with NO membership in any test engagement."""
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
    """Authenticated AsyncClient logged in as the non-member user."""
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "outsider", "password": "outsiderpass"},
        )
        assert resp.status_code == 200, resp.text
        yield client


# ---------------------------------------------------------------------------
# Engagement seed helper
# ---------------------------------------------------------------------------


async def _seed_engagement(
    factory: async_sessionmaker[AsyncSession],
    owner_id: UUID,
    *,
    concurrency_slot_limit: int = 3,
) -> UUID:
    """Create an engagement owned by owner_id and return its id.

    Writes ``concurrency_slot_limit`` back via an UPDATE so it is visible in
    the DB row (the column default fires at insert time; SQLite may not honour
    server_default so we set it explicitly for tests that care about a specific
    value).
    """
    async with factory() as session:
        engagement = await eng_repo.create_engagement(
            session,
            name="Test Engagement",
            scope="https://example.com",
            client_info=None,
            owner_id=owner_id,
        )
        await session.flush()
        # Write the slot limit explicitly so our tests control it precisely.
        await session.execute(
            sa_update(eng_models.Engagement)
            .where(eng_models.Engagement.id == engagement.id)
            .values(concurrency_slot_limit=concurrency_slot_limit)
        )
        await session.commit()
        await session.refresh(engagement)
        return cast(UUID, engagement.id)


# ---------------------------------------------------------------------------
# Tests — empty snapshot (nothing running)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_queue_empty_snapshot_for_member(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    member_client: AsyncClient,
    member_user: auth_models.User,
) -> None:
    """A member with no running/queued heavy tools receives an empty snapshot.

    Verifies the shape: slot_limit present, running_count=0, queued_count=0,
    queued=[].
    """
    _, factory = app_and_db
    eid = await _seed_engagement(factory, cast(UUID, member_user.id))

    resp = await member_client.get(f"/api/v1/engagements/{eid}/tool-queue")

    assert resp.status_code == 200
    data = resp.json()
    assert data["running_count"] == 0
    assert data["queued_count"] == 0
    assert data["queued"] == []
    assert "slot_limit" in data


# ---------------------------------------------------------------------------
# Tests — slot_limit comes from the DB engagement row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_queue_slot_limit_comes_from_db_row(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    member_client: AsyncClient,
    member_user: auth_models.User,
) -> None:
    """slot_limit in the snapshot matches the persisted engagement setting."""
    _, factory = app_and_db
    eid = await _seed_engagement(factory, cast(UUID, member_user.id), concurrency_slot_limit=5)

    resp = await member_client.get(f"/api/v1/engagements/{eid}/tool-queue")

    assert resp.status_code == 200
    assert resp.json()["slot_limit"] == 5


# ---------------------------------------------------------------------------
# Tests — correct counts when runs are active
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_queue_running_count_when_slot_held(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    member_client: AsyncClient,
    member_user: auth_models.User,
) -> None:
    """running_count reflects the number of in-process admitted (in_use) slots."""
    _, factory = app_and_db
    eid = await _seed_engagement(factory, cast(UUID, member_user.id))

    # Acquire a slot directly to simulate an admitted heavy run.
    run_id = uuid4()

    handle = await concurrency.acquire(
        engagement_id=eid,
        slot_limit=3,
        tool_run_id=run_id,
        target_host="localhost",
        server_name="httpx",
        tool_name="run_httpx",
        on_queued=lambda pos, reason: None,
        on_started=lambda: None,
    )

    try:
        resp = await member_client.get(f"/api/v1/engagements/{eid}/tool-queue")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running_count"] == 1
        assert data["queued_count"] == 0
        assert data["queued"] == []
    finally:
        concurrency.release(handle)


@pytest.mark.asyncio
async def test_tool_queue_queued_count_and_fifo_list(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    member_client: AsyncClient,
    member_user: auth_models.User,
) -> None:
    """queued_count and the queued list are populated when runs are waiting.

    Setup:
      - slot_limit=1 so the second acquire blocks.
      - Two heavy runs against the same host: first is admitted (running_count=1),
        second is queued (queued_count=1, position=1).
    """
    _, factory = app_and_db
    eid = await _seed_engagement(factory, cast(UUID, member_user.id))

    run_id_1 = uuid4()
    run_id_2 = uuid4()

    # Admit the first run (fast-path: slot free).
    handle_1 = await concurrency.acquire(
        engagement_id=eid,
        slot_limit=1,
        tool_run_id=run_id_1,
        target_host="localhost",
        server_name="httpx",
        tool_name="run_httpx",
        on_queued=lambda pos, reason: None,
        on_started=lambda: None,
    )

    # Queue the second run: slot_limit=1 and slot is taken.
    queued_positions: list[int] = []
    admitted_event = asyncio.Event()

    async def _on_queued(pos: int, _reason: str) -> None:
        queued_positions.append(pos)

    async def _on_started() -> None:
        admitted_event.set()

    # acquire() will block inside the event loop until handle_1 is released.
    # We schedule it as a background task so control returns to this coroutine.
    acquire_task = asyncio.create_task(
        concurrency.acquire(
            engagement_id=eid,
            slot_limit=1,
            tool_run_id=run_id_2,
            target_host="localhost",
            server_name="httpx",
            tool_name="run_httpx",
            on_queued=_on_queued,
            on_started=_on_started,
        )
    )

    # Yield control so the background task enqueues and calls _on_queued.
    await asyncio.sleep(0)
    # Allow any pending callbacks to run.
    await asyncio.sleep(0)

    try:
        resp = await member_client.get(f"/api/v1/engagements/{eid}/tool-queue")
        assert resp.status_code == 200
        data = resp.json()
        assert data["running_count"] == 1
        assert data["queued_count"] == 1
        assert len(data["queued"]) == 1

        queued_item = data["queued"][0]
        assert queued_item["position"] == 1
        assert queued_item["tool_run_id"] == str(run_id_2)
        assert queued_item["server_name"] == "httpx"
        assert queued_item["tool_name"] == "run_httpx"
        assert queued_item["target_host"] == "localhost"
        assert queued_item["reason"] in ("slot_full", "target_locked")
        assert "enqueued_at" in queued_item
    finally:
        # Release the first handle so the second task can be admitted and finish.
        concurrency.release(handle_1)
        handle_2 = await acquire_task
        concurrency.release(handle_2)


# ---------------------------------------------------------------------------
# Tests — 404 for non-member (no existence disclosure)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_queue_404_for_non_member(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    non_member_client: AsyncClient,
    member_user: auth_models.User,
) -> None:
    """A non-member receives 404.  The error body must not disclose existence.

    Both "engagement exists but caller is not a member" and "engagement does not
    exist" must return exactly the same 404 error shape (§17.1).
    """
    _, factory = app_and_db
    # Engagement exists but the non_member_user is not a member.
    eid = await _seed_engagement(factory, cast(UUID, member_user.id))

    resp = await non_member_client.get(f"/api/v1/engagements/{eid}/tool-queue")

    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_tool_queue_404_for_missing_engagement(
    non_member_client: AsyncClient,
) -> None:
    """A request for a non-existent engagement also returns 404 (no disclosure)."""
    resp = await non_member_client.get(f"/api/v1/engagements/{uuid4()}/tool-queue")

    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_tool_queue_non_member_and_missing_return_identical_shape(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    non_member_client: AsyncClient,
    member_user: auth_models.User,
) -> None:
    """The 404 body for a non-member is indistinguishable from a missing engagement.

    Asserts §17.1: no path leaks whether the engagement exists.
    """
    _, factory = app_and_db
    existing_eid = await _seed_engagement(factory, cast(UUID, member_user.id))
    missing_eid = uuid4()

    resp_nonmember = await non_member_client.get(f"/api/v1/engagements/{existing_eid}/tool-queue")
    resp_missing = await non_member_client.get(f"/api/v1/engagements/{missing_eid}/tool-queue")

    assert resp_nonmember.status_code == 404
    assert resp_missing.status_code == 404
    # Both must have the same error code (exact message may differ in service
    # layer but the HTTP shape is identical).
    assert resp_nonmember.json()["error"]["code"] == resp_missing.json()["error"]["code"]
