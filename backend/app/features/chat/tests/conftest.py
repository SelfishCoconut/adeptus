"""Feature-local fixtures for the chat tests.

Uses an in-memory SQLite async engine (same pattern as mcp/audit tests).

Postgres-specific column types patched for SQLite compatibility:
- ``User.id`` / ``Engagement.id`` / ``ChatMessage.id`` / ``AuditEntry.id``:
  ``server_default=text("gen_random_uuid()")`` → Python-side ``ColumnDefault(uuid4)``.
- ``Session.ip``: INET has no SQLite DDL equivalent; replaced with Text().

Only the tables this feature touches are created (not the full ``Base.metadata``) so the
combined test session never trips on another feature's Postgres-only DDL — mirroring the
audit conftest. ``audit_chain_head`` is created so the real ``audit_service.record`` can
run on SQLite (which silently ignores ``FOR UPDATE``) in the router/integration tests.
"""

from collections.abc import AsyncGenerator, Iterator
from typing import cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import Column, ColumnDefault, Connection, Table, Text
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
from app.features.audit import models as audit_models
from app.features.auth import models as auth_models
from app.features.auth.router import router as auth_router
from app.features.autonomy import models as autonomy_models
from app.features.chat import models as chat_models
from app.features.chat.router import router as chat_router
from app.features.engagements import models as eng_models
from app.features.graph import models as graph_models
from app.features.personas import models as persona_models


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Provide the required settings env so get_settings() can instantiate (the service
    reads ADEPTUS_LLM_MODEL when streaming)."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin")
    monkeypatch.setenv("ADEPTUS_ADMIN_PASSWORD_HASH", "x")
    monkeypatch.setenv("ADEPTUS_LLM_MODEL", "qwen3.5:9b")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _patch_sqlite_columns() -> None:
    """Swap Postgres-only column defaults/types for SQLite-compatible ones."""
    for model in (
        auth_models.User,
        eng_models.Engagement,
        chat_models.ChatMessage,
        audit_models.AuditEntry,
        # Slice 12: the chat streamer reads the live graph for the §5.3 subset.
        graph_models.GraphNode,
        graph_models.GraphEdge,
        # Slice 15: the streamer resolves the turn's persona (chat → personas).
        persona_models.Persona,
        # Slice 16: the read/replay paths query this turn's approval cards.
        approvals_models.ApprovalRequest,
        # Slice 18: create_requests_for_turn reads active standing-autonomy grants.
        autonomy_models.AutonomyGrant,
    ):
        id_col: Column = model.__table__.c.id  # type: ignore[assignment]
        id_col.default = ColumnDefault(uuid4)

    ip_col: Column = auth_models.Session.__table__.c.ip  # type: ignore[assignment]
    ip_col.type = Text()


_TABLES: list[Table] = [
    cast(Table, model.__table__)
    for model in (
        auth_models.User,
        auth_models.Session,
        eng_models.Engagement,
        eng_models.EngagementMember,
        chat_models.ChatMessage,
        audit_models.AuditEntry,
        audit_models.AuditChainHead,
        graph_models.GraphNode,
        graph_models.GraphEdge,
        persona_models.Persona,
        approvals_models.ApprovalRequest,
        autonomy_models.AutonomyGrant,
    )
]


def _create_tables(sync_conn: Connection) -> None:
    Base.metadata.create_all(sync_conn, tables=_TABLES)


async def _make_engine() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    _patch_sqlite_columns()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_create_tables)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, factory


@pytest.fixture
def mock_audit_record() -> Iterator[AsyncMock]:
    """Stub the Slice-10 audit emission for chat service tests.

    Service streaming tests assert the ``ai_call`` emission via this mock; router/
    integration tests use the real ``record`` against SQLite instead.
    """
    with patch("app.features.chat.service.audit_service.record", new_callable=AsyncMock) as mock:
        yield mock


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async SQLite in-memory session for chat unit tests."""
    engine, factory = await _make_engine()
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def db_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Async SQLite factory with ``service.get_sessionmaker`` patched to it.

    ``stream_assistant_reply`` opens its OWN session (the auth session is already
    closed), so streaming tests must seed and assert against the same engine the service
    streams into.
    """
    engine, factory = await _make_engine()
    with patch("app.features.chat.service.get_sessionmaker", return_value=factory):
        yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def app_and_factory() -> AsyncGenerator[
    tuple[FastAPI, async_sessionmaker[AsyncSession]], None
]:
    """A FastAPI app (auth + chat routers) backed by a fresh SQLite engine.

    Both the WS auth path (``router.get_sessionmaker``) and the streaming path
    (``service.get_sessionmaker``) are patched to the same in-memory factory so the
    HTTP, WS-auth, and streaming sessions all share one database.
    """
    engine, factory = await _make_engine()

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(chat_router)
    register_error_handlers(app)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db

    with (
        patch("app.features.chat.router.get_sessionmaker", return_value=factory),
        patch("app.features.chat.service.get_sessionmaker", return_value=factory),
    ):
        yield app, factory

    await engine.dispose()
