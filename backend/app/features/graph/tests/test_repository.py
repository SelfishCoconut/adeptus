"""Repository tests for the graph feature (real async test DB).

All tests use the in-memory SQLite async session provided by conftest.py.
pytest-asyncio is configured with asyncio_mode="auto" so no explicit
@pytest.mark.asyncio decorator is needed.

The partial unique index test (test_partial_unique_index_blocks_duplicate_live_edge)
exercises the ``uq_graph_edges_live_triple`` index.  The conftest patches the index
on SQLite to use the proper WHERE clause so that soft-deleted edges do not block
re-creating the same triple (SQLAlchemy renders ``postgresql_where`` only for Postgres).
"""

import asyncio
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.graph import repository as repo
from app.features.graph.models import GraphEdge, GraphNode, GraphUserUndoStack

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _uid(obj: GraphNode | GraphEdge) -> UUID:
    """Cast a SQLAlchemy UUID column value to plain uuid.UUID."""
    return cast(UUID, obj.id)


async def _make_node(
    db: AsyncSession,
    *,
    engagement_id: UUID | None = None,
    node_type: str = "host",
    label: str = "10.0.0.1",
    properties: dict | None = None,
) -> GraphNode:
    """Helper: insert a GraphNode in the given session."""
    return await repo.insert_node(
        db,
        engagement_id=engagement_id or uuid4(),
        node_type=node_type,
        label=label,
        properties=properties or {},
    )


async def _make_edge(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    source_id: UUID,
    target_id: UUID,
    relation: str = "connects",
    properties: dict | None = None,
) -> GraphEdge:
    """Helper: insert a GraphEdge in the given session."""
    return await repo.insert_edge(
        db,
        engagement_id=engagement_id,
        source_id=source_id,
        target_id=target_id,
        relation=relation,
        properties=properties or {},
    )


# ---------------------------------------------------------------------------
# test_insert_and_load_live_graph
# ---------------------------------------------------------------------------


async def test_insert_and_load_live_graph(db_session: AsyncSession) -> None:
    """Inserting nodes and edges shows up in load_live_graph; deleted items do not."""
    eng_id = uuid4()

    node_a = await _make_node(db_session, engagement_id=eng_id, label="host-A")
    node_b = await _make_node(db_session, engagement_id=eng_id, label="host-B")
    edge = await _make_edge(
        db_session,
        engagement_id=eng_id,
        source_id=_uid(node_a),
        target_id=_uid(node_b),
        relation="connects",
    )

    nodes, edges = await repo.load_live_graph(db_session, eng_id)

    assert len(nodes) == 2
    assert len(edges) == 1
    node_ids = {_uid(n) for n in nodes}
    assert _uid(node_a) in node_ids
    assert _uid(node_b) in node_ids
    assert _uid(edge) == _uid(edges[0])

    # Soft-delete node_b and re-check; only node_a and no edges should appear.
    await repo.soft_delete_node(db_session, node=node_b)
    nodes2, edges2 = await repo.load_live_graph(db_session, eng_id)
    live_ids = {_uid(n) for n in nodes2}
    assert _uid(node_a) in live_ids
    assert _uid(node_b) not in live_ids
    # Cascade deleted the edge too.
    assert edges2 == []


# ---------------------------------------------------------------------------
# test_soft_delete_node_cascades_to_edges
# ---------------------------------------------------------------------------


