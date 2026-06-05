"""Router-layer tests for the audit feature (Slice 10 task 6).

Uses httpx.AsyncClient against a test app with the auth + audit routers. Real auth
(so the 401 path is genuine); the audit service is mocked so these focus on HTTP
concerns — status translation (404/403), query-param parsing (self_approved, action,
cursor), and pagination passthrough. The real membership/admin logic is covered by
test_service.py.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Column, ColumnDefault, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import Base, get_db
from app.core.errors import ForbiddenError, NotFoundError, register_error_handlers
from app.features.audit.hashing import GENESIS_HASH
from app.features.audit.router import router as audit_router
from app.features.audit.schemas import AuditAction, AuditEntryRead, AuditPage
from app.features.auth import models as auth_models
from app.features.auth import repository as auth_repo
from app.features.auth.router import router as auth_router
from app.features.engagements import models as eng_models  # noqa: F401 — registers tables

_hasher = PasswordHasher()


def _sample_page(next_cursor: str | None = None) -> AuditPage:
    entry = AuditEntryRead(
        id=uuid4(),
        seq=1,
        action=AuditAction.LOGIN,
        actor_user_id=uuid4(),
        engagement_id=None,
        target_type=None,
        target_id=None,
        self_approved=None,
        payload={},
        created_at=datetime(2026, 6, 5, tzinfo=UTC),
        prev_hash=GENESIS_HASH,
        entry_hash="a" * 64,
    )
    return AuditPage(items=[entry], next_cursor=next_cursor)


@pytest_asyncio.fixture
async def app_and_db(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[FastAPI, async_sessionmaker[AsyncSession]], None]:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin")
    monkeypatch.setenv(
        "ADEPTUS_ADMIN_PASSWORD_HASH",
        "$argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    get_settings.cache_clear()

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
    app.include_router(audit_router)
    register_error_handlers(app)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    yield app, factory
    get_settings.cache_clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def client(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> AsyncGenerator[AsyncClient, None]:
    """Authenticated client (logged-in regular user)."""
    app, factory = app_and_db
    async with factory() as session:
        await auth_repo.create_user(
            session, username="alice", password_hash=_hasher.hash("secretpass"), role="user"
        )
        await session.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as c:
        resp = await c.post(
            "/api/v1/auth/login", json={"username": "alice", "password": "secretpass"}
        )
        assert resp.status_code == 200, resp.text
        yield c


async def test_list_audit_200_for_member(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    mock = AsyncMock(return_value=_sample_page())
    monkeypatch.setattr("app.features.audit.router.service.list_engagement_audit", mock)
    resp = await client.get(f"/api/v1/audit?engagement_id={uuid4()}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["action"] == "login"


async def test_list_audit_404_for_non_member(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    mock = AsyncMock(side_effect=NotFoundError("Engagement not found"))
    monkeypatch.setattr("app.features.audit.router.service.list_engagement_audit", mock)
    resp = await client.get(f"/api/v1/audit?engagement_id={uuid4()}")
    assert resp.status_code == 404


async def test_list_audit_self_approved_query_filter(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    mock = AsyncMock(return_value=_sample_page())
    monkeypatch.setattr("app.features.audit.router.service.list_engagement_audit", mock)
    eng = uuid4()
    resp = await client.get(
        f"/api/v1/audit?engagement_id={eng}&self_approved=true&action=approval_granted"
    )
    assert resp.status_code == 200
    kwargs = mock.call_args.kwargs
    assert kwargs["self_approved"] is True
    assert kwargs["action"] is not None and kwargs["action"].value == "approval_granted"


async def test_audit_pagination_cursor(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    mock = AsyncMock(return_value=_sample_page(next_cursor="NjQ="))
    monkeypatch.setattr("app.features.audit.router.service.list_engagement_audit", mock)
    resp = await client.get(f"/api/v1/audit?engagement_id={uuid4()}&cursor=Mw%3D%3D&limit=10")
    assert resp.status_code == 200
    assert resp.json()["next_cursor"] == "NjQ="
    kwargs = mock.call_args.kwargs
    assert kwargs["cursor"] == "Mw=="
    assert kwargs["limit"] == 10


async def test_global_audit_200_for_admin(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    mock = AsyncMock(return_value=_sample_page())
    monkeypatch.setattr("app.features.audit.router.service.list_global_audit", mock)
    resp = await client.get("/api/v1/audit/global")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1


async def test_global_audit_403_for_non_admin(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    mock = AsyncMock(side_effect=ForbiddenError("Admin privileges required"))
    monkeypatch.setattr("app.features.audit.router.service.list_global_audit", mock)
    resp = await client.get("/api/v1/audit/global")
    assert resp.status_code == 403


async def test_audit_unauthenticated_401(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as c:
        resp = await c.get(f"/api/v1/audit?engagement_id={uuid4()}")
        assert resp.status_code == 401
        resp2 = await c.get("/api/v1/audit/global")
        assert resp2.status_code == 401
