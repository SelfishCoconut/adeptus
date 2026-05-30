"""Router-layer tests for the auth feature.

Uses httpx.AsyncClient with ASGITransport against a fresh FastAPI app that:
- Includes the auth router.
- Registers error handlers.
- Overrides get_db with an in-memory SQLite session factory.

SQLite patches (UUID server default, INET type) mirror conftest.py.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import AsyncGenerator
from typing import cast
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
from app.core.errors import register_error_handlers
from app.features.auth import models  # noqa: F401 — registers ORM metadata
from app.features.auth import repository as repo
from app.features.auth.router import router as auth_router

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_hasher = PasswordHasher()


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(tz=datetime.UTC)


def _uid(user: models.User) -> UUID:
    return cast(UUID, user.id)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def app_and_db(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[FastAPI, async_sessionmaker[AsyncSession]], None]:
    """Spin up a test FastAPI app backed by a fresh SQLite in-memory database."""
    # Settings validation requires these env vars; get_db is overridden so DATABASE_URL
    # value is never actually used to open a connection.
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin")
    monkeypatch.setenv(
        "ADEPTUS_ADMIN_PASSWORD_HASH",
        "$argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )

    from app.core.config import get_settings

    get_settings.cache_clear()

    # Patch Postgres-specific column types for SQLite compatibility (same as conftest.py).
    id_col: Column = models.User.__table__.c.id  # type: ignore[assignment]
    id_col.default = ColumnDefault(uuid4)

    ip_col: Column = models.Session.__table__.c.ip  # type: ignore[assignment]
    ip_col.type = Text()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    app = FastAPI()
    app.include_router(auth_router)
    register_error_handlers(app)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    yield app, factory

    get_settings.cache_clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def admin_user(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> models.User:
    """Insert an admin user directly into the test DB."""
    _, factory = app_and_db
    pw_hash = _hasher.hash("correcthorse")
    async with factory() as session:
        user = await repo.create_user(
            session,
            username="admin",
            password_hash=pw_hash,
            role="admin",
        )
        await session.commit()
        # Re-fetch so the returned object is detached from the session but has all fields.
        await session.refresh(user)
        return user


@pytest_asyncio.fixture
async def authenticated_client(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    admin_user: models.User,
) -> AsyncGenerator[AsyncClient, None]:
    """An AsyncClient that has already logged in and carries the session cookie."""
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "correcthorse"},
        )
        assert resp.status_code == 200, resp.text
        yield client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_sets_cookie(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    admin_user: models.User,
) -> None:
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "correcthorse"},
        )

    assert resp.status_code == 200
    # Cookie is in the Set-Cookie header with the required security attributes.
    cookie_name = get_settings().SESSION_COOKIE_NAME
    set_cookie = resp.headers.get("set-cookie", "")
    lowered = set_cookie.lower()
    assert f"{cookie_name}=" in set_cookie
    assert "httponly" in lowered
    assert "secure" in lowered
    assert "samesite=lax" in lowered
    # Cookie lifetime tracks the session TTL via Max-Age (not an absolute epoch fed to
    # `expires`, which Starlette would treat as seconds-from-now -> decades-long cookie).
    match = re.search(r"max-age=(\d+)", lowered)
    assert match is not None, f"no Max-Age in Set-Cookie: {set_cookie!r}"
    expected = get_settings().SESSION_TTL_DAYS * 86400
    assert abs(int(match.group(1)) - expected) <= 60

    body = resp.json()
    assert "id" in body
    assert body["username"] == "admin"
    assert body["role"] == "admin"


@pytest.mark.asyncio
async def test_login_wrong_password(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    admin_user: models.User,
) -> None:
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "wrongpassword"},
        )

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_logout_clears_cookie_and_deletes_session(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    admin_user: models.User,
) -> None:
    app, factory = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        # Login.
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "correcthorse"},
        )
        assert login_resp.status_code == 200
        cookie_name = get_settings().SESSION_COOKIE_NAME
        assert cookie_name in client.cookies

        # Logout.
        logout_resp = await client.post("/api/v1/auth/logout")
        assert logout_resp.status_code == 204

        # Cookie should be cleared: either removed from jar or have max-age=0.
        set_cookie_after = logout_resp.headers.get("set-cookie", "")
        cookie_cleared = (
            cookie_name not in client.cookies
            or "max-age=0" in set_cookie_after.lower()
            or f'{cookie_name}=""' in set_cookie_after
            or f"{cookie_name}=;" in set_cookie_after
        )
        assert cookie_cleared, (
            f"Cookie not cleared: cookies={dict(client.cookies)!r}, header={set_cookie_after!r}"
        )

        # After logout the cookie is gone from the jar; GET /me should return 401.
        me_resp = await client.get("/api/v1/auth/me")
        assert me_resp.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_user(
    authenticated_client: AsyncClient,
) -> None:
    resp = await authenticated_client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "admin"
    assert body["role"] == "admin"


@pytest.mark.asyncio
async def test_me_401_without_cookie(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_401_expired_session(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    admin_user: models.User,
) -> None:
    app, factory = app_and_db
    # Manually insert an already-expired session.
    expired_at = _utcnow() - datetime.timedelta(hours=1)
    session_id = "expired_session_id_for_test_000000000000000000000000000000"
    async with factory() as session:
        await repo.create_session(
            session,
            session_id=session_id,
            user_id=_uid(admin_user),
            expires_at=expired_at,
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        client.cookies.set(get_settings().SESSION_COOKIE_NAME, session_id)
        resp = await client.get("/api/v1/auth/me")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_401_unknown_session(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """A cookie carrying a session id with no matching row (forged or stale) is rejected."""
    app, _ = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        client.cookies.set(
            get_settings().SESSION_COOKIE_NAME,
            "no_such_session_id_000000000000000000000000000000",
        )
        resp = await client.get("/api/v1/auth/me")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_accept_terms_sets_timestamp(
    app_and_db: tuple[FastAPI, async_sessionmaker[AsyncSession]],
    admin_user: models.User,
) -> None:
    app, factory = app_and_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        # Login.
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "correcthorse"},
        )
        assert login_resp.status_code == 200

        # Accept terms.
        accept_resp = await client.post("/api/v1/auth/accept-terms")
        assert accept_resp.status_code == 200
        body = accept_resp.json()
        assert body["terms_accepted_at"] is not None