async def test_soft_delete_node_cascades_to_edges(db_session: AsyncSession) -> None:
    """Soft-deleting a node cascades deleted=True to all live incident edges."""
    eng_id = uuid4()

    hub = await _make_node(db_session, engagement_id=eng_id, label="hub")
    leaf_a = await _make_node(db_session, engagement_id=eng_id, label="leaf-a")
    leaf_b = await _make_node(db_session, engagement_id=eng_id, label="leaf-b")

    # Two edges incident to hub (as source and target).
    edge_out = await _make_edge(
        db_session,
        engagement_id=eng_id,
        source_id=_uid(hub),
        target_id=_uid(leaf_a),
        relation="connects",
    )
    edge_in = await _make_edge(
        db_session,
        engagement_id=eng_id,
        source_id=_uid(leaf_b),
        target_id=_uid(hub),
        relation="reports",
    )
    # Edge between the two leaves — should NOT be affected.
    edge_leaves = await _make_edge(
        db_session,
        engagement_id=eng_id,
        source_id=_uid(leaf_a),
        target_id=_uid(leaf_b),
        relation="peers",
    )

    # Pre-delete: all live.
    nodes, edges = await repo.load_live_graph(db_session, eng_id)
    assert len(nodes) == 3
    assert len(edges) == 3

    await repo.soft_delete_node(db_session, node=hub)

    # Reload from DB to check cascade.
    edge_out_fresh = await repo.get_edge(db_session, _uid(edge_out))
    edge_in_fresh = await repo.get_edge(db_session, _uid(edge_in))
    edge_leaves_fresh = await repo.get_edge(db_session, _uid(edge_leaves))

    assert edge_out_fresh is not None and edge_out_fresh.deleted is True
    assert edge_in_fresh is not None and edge_in_fresh.deleted is True
    # Non-incident edge is unaffected.
    assert edge_leaves_fresh is not None and edge_leaves_fresh.deleted is False

    # Live graph shows only the two leaves and the leaf edge.
    nodes2, edges2 = await repo.load_live_graph(db_session, eng_id)
    live_node_ids = {_uid(n) for n in nodes2}
    assert _uid(hub) not in live_node_ids
    assert _uid(leaf_a) in live_node_ids
    assert _uid(leaf_b) in live_node_ids
    assert len(edges2) == 1
    assert _uid(edges2[0]) == _uid(edge_leaves)


# ---------------------------------------------------------------------------
# test_history_records_prestate
# ---------------------------------------------------------------------------


async def test_history_records_prestate(db_session: AsyncSession) -> None:
    """record_node_history captures the pre-mutation state; update changes the live row."""
    eng_id = uuid4()

    node = await _make_node(
        db_session,
        engagement_id=eng_id,
        label="original-label",
        properties={"os": "linux"},
    )

    # Record history BEFORE the mutation (captures original state).
    history = await repo.record_node_history(db_session, node=node)

    # Mutate the node.
    await repo.update_node_row(db_session, node=node, label="new-label", properties={"os": "win"})

    # History row preserves the original state.
    assert history.label == "original-label"
    assert history.properties == {"os": "linux"}
    assert history.deleted is False
    assert history.node_id == _uid(node)
    assert history.engagement_id == eng_id

    # Live node reflects the new state.
    live = await repo.get_node(db_session, _uid(node))
    assert live is not None
    assert live.label == "new-label"
    assert live.properties == {"os": "win"}

    # Edge history parallel test.
    node_b = await _make_node(db_session, engagement_id=eng_id, label="b")
    edge = await _make_edge(
        db_session,
        engagement_id=eng_id,
        source_id=_uid(node),
        target_id=_uid(node_b),
        relation="original-rel",
    )
    edge_hist = await repo.record_edge_history(db_session, edge=edge)
    assert edge_hist.relation == "original-rel"
    assert edge_hist.deleted is False
    assert edge_hist.edge_id == _uid(edge)


# ---------------------------------------------------------------------------
# test_latest_node_history_returns_most_recent
# ---------------------------------------------------------------------------


