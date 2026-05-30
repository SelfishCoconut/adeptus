"""Integration tests for the auth feature against a real PostgreSQL instance.

These exercise paths the SQLite unit suite cannot: the ``INSERT ... ON CONFLICT
DO NOTHING`` admin bootstrap (a Postgres-dialect statement) and a full
login -> me -> logout -> me cycle over a real async engine.

Marked ``integration`` (deselected by the default ``make test-backend`` run) and
run by ``make test-integration``. Each test is isolated inside a throwaway schema
so it never touches the dev/prod ``users``/``sessions`` tables, and the whole
module skips cleanly when no Postgres is reachable.

Point them at a database with ``ADEPTUS_TEST_DATABASE_URL``; it defaults to the
compose Postgres on localhost. (``DATABASE_URL`` is deliberately not consulted:
the unit-test conftest sets it to a non-routable placeholder via ``setdefault``.)
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import Base, get_db
from app.core.errors import register_error_handlers
from app.features.auth import (
    models,  # noqa: F401 — registers ORM metadata
    service,
)
from app.features.auth import repository as repo
from app.features.auth.router import router as auth_router

pytestmark = pytest.mark.integration

_DEFAULT_DSN = "postgresql+asyncpg://adeptus:adeptus@localhost:5432/adeptus"
_ADMIN_PW = "correcthorse"
_ADMIN_HASH = PasswordHasher().hash(_ADMIN_PW)


def _dsn() -> str:
    return os.environ.get("ADEPTUS_TEST_DATABASE_URL") or _DEFAULT_DSN


@pytest_asyncio.fixture
async def pg_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """A session factory bound to a freshly-created, disposable Postgres schema.

    Skips the test if Postgres is unreachable so the suite stays green on machines
    without the compose stack up.
    """
    schema = f"auth_it_{uuid.uuid4().hex[:12]}"
    admin_engine = create_async_engine(_dsn(), isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception as exc:  # noqa: BLE001 — any connect/setup failure means "no PG here"
        await admin_engine.dispose()
        pytest.skip(f"Postgres not available for integration tests: {exc}")

    # Scope this engine's connections to the throwaway schema so the ORM's unqualified
    # tables are created and queried there, never in public.
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


def _make_app(factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    app = FastAPI()
    app.include_router(auth_router)
    register_error_handlers(app)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    return app


@pytest.mark.asyncio
async def test_login_logout_full_cycle(
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    """POST login -> GET me -> POST logout -> GET me 401, over a real Postgres engine."""
    async with pg_factory() as session:
        await repo.create_user(session, username="admin", password_hash=_ADMIN_HASH, role="admin")
        await session.commit()

    app = _make_app(pg_factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        login = await client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": _ADMIN_PW}
        )
        assert login.status_code == 200, login.text
        assert get_settings().SESSION_COOKIE_NAME in client.cookies

        me = await client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["username"] == "admin"

        logout = await client.post("/api/v1/auth/logout")
        assert logout.status_code == 204

        me_after = await client.get("/api/v1/auth/me")
        assert me_after.status_code == 401


@pytest.mark.asyncio
async def test_bootstrap_admin_idempotent(
    pg_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running bootstrap twice creates exactly one admin — exercises the real
    INSERT ... ON CONFLICT DO NOTHING path that SQLite cannot."""
    monkeypatch.setenv("DATABASE_URL", _dsn())
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "rootadmin")
    monkeypatch.setenv("ADEPTUS_ADMIN_PASSWORD_HASH", _ADMIN_HASH)
    get_settings.cache_clear()
    try:
        async with pg_factory() as session:
            first = await service.bootstrap_admin(session)
            await session.commit()
        async with pg_factory() as session:
            second = await service.bootstrap_admin(session)
            await session.commit()

        assert first is not None  # created on the first run
        assert second is None  # no-op on the second run (conflict)

        async with pg_factory() as session:
            admin = await repo.get_user_by_username(session, "rootadmin")
            assert admin is not None
            assert admin.role == "admin"
    finally:
        get_settings.cache_clear()
