"""Feature-local fixtures and mocks for the findings feature tests.

Repository tests use an in-memory SQLite async engine following the same pattern
as graph/tests/conftest.py.

Postgres-specific types patched for SQLite compatibility:
- ``User.id`` / ``Engagement.id`` / ``GraphNode.id`` / ``Finding.id`` /
  ``FindingHistory.id``: ``server_default=text("gen_random_uuid()")`` →
  Python-side ``ColumnDefault(uuid4)``.
- ``Session.ip``: INET has no SQLite DDL equivalent → Text().

SQLite does not enforce FK constraints at runtime, so repository tests can insert
Finding rows with bare engagement/node UUIDs without needing real parent rows
(node-link validation is exercised explicitly where it matters).
"""

from collections.abc import AsyncGenerator, Iterator
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import Column, ColumnDefault, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.features.auth import models as auth_models  # noqa: F401 — registers users/sessions
from app.features.engagements import models as eng_models  # noqa: F401 — registers engagements
from app.features.findings import models as findings_models  # noqa: F401 — registers findings
from app.features.graph import models as graph_models  # noqa: F401 — registers graph_nodes (FK)


@pytest.fixture
def mock_audit_record() -> Iterator[AsyncMock]:
    """Stub the audit emission for findings service tests.

    Service tests drive the mutators with a *mocked* db session, so the real
    ``audit_service.record`` (which runs SQL) cannot execute. They request this
    fixture explicitly to replace ``record`` with an AsyncMock and assert it was
    called with the right action/target. NOT autouse: repository and schema tests
    do not touch the service module, and router tests exercise the *real* audit
    path against the SQLite test DB (mirroring the graph router tests).
    """
    with patch(
        "app.features.findings.service.audit_service.record", new_callable=AsyncMock
    ) as mock:
        yield mock


def _apply_sqlite_patches() -> None:
    """Patch Postgres-specific column types/defaults for SQLite compatibility."""
    user_id_col: Column = auth_models.User.__table__.c.id  # type: ignore[assignment]
    user_id_col.default = ColumnDefault(uuid4)

    ip_col: Column = auth_models.Session.__table__.c.ip  # type: ignore[assignment]
    ip_col.type = Text()

    eng_id_col: Column = eng_models.Engagement.__table__.c.id  # type: ignore[assignment]
    eng_id_col.default = ColumnDefault(uuid4)

    node_id_col: Column = graph_models.GraphNode.__table__.c.id  # type: ignore[assignment]
    node_id_col.default = ColumnDefault(uuid4)

    finding_id_col: Column = findings_models.Finding.__table__.c.id  # type: ignore[assignment]
    finding_id_col.default = ColumnDefault(uuid4)

    hist_id_col: Column = findings_models.FindingHistory.__table__.c.id  # type: ignore[assignment]
    hist_id_col.default = ColumnDefault(uuid4)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async SQLite in-memory session for findings feature unit tests."""
    _apply_sqlite_patches()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session

    await engine.dispose()
