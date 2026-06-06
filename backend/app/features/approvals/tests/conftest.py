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

from collections.abc import AsyncGenerator, Iterator
from typing import cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import Column, ColumnDefault, Connection, Table, Text, insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.db import Base, get_db
from app.core.errors import register_error_handlers
from app.features.approvals import models as approvals_models
from app.features.approvals.router import router as approvals_router
from app.features.audit import models as audit_models
from app.features.audit.hashing import GENESIS_HASH
from app.features.auth import models as auth_models
from app.features.auth.router import router as auth_router
from app.features.autonomy import models as autonomy_models
from app.features.chat import models as chat_models
from app.features.engagements import models as eng_models
from app.features.mcp import models as mcp_models


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async SQLite in-memory session for approvals feature unit tests."""
    id_col: Column = approvals_models.ApprovalRequest.__table__.c.id  # type: ignore[assignment]
    id_col.default = ColumnDefault(uuid4)
    # Slice 18: create_requests_for_turn reads autonomy_grants (get_active_reasons) to
    # decide auto-approval, so the table must exist (empty ⇒ Slice 16/17 behaviour).
    grant_id_col: Column = autonomy_models.AutonomyGrant.__table__.c.id  # type: ignore[assignment]
    grant_id_col.default = ColumnDefault(uuid4)

    tables = [
        approvals_models.ApprovalRequest.__table__,
        autonomy_models.AutonomyGrant.__table__,
    ]
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda c: Base.metadata.create_all(c, tables=tables)  # type: ignore[arg-type]
        )

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session

    await engine.dispose()


# ---------------------------------------------------------------------------
# Router/integration harness: a full app on SQLite (auth + approvals), real audit
# ---------------------------------------------------------------------------


def _patch_sqlite_columns() -> None:
    """Swap Postgres-only column defaults/types for SQLite-compatible ones (idempotent)."""
    for model in (
        auth_models.User,
        eng_models.Engagement,
        chat_models.ChatMessage,
        mcp_models.ToolRun,
        audit_models.AuditEntry,
        approvals_models.ApprovalRequest,
    ):
        id_col: Column = model.__table__.c.id  # type: ignore[assignment]
        id_col.default = ColumnDefault(uuid4)
    ip_col: Column = auth_models.Session.__table__.c.ip  # type: ignore[assignment]
    ip_col.type = Text()


_ROUTER_TABLES: list[Table] = [
    cast(Table, model.__table__)
    for model in (
        auth_models.User,
        auth_models.Session,
        eng_models.Engagement,
        eng_models.EngagementMember,
        chat_models.ChatMessage,
        mcp_models.ToolRun,
        audit_models.AuditEntry,
        audit_models.AuditChainHead,
        approvals_models.ApprovalRequest,
    )
]


def _create_router_tables(sync_conn: Connection) -> None:
    Base.metadata.create_all(sync_conn, tables=_ROUTER_TABLES)


async def _make_router_engine() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    _patch_sqlite_columns()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_create_router_tables)
        # Seed the audit chain head so the real audit_service.record can append on SQLite.
        await conn.execute(
            insert(audit_models.AuditChainHead).values(id=1, last_seq=0, head_hash=GENESIS_HASH)
        )
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, factory


@pytest_asyncio.fixture
async def app_and_factory() -> AsyncGenerator[
    tuple[FastAPI, async_sessionmaker[AsyncSession], AsyncMock], None
]:
    """A FastAPI app (auth + approvals routers) on a fresh SQLite engine.

    ``mcp.service.execute_tool_run`` is mocked (no real subprocess) and yielded so the
    approve tests can assert the run handoff; the real ``audit_service.record`` runs
    against SQLite (which ignores ``FOR UPDATE``).
    """
    engine, factory = await _make_router_engine()

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(approvals_router)
    register_error_handlers(app)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    with patch(
        "app.features.approvals.service.mcp_service.execute_tool_run", new_callable=AsyncMock
    ) as exec_run:
        from types import SimpleNamespace

        exec_run.return_value = SimpleNamespace(tool_run_id=uuid4())
        yield app, factory, exec_run

    await engine.dispose()


@pytest.fixture(autouse=True)
def _approvals_settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Provide the settings env the auth dependency needs to instantiate get_settings()."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin")
    monkeypatch.setenv("ADEPTUS_ADMIN_PASSWORD_HASH", "x")
    monkeypatch.setenv("ADEPTUS_LLM_MODEL", "qwen3.5:9b")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
