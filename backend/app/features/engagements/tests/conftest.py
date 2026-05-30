"""Feature-local fixtures for the engagements tests.

Mirrors app/features/auth/tests/conftest.py.

SQLite in-memory is used instead of a real Postgres container so that
repository-layer unit tests have no external service dependency.  Three
Postgres-specific column types are patched before create_all:

- ``User.id`` / ``Engagement.id``: ``server_default=text("gen_random_uuid()")``
  is Postgres SQL that SQLite cannot execute.  We replace each with a
  Python-side ``ColumnDefault(uuid4)`` so flush() populates the id fields.
- ``Session.ip``: the ``INET`` dialect type has no SQLite equivalent; we swap
  it for plain ``Text()``.

Both auth and engagements models are imported (noqa F401) so that
``Base.metadata.create_all`` builds the ``users``, ``sessions``,
``engagements``, and ``engagement_members`` tables.  The ``users`` table is
needed to satisfy the FK on ``engagement_members.user_id`` and to drive the
username JOIN in ``get_members``.

Note: SQLite does not enforce FK / ON-DELETE-CASCADE by default.  That is
acceptable here because repository methods perform explicit inserts/deletes
and do not rely on DB cascades.
"""

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import Column, ColumnDefault, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.features.auth import models as auth_models  # noqa: F401 — registers users/sessions
from app.features.engagements import models as eng_models  # noqa: F401 — registers engagements


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async SQLite in-memory session for engagements unit tests."""
    # Patch User.id: replace Postgres gen_random_uuid() server default with a
    # Python-side uuid4 so SQLite can generate ids during flush().
    user_id_col: Column = auth_models.User.__table__.c.id  # type: ignore[assignment]
    user_id_col.default = ColumnDefault(uuid4)

    # Patch Session.ip: INET has no SQLite DDL equivalent.
    ip_col: Column = auth_models.Session.__table__.c.ip  # type: ignore[assignment]
    ip_col.type = Text()

    # Patch Engagement.id: same gen_random_uuid() pattern as User.id.
    eng_id_col: Column = eng_models.Engagement.__table__.c.id  # type: ignore[assignment]
    eng_id_col.default = ColumnDefault(uuid4)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session

    await engine.dispose()
