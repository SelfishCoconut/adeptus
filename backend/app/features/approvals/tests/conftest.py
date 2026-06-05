"""Feature-local fixtures for the approvals feature tests.

Uses an in-memory SQLite async engine, mirroring audit/conftest. Only the
``approval_requests`` table is created: the shared ``Base.metadata`` also holds other
features' models whose Postgres-only types (``INET`` on ``sessions``) don't render on
SQLite. SQLite does not enforce FK constraints at runtime, so unit tests insert
``ApprovalRequest`` rows with bare engagement/chat-message/user UUIDs without needing real
parent rows — but the ``status`` CHECK constraint IS enforced by SQLite, so the
state-vocabulary guard is exercised here.

Postgres-specific bits patched for SQLite:
- ``ApprovalRequest.id``: ``server_default=text("gen_random_uuid()")`` → Python-side uuid4.
- ``args`` / ``reasons`` already use ``JSONB().with_variant(JSON(), "sqlite")``.
"""

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import Column, ColumnDefault
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.features.approvals import models as approvals_models

# Register the FK-target tables on Base.metadata so the approval_requests FK columns
# resolve at DDL-compile time. Only approval_requests is actually created (SQLite does
# not enforce FKs at runtime), so the parent tables' Postgres-only types never render.
from app.features.auth import models as _auth_models  # noqa: F401,E402 — registers users
from app.features.chat import models as _chat_models  # noqa: F401,E402 — chat_messages
from app.features.engagements import models as _eng_models  # noqa: F401,E402 — engagements
from app.features.mcp import models as _mcp_models  # noqa: F401,E402 — tool_runs


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async SQLite in-memory session for approvals feature unit tests."""
    id_col: Column = approvals_models.ApprovalRequest.__table__.c.id  # type: ignore[assignment]
    id_col.default = ColumnDefault(uuid4)

    tables = [approvals_models.ApprovalRequest.__table__]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=tables)  # type: ignore[arg-type]
        )

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session

    await engine.dispose()
