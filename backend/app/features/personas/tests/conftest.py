"""Feature-local fixtures for the personas tests.

In-memory SQLite async engine (same pattern as the chat/audit tests). Postgres-only
column defaults/types are patched for SQLite before ``create_all``; only the tables this
feature touches are created so the shared ``Base.metadata`` never trips on another
feature's Postgres-only DDL.

``db_session`` backs the repository/service/bootstrap unit tests; ``app_and_factory``
(auth + personas routers, real session-cookie auth) backs the HTTP router tests.
"""

from collections.abc import AsyncGenerator, Iterator
from typing import cast
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import Column, ColumnDefault, Connection, Table, Text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.db import Base, get_db
from app.core.errors import register_error_handlers
from app.features.audit import models as audit_models
from app.features.auth import models as auth_models
from app.features.auth.router import router as auth_router
from app.features.personas import models as persona_models


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Provide the minimal settings env so get_settings() can instantiate."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin")
    monkeypatch.setenv("ADEPTUS_ADMIN_PASSWORD_HASH", "x")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _patch_sqlite_columns() -> None:
    """Swap Postgres-only column defaults/types for SQLite-compatible ones."""
    for model in (auth_models.User, persona_models.Persona, audit_models.AuditEntry):
        id_col: Column = model.__table__.c.id  # type: ignore[assignment]
        id_col.default = ColumnDefault(uuid4)

    ip_col: Column = auth_models.Session.__table__.c.ip  # type: ignore[assignment]
    ip_col.type = Text()


_TABLES: list[Table] = [
    cast(Table, model.__table__)
    for model in (
        auth_models.User,
        auth_models.Session,
        persona_models.Persona,
        audit_models.AuditEntry,
        audit_models.AuditChainHead,
    )
]


def _create_tables(sync_conn: Connection) -> None:
    Base.metadata.create_all(sync_conn, tables=_TABLES)


async def _make_engine() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    _patch_sqlite_columns()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_create_tables)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, factory


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async SQLite in-memory session for personas unit tests."""
    engine, factory = await _make_engine()
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def db_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Async SQLite factory (for seeding across multiple independent sessions)."""
    engine, factory = await _make_engine()
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def app_and_factory() -> AsyncGenerator[
    tuple[FastAPI, async_sessionmaker[AsyncSession]], None
]:
    """A FastAPI app (auth + personas routers) backed by a fresh SQLite engine."""
    engine, factory = await _make_engine()

    # Imported lazily so the repository/service unit tests collect before the router exists.
    from app.features.personas.router import router as personas_router

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(personas_router)
    register_error_handlers(app)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    yield app, factory
    await engine.dispose()