async def test_latest_node_history_returns_most_recent(db_session: AsyncSession) -> None:
    """latest_node_history returns the most-recently-inserted history row."""
    eng_id = uuid4()

    node = await _make_node(db_session, engagement_id=eng_id, label="v0")

    # No history yet → None.
    assert await repo.latest_node_history(db_session, _uid(node)) is None

    # Record two history snapshots in sequence.  A brief sleep ensures distinct
    # recorded_at values on SQLite (DATETIME DEFAULT CURRENT_TIMESTAMP has 1-second
    # granularity; without the sleep two rapidly-inserted rows share the same timestamp
    # and ordering becomes non-deterministic).
    h1 = await repo.record_node_history(db_session, node=node)
    await repo.update_node_row(db_session, node=node, label="v1", properties={})

    await asyncio.sleep(1.01)

    h2 = await repo.record_node_history(db_session, node=node)
    await repo.update_node_row(db_session, node=node, label="v2", properties={})

    latest = await repo.latest_node_history(db_session, _uid(node))
    assert latest is not None
    # h2 was recorded after h1 so it should be the latest.
    assert latest.id == h2.id
    assert latest.label == "v1"  # pre-v2-mutation state

    # Sanity: h1 captured the pre-v1-mutation state.
    assert h1.label == "v0"

    # Parallel check for edges.
    node_b = await _make_node(db_session, engagement_id=eng_id, label="b")
    edge = await _make_edge(
        db_session,
        engagement_id=eng_id,
        source_id=_uid(node),
        target_id=_uid(node_b),
        relation="rel-v0",
    )

    assert await repo.latest_edge_history(db_session, _uid(edge)) is None

    eh1 = await repo.record_edge_history(db_session, edge=edge)
    # Simulate a mutation (just record a second history with a fresh label)
    edge.relation = "rel-v1"
    await db_session.flush()

    await asyncio.sleep(1.01)

    eh2 = await repo.record_edge_history(db_session, edge=edge)

    latest_e = await repo.latest_edge_history(db_session, _uid(edge))
    assert latest_e is not None
    assert latest_e.id == eh2.id
    assert eh1.relation == "rel-v0"
    assert eh2.relation == "rel-v1"


# ---------------------------------------------------------------------------
# test_partial_unique_index_blocks_duplicate_live_edge
# ---------------------------------------------------------------------------


async def test_partial_unique_index_blocks_duplicate_live_edge(db_session: AsyncSession) -> None:
    """Inserting two live edges with the same (engagement, source, target, relation)
    raises IntegrityError due to the partial unique index.
    """
    eng_id = uuid4()

    src = await _make_node(db_session, engagement_id=eng_id, label="src")
    tgt = await _make_node(db_session, engagement_id=eng_id, label="tgt")

    await _make_edge(
        db_session,
        engagement_id=eng_id,
        source_id=_uid(src),
        target_id=_uid(tgt),
        relation="runs",
    )

    # A second live edge with the same triple must be rejected.
    with pytest.raises(IntegrityError):
        await _make_edge(
            db_session,
            engagement_id=eng_id,
            source_id=_uid(src),
            target_id=_uid(tgt),
            relation="runs",
        )


# ---------------------------------------------------------------------------
# test_duplicate_triple_allowed_after_soft_delete
# ---------------------------------------------------------------------------


async def test_duplicate_triple_allowed_after_soft_delete(db_session: AsyncSession) -> None:
    """Soft-deleting the first edge then inserting the same triple succeeds."""
    eng_id = uuid4()

    src = await _make_node(db_session, engagement_id=eng_id, label="src")
    tgt = await _make_node(db_session, engagement_id=eng_id, label="tgt")

    edge1 = await _make_edge(
        db_session,
        engagement_id=eng_id,
        source_id=_uid(src),
        target_id=_uid(tgt),
        relation="runs",
    )

    # Soft-delete the first edge.
    await repo.soft_delete_edge(db_session, edge=edge1)

    # Re-create the same triple — must succeed now the first is deleted.
    edge2 = await _make_edge(
        db_session,
        engagement_id=eng_id,
        source_id=_uid(src),
        target_id=_uid(tgt),
        relation="runs",
    )

    assert _uid(edge2) != _uid(edge1)
    assert edge2.deleted is False

    # Only one live edge exists.
    live_edge = await repo.find_live_edge(db_session, eng_id, _uid(src), _uid(tgt), "runs")
    assert live_edge is not None
    assert _uid(live_edge) == _uid(edge2)


