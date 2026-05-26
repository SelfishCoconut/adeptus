"""Feature-local fixtures for the auth tests."""

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import Column, ColumnDefault, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.features.auth import models  # noqa: F401 — registers ORM metadata


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async SQLite in-memory session for unit tests.

    Two Postgres-specific column types need patching for SQLite:
    - User.id: server_default=text("gen_random_uuid()") is Postgres SQL that SQLite
      cannot execute.  We replace it with a Python-side ColumnDefault(uuid4) so that
      create_user() (which does db.add + flush) works without touching production code.
    - Session.ip: the INET dialect type has no SQLite equivalent.  We swap it for
      plain Text(), which stores the same string data and is sufficient for unit tests.

    Both patches mutate module-level SQLAlchemy table metadata; they are idempotent
    and harmless because each test session uses a fresh in-memory database.
    """
    # Patch Postgres-only server default so SQLite can generate UUIDs.
    # Return a real uuid.UUID object (not a str) — UUID(as_uuid=True) calls .hex on the value.
    id_col: Column = models.User.__table__.c.id  # type: ignore[assignment]
    id_col.default = ColumnDefault(uuid4)

    # Patch Postgres INET type to plain Text for SQLite DDL compatibility.
    ip_col: Column = models.Session.__table__.c.ip  # type: ignore[assignment]
    ip_col.type = Text()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session

    await engine.dispose()
