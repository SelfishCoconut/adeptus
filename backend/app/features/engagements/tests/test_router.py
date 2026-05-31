"""Router-layer tests for the engagements feature.

Uses httpx.AsyncClient with ASGITransport against a test FastAPI app that:
- Includes the engagements router.
- Includes the auth router (needed to create real sessions for authentication).
- Registers error handlers.
- Overrides get_db with an in-memory SQLite session factory.

The engagements service is mocked with unittest.mock.patch so no real DB state
for engagements is needed — only a real user/session row is needed to satisfy
the get_current_user dependency.

Status codes tested: 201, 200, 401, 403, 404, 409, 204, 400.
"""

from __future__ import annotations

import datetime
from collections.abc import AsyncGenerator
from typing import cast
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Column, ColumnDefault, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import Base, get_db
from app.core.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    register_error_handlers,
)
from app.features.auth import models as auth_models
from app.features.auth import repository as auth_repo
from app.features.auth.router import router as auth_router
from app.features.engagements import models as eng_models  # noqa: F401 — registers tables
from app.features.engagements.router import router as eng_router
from app.features.engagements.schemas import (
    EngagementDetail,
    EngagementSummary,
    MemberEntry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_hasher = PasswordHasher()


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.UTC)


def _now() -> datetime.datetime:
    return datetime.datetime(2026, 5, 30, 12, 0, 0, tzinfo=datetime.UTC)


def _make_engagement_detail(*, engagement_id: UUID | None = None) -> EngagementDetail:
    return EngagementDetail(
        id=engagement_id or uuid4(),
        name="ACME Web Assessment",
        status="active",
        scope="192.168.1.0/24",
        client_info="ACME Corp",
        created_at=_now(),
        updated_at=_now(),
        member_role="owner",
        privacy_mode="local_only",
    )


def _make_engagement_summary(*, engagement_id: UUID | None = None) -> EngagementSummary:
    return EngagementSummary(
        id=engagement_id or uuid4(),
        name="ACME Web Assessment",
        status="active",
        created_at=_now(),
        member_role="owner",
        privacy_mode="local_only",
    )


def _make_member_entry(*, user_id: UUID | None = None) -> MemberEntry:
    return MemberEntry(
        user_id=user_id or uuid4(),
        username="alice",
        role="member",
        joined_at=_now(),
    )


# ---------------------------------------------------------------------------
# Fixtures
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

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(eng_router)
    register_error_handlers(app)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    yield app, factory

    get_settings.cache_clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def owner_user(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> auth_models.User:
    """Insert an owner user directly into the test DB."""
    _, factory = app_and_db
    pw_hash = _hasher.hash("secretpass")
    async with factory() as session:
        user = await auth_repo.create_user(
            session,
            username="owner",
            password_hash=pw_hash,
            role="user",
        )
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def member_user(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> auth_models.User:
    """Insert a non-owner member user."""
    _, factory = app_and_db
    pw_hash = _hasher.hash("secretpass")
    async with factory() as session:
        user = await auth_repo.create_user(
            session,
            username="member",
            password_hash=pw_hash,
            role="user",
        )
        await session.commit()
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def owner_client(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    owner_user: auth_models.User,
) -> AsyncGenerator[AsyncClient, None]:
    """Authenticated AsyncClient logged in as the owner user."""
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": "secretpass"},
        )
        assert resp.status_code == 200, resp.text
        yield client


@pytest_asyncio.fixture
async def member_client(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    member_user: auth_models.User,
) -> AsyncGenerator[AsyncClient, None]:
    """Authenticated AsyncClient logged in as the member (non-owner) user."""
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "member", "password": "secretpass"},
        )
        assert resp.status_code == 200, resp.text
        yield client


# ---------------------------------------------------------------------------
# Tests — list_engagements (GET /api/v1/engagements)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_engagements_returns_200(
    owner_client: AsyncClient,
    owner_user: auth_models.User,
) -> None:
    summaries = [_make_engagement_summary()]
    with patch(
        "app.features.engagements.router.service.list_engagements",
        new=AsyncMock(return_value=summaries),
    ):
        resp = await owner_client.get("/api/v1/engagements")

    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["name"] == "ACME Web Assessment"
    assert body[0]["member_role"] == "owner"


