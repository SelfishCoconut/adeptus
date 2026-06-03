"""Feature-local fixtures and mocks for the graph feature tests.

Repository tests use an in-memory SQLite async engine following the same pattern
as mcp/tests/conftest.py and engagements/tests/conftest.py.

Postgres-specific types patched for SQLite compatibility:
- ``User.id`` / ``Engagement.id`` / ``GraphNode.id`` / ``GraphEdge.id`` /
  ``GraphNodeHistory.id`` / ``GraphEdgeHistory.id``:
  ``server_default=text("gen_random_uuid()")`` → Python-side ``ColumnDefault(uuid4)``.
- ``Session.ip``: INET has no SQLite DDL equivalent → Text().

SQLite does not enforce FK constraints at runtime, so repository tests can insert
GraphNode rows with bare engagement UUIDs without needing real Engagement rows.
SQLite does support partial unique indexes with a WHERE clause; the partial unique
index on ``graph_edges`` (``uq_graph_edges_live_triple``) is exercised in tests.
"""

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import Column, ColumnDefault, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.features.auth import models as auth_models  # noqa: F401 — registers users/sessions
from app.features.engagements import models as eng_models  # noqa: F401 — registers engagements
from app.features.graph import models as graph_models  # noqa: F401 — registers graph tables


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Async SQLite in-memory session for graph feature unit tests."""
    # Patch User.id: Postgres gen_random_uuid() → Python-side uuid4.
    user_id_col: Column = auth_models.User.__table__.c.id  # type: ignore[assignment]
    user_id_col.default = ColumnDefault(uuid4)

    # Patch Session.ip: INET → Text.
    ip_col: Column = auth_models.Session.__table__.c.ip  # type: ignore[assignment]
    ip_col.type = Text()

    # Patch Engagement.id: Postgres gen_random_uuid() → Python-side uuid4.
    eng_id_col: Column = eng_models.Engagement.__table__.c.id  # type: ignore[assignment]
    eng_id_col.default = ColumnDefault(uuid4)

    # Patch graph table PKs: Postgres gen_random_uuid() → Python-side uuid4.
    node_id_col: Column = graph_models.GraphNode.__table__.c.id  # type: ignore[assignment]
    node_id_col.default = ColumnDefault(uuid4)

    edge_id_col: Column = graph_models.GraphEdge.__table__.c.id  # type: ignore[assignment]
    edge_id_col.default = ColumnDefault(uuid4)

    node_hist_id_col: Column = graph_models.GraphNodeHistory.__table__.c.id  # type: ignore[assignment]
    node_hist_id_col.default = ColumnDefault(uuid4)

    edge_hist_id_col: Column = graph_models.GraphEdgeHistory.__table__.c.id  # type: ignore[assignment]
    edge_hist_id_col.default = ColumnDefault(uuid4)

    undo_id_col: Column = graph_models.GraphUserUndoStack.__table__.c.id  # type: ignore[assignment]
    undo_id_col.default = ColumnDefault(uuid4)

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # SQLAlchemy renders ``postgresql_where`` only for Postgres; on SQLite the
        # partial unique index ``uq_graph_edges_live_triple`` is created as a full
        # unique index.  Replace it with a proper SQLite partial unique index so that
        # soft-deleted edges do not block re-creating the same (engagement, source,
        # target, relation) triple.
        from sqlalchemy import text as _text

        await conn.execute(_text("DROP INDEX IF EXISTS uq_graph_edges_live_triple"))
        await conn.execute(
            _text(
                "CREATE UNIQUE INDEX uq_graph_edges_live_triple"
                " ON graph_edges (engagement_id, source_id, target_id, relation)"
                " WHERE deleted = 0"
            )
        )

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session

    await engine.dispose()
