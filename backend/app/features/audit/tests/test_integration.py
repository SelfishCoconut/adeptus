"""Integration tests for the audit hash-chain against a real Postgres (§14).

These cover what SQLite unit tests cannot:
  * the real timestamptz + JSONB round-trip through the shared canonicalization (Risk 2),
  * the concurrent single-appender no-fork guarantee under real row locking (Risk 1).

Each test runs against a throwaway Postgres schema (mirrors graph/mcp integration) and
skips cleanly when Postgres is unreachable. Marked ``integration`` — excluded from the
default ``make test-backend`` run; executed by ``make test-integration``. Point at a
server with ``ADEPTUS_TEST_DATABASE_URL`` (defaults to the compose Postgres).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import delete, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import Base, get_engine, get_sessionmaker
from app.features.audit import repository, verify
from app.features.audit.hashing import GENESIS_HASH
from app.features.audit.models import AuditChainHead, AuditEntry

pytestmark = pytest.mark.integration

_DEFAULT_DSN = "postgresql+asyncpg://adeptus:adeptus@localhost:5432/adeptus"


def _dsn() -> str:
    return os.environ.get("ADEPTUS_TEST_DATABASE_URL") or _DEFAULT_DSN


@pytest_asyncio.fixture
async def pg_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Session factory scoped to a throwaway Postgres schema. Skips if Postgres is down."""
    monkeypatch.setenv("DATABASE_URL", _dsn())
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin_it")
    monkeypatch.setenv(
        "ADEPTUS_ADMIN_PASSWORD_HASH",
        "$argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$hashhashhashhashhashhashhashhashhashhashhas",
    )
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()

    schema = f"audit_it_{uuid.uuid4().hex[:12]}"
    admin_engine = create_async_engine(_dsn(), isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    except Exception as exc:  # noqa: BLE001
        await admin_engine.dispose()
        pytest.skip(f"Postgres not available for integration tests: {exc}")

    engine = create_async_engine(_dsn(), connect_args={"server_settings": {"search_path": schema}})
    # Only the audit tables are needed; create them (other models' Postgres types are
    # all native, but scoping to the two tables keeps the schema minimal).
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(
                c,
                tables=[AuditEntry.__table__, AuditChainHead.__table__],  # type: ignore[list-item]
            )
        )

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        yield factory
    finally:
        await engine.dispose()
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()
        get_settings.cache_clear()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()


async def _seed_head(factory: async_sessionmaker[AsyncSession]) -> None:
    """Seed the genesis head row, mirroring the migration in production."""
    async with factory() as db:
        db.add(AuditChainHead(id=1, last_seq=0, head_hash=GENESIS_HASH))
        await db.commit()


async def test_audit_chain_intact_after_mixed_actions(
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Headline §14 happy path: mixed actions form a clean, verifiable chain on Postgres."""
    await _seed_head(pg_factory)
    async with pg_factory() as db:
        await repository.append_entry(
            db, action="login", actor_user_id=uuid.uuid4(), payload={"z": 1, "a": {"y": 2, "x": 3}}
        )
        await repository.append_entry(
            db,
            action="tool_run",
            actor_user_id=uuid.uuid4(),
            engagement_id=uuid.uuid4(),
            target_type="tool_run",
            target_id=str(uuid.uuid4()),
            payload={"server": "s", "tool": "t", "target": "http://localhost:3000"},
        )
        await repository.append_entry(
            db,
            action="graph_node_created",
            actor_user_id=uuid.uuid4(),
            engagement_id=uuid.uuid4(),
            target_type="node",
            target_id=str(uuid.uuid4()),
        )
        await db.commit()

    async with pg_factory() as db:
        ok, verified, broke = await verify.verify(db)
        assert ok, broke
        assert verified == 3


async def test_audit_chain_detects_tampering(
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Headline §14 tamper-evidence: a rewritten field is caught at the exact seq."""
    await _seed_head(pg_factory)
    async with pg_factory() as db:
        for _ in range(4):
            await repository.append_entry(db, action="login", actor_user_id=uuid.uuid4())
        await db.commit()

    async with pg_factory() as db:
        await db.execute(
            update(AuditEntry).where(AuditEntry.seq == 3).values(payload={"tampered": True})
        )
        await db.commit()

    async with pg_factory() as db:
        ok, _, broke = await verify.verify(db)
        assert not ok
        assert broke is not None
        assert broke.kind == "content-tamper"
        assert broke.seq == 3


async def test_audit_chain_detects_row_deletion(
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Deleting a middle row breaks the chain (seq gap)."""
    await _seed_head(pg_factory)
    async with pg_factory() as db:
        for _ in range(5):
            await repository.append_entry(db, action="login", actor_user_id=uuid.uuid4())
        await db.commit()

    async with pg_factory() as db:
        await db.execute(delete(AuditEntry).where(AuditEntry.seq == 3))
        await db.commit()

    async with pg_factory() as db:
        ok, _, broke = await verify.verify(db)
        assert not ok
        assert broke is not None
        assert broke.kind == "seq-gap"


async def test_concurrent_appends_no_fork(
    pg_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Risk 1 (load-bearing): N concurrent appends, each in its own session/transaction,
    serialize under the audit_chain_head FOR UPDATE lock — contiguous seq, no fork."""
    await _seed_head(pg_factory)
    n = 12

    async def one_append() -> None:
        async with pg_factory() as db:
            await repository.append_entry(db, action="login", actor_user_id=uuid.uuid4())
            await db.commit()

    await asyncio.gather(*[one_append() for _ in range(n)])

    async with pg_factory() as db:
        seqs = [e.seq async for e in repository.iter_chain_ordered(db)]
        assert seqs == list(range(1, n + 1))  # gap-free, no duplicates, no fork
        ok, verified, broke = await verify.verify(db)
        assert ok, broke
        assert verified == n
