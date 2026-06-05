"""Feature-local fixtures for the chat tests.

Uses an in-memory SQLite async engine (same pattern as mcp/audit tests).

Postgres-specific column types patched for SQLite compatibility:
- ``User.id`` / ``Engagement.id`` / ``ChatMessage.id``: server_default
  ``gen_random_uuid()`` is Postgres SQL; replaced with a Python-side
  ``ColumnDefault(uuid4)``.
- ``Session.ip``: INET has no SQLite DDL equivalent; replaced with Text().

All base tables (users, sessions, engagements, engagement_members, chat_messages,
audit_entries, audit_chain_head) are created so FK references in DDL are satisfied and
the real ``audit_service.record`` can run against SQLite in the router/integration tests.
"""

from collections.abc import AsyncGenerator, Iterator
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import Column, ColumnDefault, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.features.audit import models as audit_models  # noqa: F401 — registers audit tables
from app.features.auth import models as auth_models  # noqa: F401 — registers users/sessions
from app.features.chat import models as chat_models  # noqa: F401 — registers chat_messages
from app.features.engagements import models as eng_models  # noqa: F401 — registers engagements


def _patch_sqlite_columns() -> None:
    """Swap Postgres-only column defaults/types for SQLite-compatible ones."""
    user_id_col: Column = auth_models.User.__table__.c.id  # type: ignore[assignment]
    user_id_col.default = ColumnDefault(uuid4)

    ip_col: Column = auth_models.Session.__table__.c.ip  # type: ignore[assignment]
    ip_col.type = Text()

    eng_id_col: Column = eng_models.Engagement.__table__.c.id  # type: ignore[assignment]
    eng_id_col.default = ColumnDefault(uuid4)

    chat_id_col: Column = chat_models.ChatMessage.__table__.c.id  # type: ignore[assignment]
    chat_id_col.default = ColumnDefault(uuid4)


@pytest.fixture
def mock_audit_record() -> Iterator[AsyncMock]:
    """Stub the Slice-10 audit emission for chat service tests.

    Service tests drive ``stream_assistant_reply`` with a *mocked* db session, so the
    real ``audit_service.record`` (which runs SQL) cannot execute. Tests that need to
    assert the ``ai_call`` emission request this fixture; router/integration tests use
    the real record against SQLite instead.
    """
    with patch("app.features.chat.service.audit_service.record", new_callable=AsyncMock) as mock:
        yield mock


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async SQLite in-memory session for chat unit tests."""
    _patch_sqlite_columns()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session

    await engine.dispose()