# ---------------------------------------------------------------------------
# test_find_live_edge_returns_match_and_none
# ---------------------------------------------------------------------------


async def test_find_live_edge_returns_match_and_none(db_session: AsyncSession) -> None:
    """find_live_edge returns the matching live edge or None when absent/deleted."""
    eng_id = uuid4()

    src = await _make_node(db_session, engagement_id=eng_id, label="src")
    tgt = await _make_node(db_session, engagement_id=eng_id, label="tgt")

    # No edge yet → None.
    assert await repo.find_live_edge(db_session, eng_id, _uid(src), _uid(tgt), "runs") is None

    edge = await _make_edge(
        db_session,
        engagement_id=eng_id,
        source_id=_uid(src),
        target_id=_uid(tgt),
        relation="runs",
    )

    # Live edge found.
    found = await repo.find_live_edge(db_session, eng_id, _uid(src), _uid(tgt), "runs")
    assert found is not None
    assert _uid(found) == _uid(edge)

    # A different relation between the same pair → None.
    assert await repo.find_live_edge(db_session, eng_id, _uid(src), _uid(tgt), "hosts") is None

    # After soft-delete → None.
    await repo.soft_delete_edge(db_session, edge=edge)
    assert await repo.find_live_edge(db_session, eng_id, _uid(src), _uid(tgt), "runs") is None


# ---------------------------------------------------------------------------
# test_load_full_graph_includes_deleted
# ---------------------------------------------------------------------------


async def test_load_full_graph_includes_deleted(db_session: AsyncSession) -> None:
    """load_full_graph returns deleted nodes and edges (for writer warm-start)."""
    eng_id = uuid4()

    node_a = await _make_node(db_session, engagement_id=eng_id, label="a")
    node_b = await _make_node(db_session, engagement_id=eng_id, label="b")
    edge = await _make_edge(
        db_session,
        engagement_id=eng_id,
        source_id=_uid(node_a),
        target_id=_uid(node_b),
        relation="link",
    )

    # Soft-delete both node and edge.
    await repo.soft_delete_edge(db_session, edge=edge)
    await repo.soft_delete_node(db_session, node=node_a)

    # load_live_graph excludes deleted items.
    live_nodes, live_edges = await repo.load_live_graph(db_session, eng_id)
    assert len(live_nodes) == 1  # node_b only
    assert len(live_edges) == 0

    # load_full_graph includes everything.
    all_nodes, all_edges = await repo.load_full_graph(db_session, eng_id)
    assert len(all_nodes) == 2
    assert len(all_edges) == 1
    # Deleted flags are preserved.
    by_id = {_uid(n): n for n in all_nodes}
    assert by_id[_uid(node_a)].deleted is True
    assert by_id[_uid(node_b)].deleted is False


# ---------------------------------------------------------------------------
# test_get_node_and_get_edge
# ---------------------------------------------------------------------------


async def test_get_node_and_get_edge(db_session: AsyncSession) -> None:
    """get_node / get_edge return the row or None."""
    eng_id = uuid4()

    node = await _make_node(db_session, engagement_id=eng_id, label="probe")
    fetched_node = await repo.get_node(db_session, _uid(node))
    assert fetched_node is not None
    assert _uid(fetched_node) == _uid(node)

    # Non-existent id.
    assert await repo.get_node(db_session, uuid4()) is None

    node_b = await _make_node(db_session, engagement_id=eng_id, label="b")
    edge = await _make_edge(
        db_session,
        engagement_id=eng_id,
        source_id=_uid(node),
        target_id=_uid(node_b),
        relation="link",
    )
    fetched_edge = await repo.get_edge(db_session, _uid(edge))
    assert fetched_edge is not None
    assert _uid(fetched_edge) == _uid(edge)

    assert await repo.get_edge(db_session, uuid4()) is None


