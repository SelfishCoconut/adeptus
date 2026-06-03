"""Router tests for the graph feature (task 6).

Test harness
------------
Two tiers:

1. **Real-writer happy-path tests** — an ``AsyncClient`` is wired to a real
   FastAPI app backed by an in-memory SQLite test DB.  Both the request-scoped
   ``get_db`` dependency AND the writer's internal ``get_sessionmaker`` are
   monkeypatched to the same test DB factory so the full service → writer →
   repository → in-memory-graph path executes without touching Postgres.

   ``writer.reset_state()`` is called before each test via an autouse fixture
   so no writer state leaks between tests.

   Why the real writer?  The task spec prefers the real path where feasible.
   The only complication is the dual sessionmaker override, which mirrors the
   approach in ``test_writer.py`` exactly.

2. **Mocked-service error tests** — for 401/404/409/422 error cases we patch
   ``app.features.graph.router.service.*`` with AsyncMock so we don't need to
   set up complex DB state (archived engagement, non-member user, etc.).  This
   mirrors the MCP router test pattern for error cases.  The 422 test sends a
   bad ``type`` value to Pydantic and needs no service mock at all.

Engagement + membership setup (happy-path tests)
-------------------------------------------------
Each happy-path test creates a real Engagement and EngagementMember row in the
test DB so ``service._require_member`` (which calls
``eng_repo.get_engagement_for_member``) passes the chokepoint.  A user row and
session row are inserted via the auth API login so ``get_current_user`` resolves
to a real User.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from argon2 import PasswordHasher
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Column, ColumnDefault, Text
from sqlalchemy import text as _text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.db import Base, get_db
from app.core.errors import register_error_handlers
from app.features.auth import models as auth_models
from app.features.auth import repository as auth_repo
from app.features.auth.router import router as auth_router
from app.features.engagements import models as eng_models
from app.features.engagements import repository as eng_repo
from app.features.engagements.router import router as engagements_router
from app.features.graph import models as graph_models  # noqa: F401 — registers graph tables
from app.features.graph import writer as gw
from app.features.graph.errors import EngagementArchived
from app.features.graph.router import router as graph_router
from app.features.graph.service import EngagementNotFound

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_hasher = PasswordHasher()


def _apply_sqlite_patches() -> None:
    """Patch Postgres-specific column types/defaults for SQLite compatibility.

    Idempotent: repeated calls overwrite with the same values, which is safe
    because column defaults are checked per-statement, not cached globally.
    """
    user_id_col: Column = auth_models.User.__table__.c.id  # type: ignore[assignment]
    user_id_col.default = ColumnDefault(uuid4)

    ip_col: Column = auth_models.Session.__table__.c.ip  # type: ignore[assignment]
    ip_col.type = Text()

    eng_id_col: Column = eng_models.Engagement.__table__.c.id  # type: ignore[assignment]
    eng_id_col.default = ColumnDefault(uuid4)

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


async def _build_engine_and_factory() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Build a fresh in-memory SQLite engine with the full schema."""
    _apply_sqlite_patches()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Replace the PostgreSQL partial unique index with a SQLite-compatible one.
        await conn.execute(_text("DROP INDEX IF EXISTS uq_graph_edges_live_triple"))
        await conn.execute(
            _text(
                "CREATE UNIQUE INDEX uq_graph_edges_live_triple"
                " ON graph_edges (engagement_id, source_id, target_id, relation)"
                " WHERE deleted = 0"
            )
        )

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    return engine, factory


# ---------------------------------------------------------------------------
# Fixtures — shared setup
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True)
async def reset_writer() -> AsyncGenerator[None, None]:
    """Reset the writer registry before and after every test.

    Prevents writer state (including the in-memory NetworkX graph) from leaking
    between tests.  Mirrors the autouse pattern documented in test_writer.py.
    """
    gw.reset_state()
    yield
    gw.reset_state()


