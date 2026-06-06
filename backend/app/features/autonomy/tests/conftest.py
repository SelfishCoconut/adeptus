"""Feature-local fixtures for the autonomy feature tests.

``db_session`` — in-memory SQLite session with only the ``autonomy_grants`` table, for
repository/service unit tests (mirrors approvals/audit conftest). ``client`` — a FastAPI
app (autonomy router) on SQLite with a seeded member user + engagement, overriding
``get_current_user``/``get_db`` so router tests exercise the real service + real audit
(against SQLite) without the full login flow.

Postgres-specific bit patched for SQLite: ``*.id`` server_default ``gen_random_uuid()`` →
Python-side uuid4.
"""

from collections.abc import AsyncGenerator
from typing import cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Column, ColumnDefault, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import Base, get_db
from app.core.errors import register_error_handlers
from app.features.audit import models as audit_models
from app.features.audit.hashing import GENESIS_HASH
from app.features.auth import models as auth_models
from app.features.auth import repository as auth_repo
from app.features.auth.deps import get_current_user
from app.features.auth.models import User
from app.features.autonomy import models as autonomy_models
from app.features.autonomy.router import router as autonomy_router
from app.features.engagements import models as eng_models
from app.features.engagements import repository as eng_repo


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async SQLite in-memory session for autonomy feature unit tests."""
    id_col: Column = autonomy_models.AutonomyGrant.__table__.c.id  # type: ignore[assignment]
    id_col.default = ColumnDefault(uuid4)

    tables = [autonomy_models.AutonomyGrant.__table__]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=tables)  # type: ignore[arg-type]
        )

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session

    await engine.dispose()


def _patch_sqlite_columns() -> None:
    """Swap Postgres-only uuid server-defaults for SQLite-side uuid4 (idempotent)."""
    for model in (
        auth_models.User,
        eng_models.Engagement,
        audit_models.AuditEntry,
        autonomy_models.AutonomyGrant,
    ):
        id_col: Column = model.__table__.c.id  # type: ignore[assignment]
        id_col.default = ColumnDefault(uuid4)


@pytest.fixture(autouse=True)
def _autonomy_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings env the auth dependency needs to instantiate get_settings()."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin")
    monkeypatch.setenv("ADEPTUS_ADMIN_PASSWORD_HASH", "x")
    monkeypatch.setenv("ADEPTUS_LLM_MODEL", "qwen3.5:9b")
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[tuple[AsyncClient, User, UUID], None]:
    """FastAPI app (autonomy router) on SQLite with a seeded member user + engagement.

    Yields ``(async_client, member_user, engagement_id)``. ``get_current_user`` is overridden
    to the seeded user (skips login); the real service + audit run against SQLite.
    """
    _patch_sqlite_columns()
    tables = [
        auth_models.User.__table__,
        eng_models.Engagement.__table__,
        eng_models.EngagementMember.__table__,
        audit_models.AuditEntry.__table__,
        audit_models.AuditChainHead.__table__,
        autonomy_models.AutonomyGrant.__table__,
    ]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=tables)  # type: ignore[arg-type]
        )
        await conn.execute(
            insert(audit_models.AuditChainHead).values(id=1, last_seq=0, head_hash=GENESIS_HASH)
        )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with factory() as db:
        user = await auth_repo.create_user(db, username="alice", password_hash="x")
        engagement = await eng_repo.create_engagement(
            db, name="E", scope="", client_info=None, owner_id=cast(UUID, user.id)
        )
        await db.commit()
        engagement_id = cast(UUID, engagement.id)

    app = FastAPI()
    app.include_router(autonomy_router)
    register_error_handlers(app)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, user, engagement_id

    await engine.dispose()