# ---------------------------------------------------------------------------
# Personal undo stack (Slice 09)
# ---------------------------------------------------------------------------


async def _push(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    user_id: UUID,
    op_type: str = "create_node",
    entity_kind: str = "node",
    entity_id: UUID | None = None,
    summary: str = "Created host 10.0.0.5",
) -> GraphUserUndoStack:
    """Helper: push one personal-undo entry."""
    return await repo.push_undo_entry(
        db,
        engagement_id=engagement_id,
        user_id=user_id,
        op_type=op_type,
        entity_kind=entity_kind,
        entity_id=entity_id or uuid4(),
        target_updated_at=datetime.now(UTC),
        summary=summary,
    )


async def test_push_and_list_active_stack_newest_first(db_session: AsyncSession) -> None:
    """Pushed entries come back active, newest-first."""
    eng_id, user_id = uuid4(), uuid4()

    first = await _push(db_session, engagement_id=eng_id, user_id=user_id, summary="first")
    second = await _push(db_session, engagement_id=eng_id, user_id=user_id, summary="second")
    third = await _push(db_session, engagement_id=eng_id, user_id=user_id, summary="third")

    stack = await repo.list_active_undo_stack(db_session, eng_id, user_id)
    assert [e.summary for e in stack] == ["third", "second", "first"]
    assert {e.id for e in stack} == {first.id, second.id, third.id}

    top = await repo.get_top_active_undo_entry(db_session, eng_id, user_id)
    assert top is not None
    assert top.id == third.id


async def test_push_trims_to_twenty(db_session: AsyncSession) -> None:
    """The active stack never exceeds 20; oldest active rows are trimmed."""
    eng_id, user_id = uuid4(), uuid4()

    for i in range(25):
        await _push(db_session, engagement_id=eng_id, user_id=user_id, summary=f"write-{i}")

    stack = await repo.list_active_undo_stack(db_session, eng_id, user_id)
    assert len(stack) == 20
    # Newest 20 retained (write-24 .. write-5); the 5 oldest dropped.
    assert stack[0].summary == "write-24"
    assert stack[-1].summary == "write-5"


async def test_stack_is_scoped_per_user_and_engagement(db_session: AsyncSession) -> None:
    """A stack lists only the owner's writes in the given engagement."""
    eng_a, eng_b = uuid4(), uuid4()
    user_a, user_b = uuid4(), uuid4()

    await _push(db_session, engagement_id=eng_a, user_id=user_a, summary="A/engA")
    await _push(db_session, engagement_id=eng_a, user_id=user_b, summary="B/engA")
    await _push(db_session, engagement_id=eng_b, user_id=user_a, summary="A/engB")

    a_eng_a = await repo.list_active_undo_stack(db_session, eng_a, user_a)
    assert [e.summary for e in a_eng_a] == ["A/engA"]

    b_eng_a = await repo.list_active_undo_stack(db_session, eng_a, user_b)
    assert [e.summary for e in b_eng_a] == ["B/engA"]

    a_eng_b = await repo.list_active_undo_stack(db_session, eng_b, user_a)
    assert [e.summary for e in a_eng_b] == ["A/engB"]


async def test_mark_undone_removes_from_active_stack(db_session: AsyncSession) -> None:
    """Marking an entry undone removes it from the active stack but keeps the row."""
    eng_id, user_id = uuid4(), uuid4()

    await _push(db_session, engagement_id=eng_id, user_id=user_id, summary="keep")
    top_entry = await _push(db_session, engagement_id=eng_id, user_id=user_id, summary="pop-me")

    top = await repo.get_top_active_undo_entry(db_session, eng_id, user_id)
    assert top is not None and top.id == top_entry.id

    await repo.mark_undo_entry_undone(db_session, top)

    remaining = await repo.list_active_undo_stack(db_session, eng_id, user_id)
    assert [e.summary for e in remaining] == ["keep"]
    # Row is retained (undone=true), not deleted.
    assert top.undone is True
