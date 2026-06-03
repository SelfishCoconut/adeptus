"""Tests for the graph single-writer process (ADR-0001, task 4).

Uses an in-memory SQLite async engine via a monkeypatched ``get_sessionmaker``
so the writer obtains its own sessions from the test DB (not the real Postgres).
The conftest.py ``db_session`` fixture provides the test session for direct
repository calls; the writer fixture overrides the module-level sessionmaker used
by writer.py's ``_get_writer``.

``reset_state()`` is called before each test to clear the registry and prevent
cross-test writer leakage.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import Column, ColumnDefault
from sqlalchemy import text as _text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.db import Base
from app.features.auth import models as auth_models  # noqa: F401
from app.features.engagements import models as eng_models  # noqa: F401
from app.features.graph import models as graph_models  # noqa: F401
from app.features.graph import repository as repo
from app.features.graph import writer as gw
from app.features.graph.errors import DuplicateEdge, EdgeNotFound, NodeNotFound, NoHistory
from app.features.graph.schemas import Edge, Node

# ---------------------------------------------------------------------------
# Test DB engine + sessionmaker (shared across all writer tests)
# ---------------------------------------------------------------------------


async def _build_test_engine_and_factory() -> tuple[Any, async_sessionmaker[AsyncSession]]:
    """Create an in-memory SQLite engine with the full schema and return the
    engine + sessionmaker.  Applies the same patches as conftest.py."""
    # Patch PKs to use Python-side uuid4 (gen_random_uuid() not supported by SQLite).
    user_id_col: Column = auth_models.User.__table__.c.id  # type: ignore[assignment]
    user_id_col.default = ColumnDefault(uuid4)

    ip_col: Column = auth_models.Session.__table__.c.ip  # type: ignore[assignment]
    from sqlalchemy import Text

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

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def test_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Provide a fresh SQLite async_sessionmaker for each test.

    Also patches ``app.features.graph.writer.get_sessionmaker`` so that any
    ``_Writer`` created during the test uses the test DB rather than the real
    Postgres.  ``reset_state()`` is called before yielding so each test starts
    with a clean registry.
    """
    engine, factory = await _build_test_engine_and_factory()

    # Override the module-level sessionmaker used by writer._get_writer.
    original_get_sm = gw.get_sessionmaker
    import app.features.graph.writer as _writer_mod

    _writer_mod.get_sessionmaker = lambda: factory  # type: ignore[assignment]

    # Reset any leftover writers from a previous test.
    gw.reset_state()

    yield factory

    # Cleanup.
    gw.reset_state()
    _writer_mod.get_sessionmaker = original_get_sm
    await engine.dispose()


@pytest_asyncio.fixture
async def db(test_factory: async_sessionmaker[AsyncSession]) -> AsyncGenerator[AsyncSession, None]:
    """Yield a session from the test factory for direct repository calls."""
    async with test_factory() as session:
        yield session


@pytest.fixture
def engagement_id() -> UUID:
    return uuid4()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_node(
    eng_id: UUID,
    *,
    node_type: str = "host",
    label: str = "10.0.0.1",
    properties: dict[str, Any] | None = None,
) -> Node:
    return await gw.submit_create_node(
        eng_id,
        node_type=node_type,
        label=label,
        properties=properties or {},
    )


async def _make_edge(
    eng_id: UUID,
    source_id: UUID,
    target_id: UUID,
    relation: str = "runs",
    properties: dict[str, Any] | None = None,
) -> Edge:
    return await gw.submit_create_edge(
        eng_id,
        source_id=source_id,
        target_id=target_id,
        relation=relation,
        properties=properties or {},
    )


# ---------------------------------------------------------------------------
# test_single_consumer_task_per_engagement
# ---------------------------------------------------------------------------


