"""Feature-local fixtures for the autonomy feature tests.

In-memory SQLite async engine, mirroring approvals/audit conftest. Only the
``autonomy_grants`` table is created (the shared ``Base.metadata`` also holds other
features' Postgres-only types that don't render on SQLite). SQLite does not enforce FK
constraints at runtime, so grants are inserted with bare engagement/user UUIDs — but the
``reason`` CHECK and the partial unique index ARE enforced, so those guards are exercised.

Postgres-specific bit patched for SQLite: ``AutonomyGrant.id`` server_default
``gen_random_uuid()`` → Python-side uuid4.
"""

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import Column, ColumnDefault
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.features.auth import models as auth_models  # noqa: F401 — register users FK target
from app.features.autonomy import models as autonomy_models
from app.features.engagements import (
    models as eng_models,  # noqa: F401 — register engagements FK target
)


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
