"""Feature-local fixtures for the mcp repository tests.

Uses an in-memory SQLite async engine (same pattern as auth/engagements tests).

Postgres-specific types patched for SQLite compatibility:
- ``User.id`` / ``Engagement.id`` / ``ToolRun.id``: server_default=text("gen_random_uuid()")
  is Postgres SQL; replaced with a Python-side ColumnDefault(uuid4).
- ``Session.ip``: INET has no SQLite DDL equivalent; replaced with Text().
- ``ToolRun.args``: JSONB has no SQLite DDL equivalent; replaced with JSON().

All four base tables (users, sessions, engagements, engagement_members, tool_runs) are
created so FK references in DDL are satisfied.  SQLite does not enforce FK constraints
at runtime, so repository tests can insert ToolRun rows with bare engagement UUIDs
without needing real Engagement rows.
"""

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import JSON, Column, ColumnDefault, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.features.auth import models as auth_models  # noqa: F401 — registers users/sessions
from app.features.engagements import models as eng_models  # noqa: F401 — registers engagements
from app.features.mcp import models as mcp_models  # noqa: F401 — registers tool_runs


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async SQLite in-memory session for MCP unit tests."""
    # Patch User.id: Postgres gen_random_uuid() → Python-side uuid4.
    user_id_col: Column = auth_models.User.__table__.c.id  # type: ignore[assignment]
    user_id_col.default = ColumnDefault(uuid4)

    # Patch Session.ip: INET → Text.
    ip_col: Column = auth_models.Session.__table__.c.ip  # type: ignore[assignment]
    ip_col.type = Text()

    # Patch Engagement.id: Postgres gen_random_uuid() → Python-side uuid4.
    eng_id_col: Column = eng_models.Engagement.__table__.c.id  # type: ignore[assignment]
    eng_id_col.default = ColumnDefault(uuid4)

    # Patch ToolRun.id: Postgres gen_random_uuid() → Python-side uuid4.
    tool_run_id_col: Column = mcp_models.ToolRun.__table__.c.id  # type: ignore[assignment]
    tool_run_id_col.default = ColumnDefault(uuid4)

    # Patch ToolRun.args: JSONB → JSON (SQLite-compatible).
    args_col: Column = mcp_models.ToolRun.__table__.c.args  # type: ignore[assignment]
    args_col.type = JSON()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session

    await engine.dispose()