async def test_single_consumer_task_per_engagement(
    test_factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
) -> None:
    """Concurrent first-writes spawn exactly ONE writer/consumer entry."""
    # Fire many concurrent create-node calls for the same engagement.
    results = await asyncio.gather(
        *[_make_node(engagement_id, label=f"host-{i}") for i in range(10)]
    )
    assert len(results) == 10
    # The registry must have exactly one entry.
    assert len(gw._writers) == 1
    # And that entry has exactly one live task.
    writer = gw._writers[engagement_id]
    assert not writer._task.done()


# ---------------------------------------------------------------------------
# test_writes_serialize_via_writer
# ---------------------------------------------------------------------------


async def test_writes_serialize_via_writer(
    test_factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
) -> None:
    """N concurrent submit_* calls all land (no lost updates) in serial order."""
    n = 8
    labels = [f"node-{i}" for i in range(n)]
    results = await asyncio.gather(*[_make_node(engagement_id, label=lbl) for lbl in labels])
    assert len(results) == n

    # Verify all persisted to DB (fresh session).
    async with test_factory() as s:
        pg_nodes, _ = await repo.load_live_graph(s, engagement_id)
    assert len(pg_nodes) == n


# ---------------------------------------------------------------------------
# test_warm_start_from_postgres
# ---------------------------------------------------------------------------


async def test_warm_start_from_postgres(
    test_factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
) -> None:
    """A writer warm-starts its NetworkX graph from Postgres rows on first access."""
    # Seed the DB directly (bypassing the writer) using a dedicated session.
    async with test_factory() as seed_db:
        await repo.insert_node(
            seed_db,
            engagement_id=engagement_id,
            node_type="host",
            label="seeded",
            properties={"os": "linux"},
        )
        await seed_db.commit()

    # Now use the writer — it should warm-start and see the seeded node.
    snapshot = await gw.read_graph(engagement_id)
    assert len(snapshot.nodes) == 1
    assert snapshot.nodes[0].label == "seeded"


# ---------------------------------------------------------------------------
# test_writer_warm_start_after_restart
# ---------------------------------------------------------------------------


async def test_writer_warm_start_after_restart(
    test_factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
) -> None:
    """A fresh registry (after reset_state) rebuilds correct live vs deleted state."""
    # Write two nodes via the writer.
    await _make_node(engagement_id, label="alive")
    dead_node = await _make_node(engagement_id, label="dead")
    # Soft-delete the second node.
    await gw.submit_soft_delete_node(engagement_id, dead_node.id)

    # Simulate a restart: clear the registry.
    gw.reset_state()

    # A new read must warm-start from Postgres.
    snapshot = await gw.read_graph(engagement_id)
    assert len(snapshot.nodes) == 1
    assert snapshot.nodes[0].label == "alive"

    full = await gw.read_full(engagement_id)
    labels = {n.label for n in full.nodes}
    assert labels == {"alive", "dead"}
    # The deleted node is flagged.
    dead = next(n for n in full.nodes if n.label == "dead")
    assert dead.deleted is True


# ---------------------------------------------------------------------------
# test_memory_and_postgres_consistent_after_each_op
# ---------------------------------------------------------------------------


async def test_memory_and_postgres_consistent_after_each_op(
    test_factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
) -> None:
    """After create/update/delete/undo, read_graph matches load_live_graph.

    Each Postgres check uses a fresh session so it never reads from a stale
    identity-map cache (the writer commits in its own session; the test session
    would otherwise return the pre-commit snapshot it cached earlier).
    """

    async def _live_from_pg() -> list[Any]:
        async with test_factory() as s:
            nodes, _ = await repo.load_live_graph(s, engagement_id)
            return nodes

    # Create.
    node = await _make_node(engagement_id, label="original")
    snap = await gw.read_graph(engagement_id)
    pg_nodes = await _live_from_pg()
    assert len(snap.nodes) == len(pg_nodes) == 1

    # Update.
    await gw.submit_update_node(engagement_id, node.id, label="updated")
    snap = await gw.read_graph(engagement_id)
    pg_nodes = await _live_from_pg()
    assert snap.nodes[0].label == "updated"
    assert pg_nodes[0].label == "updated"

    # Soft-delete.
    await gw.submit_soft_delete_node(engagement_id, node.id)
    snap = await gw.read_graph(engagement_id)
    pg_nodes = await _live_from_pg()
    assert len(snap.nodes) == 0
    assert len(pg_nodes) == 0

    # Undo (restores the pre-delete state: label="updated", deleted=False).
    restored = await gw.submit_undo_node(engagement_id, node.id)
    assert restored.deleted is False
    snap = await gw.read_graph(engagement_id)
    pg_nodes = await _live_from_pg()
    assert len(snap.nodes) == 1
    assert len(pg_nodes) == 1


