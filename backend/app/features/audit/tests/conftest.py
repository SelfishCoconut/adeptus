"""Feature-local fixtures for the audit feature tests.

Repository tests use an in-memory SQLite async engine, mirroring graph/mcp/conftest.
Postgres-specific bits patched for SQLite:
- ``AuditEntry.id``: ``server_default=text("gen_random_uuid()")`` → Python-side uuid4.
- ``payload`` already uses ``JSONB().with_variant(JSON(), "sqlite")`` in the model.

The single ``audit_chain_head`` singleton row (seeded by the migration in production)
is inserted here so ``append_entry`` finds a head to lock. The audit attribution
columns carry NO foreign keys, so tests insert entries with bare UUIDs and need no
``users``/``engagements`` rows.

NOTE: SQLite ignores ``FOR UPDATE`` and gives each ``:memory:`` connection its own DB,
so true concurrent no-fork locking is exercised only by the Postgres integration test
(``test_concurrent_appends_no_fork``). The unit suite verifies serialized correctness.
"""

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import Column, ColumnDefault, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.features.audit import models as audit_models
from app.features.audit.hashing import GENESIS_HASH


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async SQLite in-memory session for audit feature unit tests, head row seeded."""
    entry_id_col: Column = audit_models.AuditEntry.__table__.c.id  # type: ignore[assignment]
    entry_id_col.default = ColumnDefault(uuid4)

    tables = [audit_models.AuditEntry.__table__, audit_models.AuditChainHead.__table__]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # Create only the audit tables: the shared Base.metadata also holds other
        # features' models whose Postgres-only types (INET, etc.) don't render on SQLite.
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=tables)  # type: ignore[arg-type]
        )
        await conn.execute(
            insert(audit_models.AuditChainHead).values(id=1, last_seq=0, head_hash=GENESIS_HASH)
        )

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session

    await engine.dispose()