@pytest.mark.asyncio
async def test_list_engagements_returns_empty_list(
    owner_client: AsyncClient,
) -> None:
    with patch(
        "app.features.engagements.router.service.list_engagements",
        new=AsyncMock(return_value=[]),
    ):
        resp = await owner_client.get("/api/v1/engagements")

    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_engagements_401_unauthenticated(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.get("/api/v1/engagements")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests — create_engagement (POST /api/v1/engagements → 201)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_engagement_returns_201(
    owner_client: AsyncClient,
) -> None:
    detail = _make_engagement_detail()
    with patch(
        "app.features.engagements.router.service.create_engagement",
        new=AsyncMock(return_value=detail),
    ):
        resp = await owner_client.post(
            "/api/v1/engagements",
            json={"name": "ACME Web Assessment", "scope": "192.168.1.0/24"},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "ACME Web Assessment"
    assert body["status"] == "active"
    assert body["member_role"] == "owner"


@pytest.mark.asyncio
async def test_create_engagement_401_unauthenticated(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.post(
            "/api/v1/engagements",
            json={"name": "ACME", "scope": "example.com"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_engagement_any_authenticated_user_may_create(
    member_client: AsyncClient,
) -> None:
    """Non-admin, non-owner users can create engagements — no 403."""
    detail = _make_engagement_detail()
    with patch(
        "app.features.engagements.router.service.create_engagement",
        new=AsyncMock(return_value=detail),
    ):
        resp = await member_client.post(
            "/api/v1/engagements",
            json={"name": "New Engagement", "scope": "10.0.0.0/8"},
        )

    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Tests — get_engagement (GET /api/v1/engagements/{id})
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_engagement_returns_200(
    owner_client: AsyncClient,
) -> None:
    eid = uuid4()
    detail = _make_engagement_detail(engagement_id=eid)
    with patch(
        "app.features.engagements.router.service.get_engagement",
        new=AsyncMock(return_value=detail),
    ):
        resp = await owner_client.get(f"/api/v1/engagements/{eid}")

    assert resp.status_code == 200
    assert resp.json()["id"] == str(eid)


@pytest.mark.asyncio
async def test_get_engagement_404_non_member(
    owner_client: AsyncClient,
) -> None:
    eid = uuid4()
    with patch(
        "app.features.engagements.router.service.get_engagement",
        new=AsyncMock(side_effect=NotFoundError("Engagement not found")),
    ):
        resp = await owner_client.get(f"/api/v1/engagements/{eid}")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_engagement_401_unauthenticated(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.get(f"/api/v1/engagements/{uuid4()}")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests — list_members (GET /api/v1/engagements/{id}/members)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_members_returns_200(
    owner_client: AsyncClient,
) -> None:
    eid = uuid4()
    entries = [_make_member_entry()]
    with patch(
        "app.features.engagements.router.service.list_members",
        new=AsyncMock(return_value=entries),
    ):
        resp = await owner_client.get(f"/api/v1/engagements/{eid}/members")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["username"] == "alice"
    assert body[0]["role"] == "member"


@pytest.mark.asyncio
async def test_list_members_404_non_member(
    owner_client: AsyncClient,
) -> None:
    eid = uuid4()
    with patch(
        "app.features.engagements.router.service.list_members",
        new=AsyncMock(side_effect=NotFoundError("Engagement not found")),
    ):
        resp = await owner_client.get(f"/api/v1/engagements/{eid}/members")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_members_401_unauthenticated(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.get(f"/api/v1/engagements/{uuid4()}/members")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests — add_member (POST /api/v1/engagements/{id}/members → 201)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_member_owner_returns_201(
    owner_client: AsyncClient,
) -> None:
    eid = uuid4()
    entry = _make_member_entry()
    with patch(
        "app.features.engagements.router.service.add_member",
        new=AsyncMock(return_value=entry),
    ):
        resp = await owner_client.post(
            f"/api/v1/engagements/{eid}/members",
            json={"username": "alice"},
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "alice"
    assert body["role"] == "member"


@pytest.mark.asyncio
async def test_add_member_403_non_owner(
    owner_client: AsyncClient,
) -> None:
    eid = uuid4()
    with patch(
        "app.features.engagements.router.service.add_member",
        new=AsyncMock(side_effect=ForbiddenError("Only the engagement owner may add members")),
    ):
        resp = await owner_client.post(
            f"/api/v1/engagements/{eid}/members",
            json={"username": "alice"},
        )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_add_member_404_unknown_username(
    owner_client: AsyncClient,
) -> None:
    eid = uuid4()
    with patch(
        "app.features.engagements.router.service.add_member",
        new=AsyncMock(side_effect=NotFoundError("User not found")),
    ):
        resp = await owner_client.post(
            f"/api/v1/engagements/{eid}/members",
            json={"username": "nobody"},
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_member_409_duplicate(
    owner_client: AsyncClient,
) -> None:
    eid = uuid4()
    with patch(
        "app.features.engagements.router.service.add_member",
        new=AsyncMock(side_effect=ConflictError("User is already a member of this engagement")),
    ):
        resp = await owner_client.post(
            f"/api/v1/engagements/{eid}/members",
            json={"username": "alice"},
        )

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_add_member_401_unauthenticated(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.post(
            f"/api/v1/engagements/{uuid4()}/members",
            json={"username": "alice"},
        )
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tests — remove_member (DELETE /api/v1/engagements/{id}/members/{user_id} → 204)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remove_member_owner_returns_204(
    owner_client: AsyncClient,
) -> None:
    eid = uuid4()
    uid = uuid4()
    with patch(
        "app.features.engagements.router.service.remove_member",
        new=AsyncMock(return_value=None),
    ):
        resp = await owner_client.delete(f"/api/v1/engagements/{eid}/members/{uid}")

    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_remove_member_403_non_owner(
    owner_client: AsyncClient,
) -> None:
    eid = uuid4()
    uid = uuid4()
    with patch(
        "app.features.engagements.router.service.remove_member",
        new=AsyncMock(side_effect=ForbiddenError("Only the engagement owner may remove members")),
    ):
        resp = await owner_client.delete(f"/api/v1/engagements/{eid}/members/{uid}")

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_remove_member_400_owner_self_removal(
    owner_client: AsyncClient,
    owner_user: auth_models.User,
) -> None:
    eid = uuid4()
    with patch(
        "app.features.engagements.router.service.remove_member",
        new=AsyncMock(side_effect=BadRequestError("The engagement owner cannot remove themselves")),
    ):
        resp = await owner_client.delete(
            f"/api/v1/engagements/{eid}/members/{cast(UUID, owner_user.id)}"
        )

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_remove_member_404_not_found(
    owner_client: AsyncClient,
) -> None:
    eid = uuid4()
    uid = uuid4()
    with patch(
        "app.features.engagements.router.service.remove_member",
        new=AsyncMock(side_effect=NotFoundError("Member not found")),
    ):
        resp = await owner_client.delete(f"/api/v1/engagements/{eid}/members/{uid}")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_remove_member_401_unauthenticated(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.delete(f"/api/v1/engagements/{uuid4()}/members/{uuid4()}")
    assert resp.status_code == 401