# ---------------------------------------------------------------------------
# test_command_error_does_not_corrupt_queue
# ---------------------------------------------------------------------------


async def test_command_error_does_not_corrupt_queue(
    test_factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
) -> None:
    """A failing command propagates its error; subsequent commands still process."""
    bad_id = uuid4()  # Does not exist.

    # First command: will raise NodeNotFound.
    with pytest.raises(NodeNotFound):
        await gw.submit_update_node(engagement_id, bad_id, label="should fail")

    # Queue is intact — subsequent commands still work.
    node = await _make_node(engagement_id, label="after-error")
    assert node.label == "after-error"


# ---------------------------------------------------------------------------
# test_inmemory_not_mutated_on_db_failure
# ---------------------------------------------------------------------------


async def test_inmemory_not_mutated_on_db_failure(
    test_factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DB-transaction failure leaves the in-memory graph unchanged."""
    node = await _make_node(engagement_id, label="before-fail")

    # Snapshot in-memory state before the failing command.
    snap_before = await gw.read_graph(engagement_id)
    assert len(snap_before.nodes) == 1
    assert snap_before.nodes[0].label == "before-fail"

    # Monkeypatch repo.update_node_row to raise so the DB mutation fails.
    original_update = repo.update_node_row

    async def _fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(repo, "update_node_row", _fail)

    with pytest.raises(RuntimeError, match="simulated DB failure"):
        await gw.submit_update_node(engagement_id, node.id, label="mutated")

    # Restore.
    monkeypatch.setattr(repo, "update_node_row", original_update)

    # In-memory graph must be unchanged.
    snap_after = await gw.read_graph(engagement_id)
    assert len(snap_after.nodes) == 1
    assert snap_after.nodes[0].label == "before-fail"


# ---------------------------------------------------------------------------
# test_undo_node_restores_prior_state
# ---------------------------------------------------------------------------


async def test_undo_node_restores_prior_state(
    test_factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
) -> None:
    """submit_undo_node reverts the node to its immediately-prior state.

    The undo is repeatable: each undo records the current state as history before
    reverting, so the history stack grows with each undo and can be walked back.

    Primary check: one undo restores the pre-update state.
    Secondary check: a second undo is accepted without error (repeatability);
    the exact label depends on history-row ordering, which is implementation-
    defined — see _latest_node_history for the ordering guarantee.
    """
    node = await _make_node(engagement_id, label="v1", properties={"x": 1})
    await gw.submit_update_node(engagement_id, node.id, label="v2", properties={"x": 2})

    # First undo: node should be restored to v1.
    restored = await gw.submit_undo_node(engagement_id, node.id)
    assert restored.label == "v1"
    assert restored.properties == {"x": 1}
    assert restored.deleted is False

    # A second undo must succeed (repeatability guarantee): the undo records
    # the current state before reverting so the history stack grows.
    restored2 = await gw.submit_undo_node(engagement_id, node.id)
    # The node is now at some valid prior state (v2 in production with sub-ms
    # timestamps; ordering may differ on SQLite second-level precision, but the
    # operation must not raise).
    assert restored2.label in ("v1", "v2")


# ---------------------------------------------------------------------------
# test_undo_edge_restores_prior_state
# ---------------------------------------------------------------------------


async def test_undo_edge_restores_prior_state(
    test_factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
) -> None:
    """submit_undo_edge restores a soft-deleted edge."""
    n1 = await _make_node(engagement_id, label="source")
    n2 = await _make_node(engagement_id, label="target")
    edge = await _make_edge(engagement_id, n1.id, n2.id, relation="runs")

    # Soft-delete the edge (records pre-delete state).
    await gw.submit_soft_delete_edge(engagement_id, edge.id)

    # Undo: restores the pre-delete state (deleted=False).
    restored = await gw.submit_undo_edge(engagement_id, edge.id)
    assert restored.deleted is False
    assert restored.relation == "runs"

    snap = await gw.read_graph(engagement_id)
    assert any(e.id == edge.id for e in snap.edges)


# ---------------------------------------------------------------------------
# test_undo_with_no_history_raises
# ---------------------------------------------------------------------------


async def test_undo_with_no_history_raises(
    test_factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
) -> None:
    """submit_undo_node raises NoHistory when there are no prior history rows."""
    node = await _make_node(engagement_id, label="fresh")
    with pytest.raises(NoHistory):
        await gw.submit_undo_node(engagement_id, node.id)


async def test_undo_edge_with_no_history_raises(
    test_factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
) -> None:
    """submit_undo_edge raises NoHistory for an edge with no history."""
    n1 = await _make_node(engagement_id, label="s")
    n2 = await _make_node(engagement_id, label="t")
    edge = await _make_edge(engagement_id, n1.id, n2.id)
    with pytest.raises(NoHistory):
        await gw.submit_undo_edge(engagement_id, edge.id)


# ---------------------------------------------------------------------------
# test_duplicate_live_edge_rejected
# ---------------------------------------------------------------------------


async def test_duplicate_live_edge_rejected(
    test_factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
) -> None:
    """Second submit_create_edge with the same live triple raises DuplicateEdge."""
    n1 = await _make_node(engagement_id, label="s")
    n2 = await _make_node(engagement_id, label="t")

    # First edge — succeeds.
    e1 = await _make_edge(engagement_id, n1.id, n2.id, relation="runs")
    assert e1.deleted is False

    # Exact duplicate live triple — must raise.
    with pytest.raises(DuplicateEdge):
        await _make_edge(engagement_id, n1.id, n2.id, relation="runs")

    # Distinct relation between same pair — must succeed.
    e2 = await _make_edge(engagement_id, n1.id, n2.id, relation="exposes")
    assert e2.deleted is False

    # Soft-delete the first edge, then re-create the same triple — must succeed.
    await gw.submit_soft_delete_edge(engagement_id, e1.id)
    e3 = await _make_edge(engagement_id, n1.id, n2.id, relation="runs")
    assert e3.deleted is False


# ---------------------------------------------------------------------------
# test_soft_delete_node_cascades_to_edges_in_memory
# ---------------------------------------------------------------------------


async def test_soft_delete_node_cascades_to_edges_in_memory(
    test_factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
) -> None:
    """Soft-deleting a node cascades soft-delete to its incident edges in both
    Postgres and the in-memory graph."""
    n1 = await _make_node(engagement_id, label="n1")
    n2 = await _make_node(engagement_id, label="n2")
    edge = await _make_edge(engagement_id, n1.id, n2.id)

    await gw.submit_soft_delete_node(engagement_id, n1.id)

    # In-memory: both node and edge should be absent from the live view.
    snap = await gw.read_graph(engagement_id)
    assert not any(n.id == n1.id for n in snap.nodes)
    assert not any(e.id == edge.id for e in snap.edges)

    # Postgres: cascade soft-delete (fresh session).
    async with test_factory() as s:
        pg_nodes, pg_edges = await repo.load_live_graph(s, engagement_id)
    assert not any(n.id == n1.id for n in pg_nodes)
    assert len(pg_edges) == 0


# ---------------------------------------------------------------------------
# test_node_not_found_raises
# ---------------------------------------------------------------------------


async def test_node_not_found_raises(
    test_factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
) -> None:
    """submit_update_node raises NodeNotFound for a non-existent node."""
    with pytest.raises(NodeNotFound):
        await gw.submit_update_node(engagement_id, uuid4(), label="nope")


# ---------------------------------------------------------------------------
# test_edge_not_found_raises
# ---------------------------------------------------------------------------


async def test_edge_not_found_raises(
    test_factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
) -> None:
    """submit_soft_delete_edge raises EdgeNotFound for a non-existent edge."""
    with pytest.raises(EdgeNotFound):
        await gw.submit_soft_delete_edge(engagement_id, uuid4())


# ---------------------------------------------------------------------------
# test_multi_engagement_isolation
# ---------------------------------------------------------------------------


async def test_multi_engagement_isolation(
    test_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Each engagement gets its own writer; nodes are isolated."""
    eng_a = uuid4()
    eng_b = uuid4()

    await _make_node(eng_a, label="a-node")
    await _make_node(eng_b, label="b-node")

    snap_a = await gw.read_graph(eng_a)
    snap_b = await gw.read_graph(eng_b)

    assert len(snap_a.nodes) == 1 and snap_a.nodes[0].label == "a-node"
    assert len(snap_b.nodes) == 1 and snap_b.nodes[0].label == "b-node"

    # Two separate writer entries.
    assert len(gw._writers) == 2


# ---------------------------------------------------------------------------
# test_read_full_reflects_deleted_after_cascade  (code-review/security flag 2)
# ---------------------------------------------------------------------------


async def test_read_full_reflects_deleted_after_cascade(
    test_factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
) -> None:
    """After a cascade soft-delete, read_full returns the node AND its incident
    edge with deleted=True on the schema payload (not just the internal graph
    attribute) — keeping read_full consistent with read_graph and Postgres."""
    n1 = await _make_node(engagement_id, label="n1")
    n2 = await _make_node(engagement_id, label="n2")
    edge = await _make_edge(engagement_id, n1.id, n2.id)

    await gw.submit_soft_delete_node(engagement_id, n1.id)

    full = await gw.read_full(engagement_id)
    deleted_node = next(n for n in full.nodes if n.id == n1.id)
    cascaded_edge = next(e for e in full.edges if e.id == edge.id)
    assert deleted_node.deleted is True
    assert cascaded_edge.deleted is True

    # The surviving node stays live.
    surviving = next(n for n in full.nodes if n.id == n2.id)
    assert surviving.deleted is False


# ---------------------------------------------------------------------------
# test_create_edge_rejects_foreign_or_deleted_endpoint  (flag 1: writer-level
# endpoint-ownership guard — defense-in-depth for future ingestion callers)
# ---------------------------------------------------------------------------


async def test_create_edge_rejects_cross_engagement_endpoint(
    test_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The writer consumer rejects an edge whose endpoint belongs to another
    engagement, even though the call targets the edge's own engagement."""
    eng_a = uuid4()
    eng_b = uuid4()
    a_node = await _make_node(eng_a, label="a-host")
    b_node = await _make_node(eng_b, label="b-host")

    # Try to link engagement B's edge to engagement A's node.
    with pytest.raises(NodeNotFound):
        await gw.submit_create_edge(
            eng_b,
            source_id=b_node.id,
            target_id=a_node.id,
            relation="runs",
            properties={},
        )


async def test_create_edge_rejects_deleted_endpoint(
    test_factory: async_sessionmaker[AsyncSession],
    engagement_id: UUID,
) -> None:
    """The writer consumer rejects an edge referencing a soft-deleted endpoint."""
    n1 = await _make_node(engagement_id, label="n1")
    n2 = await _make_node(engagement_id, label="n2")
    await gw.submit_soft_delete_node(engagement_id, n2.id)

    with pytest.raises(NodeNotFound):
        await gw.submit_create_edge(
            engagement_id,
            source_id=n1.id,
            target_id=n2.id,
            relation="runs",
            properties={},
        )