@pytest_asyncio.fixture
async def app_and_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[FastAPI, async_sessionmaker[AsyncSession]], None]:
    """Real FastAPI app backed by an in-memory SQLite test DB.

    Both the request-scoped ``get_db`` dependency AND the writer's internal
    ``get_sessionmaker`` are pointed at the same test DB factory so the full
    service → writer → repository path executes in-process against the test DB.
    """
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin")
    monkeypatch.setenv(
        "ADEPTUS_ADMIN_PASSWORD_HASH",
        "$argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    from app.core.config import get_settings

    get_settings.cache_clear()

    engine, factory = await _build_engine_and_factory()

    # Override the writer's internal sessionmaker to use the test DB.
    import app.features.graph.writer as _writer_mod

    original_get_sm = _writer_mod.get_sessionmaker
    _writer_mod.get_sessionmaker = lambda: factory  # type: ignore[assignment]

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(engagements_router)
    app.include_router(graph_router)
    register_error_handlers(app)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    yield app, factory

    gw.reset_state()
    _writer_mod.get_sessionmaker = original_get_sm
    get_settings.cache_clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def member_client_and_ids(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> AsyncGenerator[tuple[AsyncClient, UUID, UUID], None]:
    """Authenticated AsyncClient + (engagement_id, user_id) for happy-path tests.

    Creates a user, logs in, creates an engagement, and adds the user as an
    owner member — so the membership chokepoint passes on every endpoint.
    """
    app, factory = app_and_factory

    # Insert user.
    pw_hash = _hasher.hash("testpass")
    async with factory() as session:
        user = await auth_repo.create_user(
            session,
            username="member",
            password_hash=pw_hash,
            role="user",
        )
        await session.commit()
        await session.refresh(user)
        user_id: UUID = user.id  # type: ignore[assignment]

    # Create engagement + membership row.
    async with factory() as session:
        engagement = await eng_repo.create_engagement(
            session,
            name="Test Engagement",
            scope="test",
            client_info=None,
            owner_id=user_id,
        )
        await session.commit()
        await session.refresh(engagement)
        engagement_id: UUID = engagement.id  # type: ignore[assignment]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "member", "password": "testpass"},
        )
        assert resp.status_code == 200, resp.text
        yield client, engagement_id, user_id


@pytest_asyncio.fixture
async def anon_app(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[FastAPI, None]:
    """Minimal unauthenticated app used for 401 tests (no test DB needed)."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin")
    monkeypatch.setenv(
        "ADEPTUS_ADMIN_PASSWORD_HASH",
        "$argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    from app.core.config import get_settings

    get_settings.cache_clear()

    engine, factory = await _build_engine_and_factory()

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(graph_router)
    register_error_handlers(app)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    yield app

    get_settings.cache_clear()
    await engine.dispose()


@pytest_asyncio.fixture
async def error_client_and_app(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[tuple[AsyncClient, FastAPI, async_sessionmaker[AsyncSession]], None]:
    """Authenticated AsyncClient for mocked-service error tests.

    Has a real user + session but the service is mocked at the test level, so
    no engagement or graph rows are needed.
    """
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("ADEPTUS_ADMIN_USER", "admin")
    monkeypatch.setenv(
        "ADEPTUS_ADMIN_PASSWORD_HASH",
        "$argon2id$v=19$m=65536,t=3,p=4$dGVzdHNhbHQ$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    )
    from app.core.config import get_settings

    get_settings.cache_clear()

    engine, factory = await _build_engine_and_factory()

    pw_hash = _hasher.hash("errpass")
    async with factory() as session:
        user = await auth_repo.create_user(
            session,
            username="erruser",
            password_hash=pw_hash,
            role="user",
        )
        await session.commit()
        await session.refresh(user)

    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(graph_router)
    register_error_handlers(app)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "erruser", "password": "errpass"},
        )
        assert resp.status_code == 200, resp.text
        yield client, app, factory

    get_settings.cache_clear()
    await engine.dispose()


# ---------------------------------------------------------------------------
# Happy-path tests — real writer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_node_201(
    member_client_and_ids: tuple[AsyncClient, UUID, UUID],
) -> None:
    """POST .../graph/nodes returns 201 with the created Node."""
    client, engagement_id, _ = member_client_and_ids

    resp = await client.post(
        f"/api/v1/engagements/{engagement_id}/graph/nodes",
        json={"type": "host", "label": "10.0.0.1", "properties": {"os": "linux"}},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["type"] == "host"
    assert body["label"] == "10.0.0.1"
    assert body["properties"] == {"os": "linux"}
    assert body["deleted"] is False
    assert "id" in body
    assert UUID(body["id"])  # valid UUID
    assert str(engagement_id) == body["engagement_id"]


@pytest.mark.asyncio
async def test_get_graph_200(
    member_client_and_ids: tuple[AsyncClient, UUID, UUID],
) -> None:
    """GET .../graph returns 200 with a GraphSnapshot (nodes + edges)."""
    client, engagement_id, _ = member_client_and_ids

    # Create a node first.
    create_resp = await client.post(
        f"/api/v1/engagements/{engagement_id}/graph/nodes",
        json={"type": "service", "label": "nginx"},
    )
    assert create_resp.status_code == 201

    resp = await client.get(f"/api/v1/engagements/{engagement_id}/graph")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "nodes" in body
    assert "edges" in body
    assert len(body["nodes"]) == 1
    assert body["nodes"][0]["label"] == "nginx"
    assert body["edges"] == []


@pytest.mark.asyncio
async def test_update_node_200(
    member_client_and_ids: tuple[AsyncClient, UUID, UUID],
) -> None:
    """PATCH .../graph/nodes/{node_id} returns 200 with the updated Node."""
    client, engagement_id, _ = member_client_and_ids

    create_resp = await client.post(
        f"/api/v1/engagements/{engagement_id}/graph/nodes",
        json={"type": "host", "label": "old-label"},
    )
    assert create_resp.status_code == 201
    node_id = create_resp.json()["id"]

    resp = await client.patch(
        f"/api/v1/engagements/{engagement_id}/graph/nodes/{node_id}",
        json={"label": "new-label"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["label"] == "new-label"
    assert body["id"] == node_id


@pytest.mark.asyncio
async def test_delete_node_204_and_disappears_from_graph(
    member_client_and_ids: tuple[AsyncClient, UUID, UUID],
) -> None:
    """DELETE .../graph/nodes/{node_id} returns 204; node disappears from GET .../graph."""
    client, engagement_id, _ = member_client_and_ids

    create_resp = await client.post(
        f"/api/v1/engagements/{engagement_id}/graph/nodes",
        json={"type": "host", "label": "deleteme"},
    )
    assert create_resp.status_code == 201
    node_id = create_resp.json()["id"]

    del_resp = await client.delete(f"/api/v1/engagements/{engagement_id}/graph/nodes/{node_id}")
    assert del_resp.status_code == 204, del_resp.text
    assert del_resp.content == b""

    # Node must no longer appear in the live graph.
    graph_resp = await client.get(f"/api/v1/engagements/{engagement_id}/graph")
    assert graph_resp.status_code == 200
    live_ids = [n["id"] for n in graph_resp.json()["nodes"]]
    assert node_id not in live_ids

    # But it should appear in history as deleted.
    hist_resp = await client.get(
        f"/api/v1/engagements/{engagement_id}/graph/history",
        params={"include_deleted": "true"},
    )
    assert hist_resp.status_code == 200
    deleted_ids = [n["id"] for n in hist_resp.json()["deleted_nodes"]]
    assert node_id in deleted_ids


@pytest.mark.asyncio
async def test_undo_node_restores(
    member_client_and_ids: tuple[AsyncClient, UUID, UUID],
) -> None:
    """POST .../graph/nodes/{node_id}/undo restores a soft-deleted node."""
    client, engagement_id, _ = member_client_and_ids

    # Create → delete → undo.
    create_resp = await client.post(
        f"/api/v1/engagements/{engagement_id}/graph/nodes",
        json={"type": "host", "label": "undome"},
    )
    assert create_resp.status_code == 201
    node_id = create_resp.json()["id"]

    del_resp = await client.delete(f"/api/v1/engagements/{engagement_id}/graph/nodes/{node_id}")
    assert del_resp.status_code == 204

    undo_resp = await client.post(f"/api/v1/engagements/{engagement_id}/graph/nodes/{node_id}/undo")
    assert undo_resp.status_code == 200, undo_resp.text
    body = undo_resp.json()
    assert body["id"] == node_id
    assert body["deleted"] is False

    # Node should be back in the live graph.
    graph_resp = await client.get(f"/api/v1/engagements/{engagement_id}/graph")
    live_ids = [n["id"] for n in graph_resp.json()["nodes"]]
    assert node_id in live_ids


@pytest.mark.asyncio
async def test_create_edge_201(
    member_client_and_ids: tuple[AsyncClient, UUID, UUID],
) -> None:
    """POST .../graph/edges returns 201 with the created Edge."""
    client, engagement_id, _ = member_client_and_ids

    src_resp = await client.post(
        f"/api/v1/engagements/{engagement_id}/graph/nodes",
        json={"type": "host", "label": "host-a"},
    )
    assert src_resp.status_code == 201
    tgt_resp = await client.post(
        f"/api/v1/engagements/{engagement_id}/graph/nodes",
        json={"type": "service", "label": "svc-b"},
    )
    assert tgt_resp.status_code == 201

    src_id = src_resp.json()["id"]
    tgt_id = tgt_resp.json()["id"]

    edge_resp = await client.post(
        f"/api/v1/engagements/{engagement_id}/graph/edges",
        json={"source_id": src_id, "target_id": tgt_id, "relation": "runs"},
    )
    assert edge_resp.status_code == 201, edge_resp.text
    body = edge_resp.json()
    assert body["source_id"] == src_id
    assert body["target_id"] == tgt_id
    assert body["relation"] == "runs"
    assert body["deleted"] is False
    assert "id" in body


@pytest.mark.asyncio
async def test_create_duplicate_edge_409(
    member_client_and_ids: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Creating a duplicate live (source, target, relation) triple returns 409."""
    client, engagement_id, _ = member_client_and_ids

    src_resp = await client.post(
        f"/api/v1/engagements/{engagement_id}/graph/nodes",
        json={"type": "host", "label": "dup-host"},
    )
    tgt_resp = await client.post(
        f"/api/v1/engagements/{engagement_id}/graph/nodes",
        json={"type": "service", "label": "dup-svc"},
    )
    src_id = src_resp.json()["id"]
    tgt_id = tgt_resp.json()["id"]

    # First edge — should succeed.
    first = await client.post(
        f"/api/v1/engagements/{engagement_id}/graph/edges",
        json={"source_id": src_id, "target_id": tgt_id, "relation": "runs"},
    )
    assert first.status_code == 201

    # Second identical edge — should be rejected.
    second = await client.post(
        f"/api/v1/engagements/{engagement_id}/graph/edges",
        json={"source_id": src_id, "target_id": tgt_id, "relation": "runs"},
    )
    assert second.status_code == 409, second.text
    body = second.json()
    assert body["error"]["code"] == "conflict"


# ---------------------------------------------------------------------------
# Error-condition tests — service mocked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_unauthenticated_401(anon_app: FastAPI) -> None:
    """POST .../graph/nodes without a session cookie returns 401."""
    eid = uuid4()
    async with AsyncClient(
        transport=ASGITransport(app=anon_app), base_url="https://test"
    ) as client:
        resp = await client.post(
            f"/api/v1/engagements/{eid}/graph/nodes",
            json={"type": "host", "label": "x"},
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_write_non_member_404(
    error_client_and_app: tuple[AsyncClient, FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """A caller who is not a member of the engagement receives 404 (§17.1)."""
    client, _, _ = error_client_and_app
    eid = uuid4()

    with patch(
        "app.features.graph.router.service.create_node",
        new=AsyncMock(side_effect=EngagementNotFound("Engagement not found")),
    ):
        resp = await client.post(
            f"/api/v1/engagements/{eid}/graph/nodes",
            json={"type": "host", "label": "x"},
        )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_write_archived_409(
    error_client_and_app: tuple[AsyncClient, FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """Writing to an archived engagement returns 409 (§4 read-only)."""
    client, _, _ = error_client_and_app
    eid = uuid4()

    with patch(
        "app.features.graph.router.service.create_node",
        new=AsyncMock(side_effect=EngagementArchived("Engagement is archived")),
    ):
        resp = await client.post(
            f"/api/v1/engagements/{eid}/graph/nodes",
            json={"type": "host", "label": "x"},
        )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


@pytest.mark.asyncio
async def test_create_node_bad_type_422(
    error_client_and_app: tuple[AsyncClient, FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """A node body with an invalid ``type`` value returns 422 (Pydantic validation).

    No service mock needed — Pydantic rejects the body before the service is
    called.
    """
    client, _, _ = error_client_and_app
    eid = uuid4()

    resp = await client.post(
        f"/api/v1/engagements/{eid}/graph/nodes",
        json={"type": "invalid_type_xyz", "label": "x"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Personal undo stack (Slice 09)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_undo_stack_200(
    member_client_and_ids: tuple[AsyncClient, UUID, UUID],
) -> None:
    """GET .../graph/undo-stack returns the caller's stack newest-first."""
    client, engagement_id, _ = member_client_and_ids

    await client.post(
        f"/api/v1/engagements/{engagement_id}/graph/nodes",
        json={"type": "host", "label": "first"},
    )
    await client.post(
        f"/api/v1/engagements/{engagement_id}/graph/nodes",
        json={"type": "host", "label": "second"},
    )

    resp = await client.get(f"/api/v1/engagements/{engagement_id}/graph/undo-stack")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["depth"] == 2
    summaries = [e["summary"] for e in body["entries"]]
    assert summaries == ["Created host second", "Created host first"]
    assert all(e["op_type"] == "create_node" for e in body["entries"])
    assert all(e["stale"] is False for e in body["entries"])


@pytest.mark.asyncio
async def test_pop_undo_stack_200(
    member_client_and_ids: tuple[AsyncClient, UUID, UUID],
) -> None:
    """POST .../graph/undo-stack/pop undoes the top write and reflects it in the graph."""
    client, engagement_id, _ = member_client_and_ids

    create = await client.post(
        f"/api/v1/engagements/{engagement_id}/graph/nodes",
        json={"type": "host", "label": "popme"},
    )
    node_id = create.json()["id"]

    resp = await client.post(f"/api/v1/engagements/{engagement_id}/graph/undo-stack/pop")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["undone"] is not None
    assert body["undone"]["op_type"] == "create_node"
    assert body["skipped_stale"] == []
    assert body["stack"]["depth"] == 0

    graph = await client.get(f"/api/v1/engagements/{engagement_id}/graph")
    live_ids = [n["id"] for n in graph.json()["nodes"]]
    assert node_id not in live_ids


@pytest.mark.asyncio
async def test_pop_empty_returns_200_undone_null(
    member_client_and_ids: tuple[AsyncClient, UUID, UUID],
) -> None:
    """Decision 2: popping an empty stack returns 200 with undone=null."""
    client, engagement_id, _ = member_client_and_ids

    resp = await client.post(f"/api/v1/engagements/{engagement_id}/graph/undo-stack/pop")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["undone"] is None
    assert body["stack"]["depth"] == 0


@pytest.mark.asyncio
async def test_pop_archived_409(
    error_client_and_app: tuple[AsyncClient, FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """pop against an archived engagement returns 409."""
    client, _, _ = error_client_and_app
    eid = uuid4()

    with patch(
        "app.features.graph.router.service.pop_undo_stack",
        new=AsyncMock(side_effect=EngagementArchived("Engagement is archived")),
    ):
        resp = await client.post(f"/api/v1/engagements/{eid}/graph/undo-stack/pop")

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


@pytest.mark.asyncio
async def test_undo_stack_non_member_404(
    error_client_and_app: tuple[AsyncClient, FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """A non-member receives 404 on the undo-stack endpoints (§17.1)."""
    client, _, _ = error_client_and_app
    eid = uuid4()

    with patch(
        "app.features.graph.router.service.get_undo_stack",
        new=AsyncMock(side_effect=EngagementNotFound("Engagement not found")),
    ):
        resp = await client.get(f"/api/v1/engagements/{eid}/graph/undo-stack")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_undo_stack_unauthenticated_401(anon_app: FastAPI) -> None:
    """GET .../graph/undo-stack without a session cookie returns 401."""
    eid = uuid4()
    async with AsyncClient(
        transport=ASGITransport(app=anon_app), base_url="https://test"
    ) as client:
        resp = await client.get(f"/api/v1/engagements/{eid}/graph/undo-stack")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_pop_is_user_scoped(
    app_and_factory: tuple[FastAPI, async_sessionmaker[AsyncSession]],
) -> None:
    """User A's pop never touches user B's writes, and vice versa (§8.2 scoping)."""
    app, factory = app_and_factory

    async with factory() as session:
        alice = await auth_repo.create_user(
            session, username="alice", password_hash=_hasher.hash("pw"), role="user"
        )
        bob = await auth_repo.create_user(
            session, username="bob", password_hash=_hasher.hash("pw"), role="user"
        )
        await session.commit()
        await session.refresh(alice)
        await session.refresh(bob)
        alice_id: UUID = alice.id  # type: ignore[assignment]
        bob_id: UUID = bob.id  # type: ignore[assignment]

    async with factory() as session:
        engagement = await eng_repo.create_engagement(
            session, name="Shared", scope="x", client_info=None, owner_id=alice_id
        )
        await session.commit()
        await session.refresh(engagement)
        eid: UUID = engagement.id  # type: ignore[assignment]

    async with factory() as session:
        await eng_repo.add_member(session, eid, bob_id)
        await session.commit()

    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as ca,
        AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as cb,
    ):
        assert (
            await ca.post("/api/v1/auth/login", json={"username": "alice", "password": "pw"})
        ).status_code == 200
        assert (
            await cb.post("/api/v1/auth/login", json={"username": "bob", "password": "pw"})
        ).status_code == 200

        # Alice creates a node.
        create = await ca.post(
            f"/api/v1/engagements/{eid}/graph/nodes",
            json={"type": "host", "label": "alice-host"},
        )
        assert create.status_code == 201
        node_id = create.json()["id"]

        # Bob's stack is empty; his pop undoes nothing and leaves Alice's node intact.
        bob_stack = await cb.get(f"/api/v1/engagements/{eid}/graph/undo-stack")
        assert bob_stack.json()["depth"] == 0
        bob_pop = await cb.post(f"/api/v1/engagements/{eid}/graph/undo-stack/pop")
        assert bob_pop.status_code == 200
        assert bob_pop.json()["undone"] is None

        graph = await ca.get(f"/api/v1/engagements/{eid}/graph")
        assert node_id in [n["id"] for n in graph.json()["nodes"]]

        # Alice's pop undoes her own write.
        alice_pop = await ca.post(f"/api/v1/engagements/{eid}/graph/undo-stack/pop")
        assert alice_pop.json()["undone"] is not None
        graph2 = await ca.get(f"/api/v1/engagements/{eid}/graph")
        assert node_id not in [n["id"] for n in graph2.json()["nodes"]]
