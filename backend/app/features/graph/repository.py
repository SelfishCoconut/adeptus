"""Database access for the graph feature: async Postgres CRUD for nodes, edges,
and their history tables (task 3).

All functions are module-level async, accept an AsyncSession first, and follow
the project pattern: select()/execute() for reads, flush()+refresh() for
server-generated defaults, cast() where mypy needs it.

Functions:
- insert_node          — insert a GraphNode, flush, refresh, return.
- update_node_row      — apply new label/properties to an existing GraphNode row.
- soft_delete_node     — set deleted=True on node AND cascade to live incident edges.
- insert_edge          — insert a GraphEdge, flush, refresh, return.
- soft_delete_edge     — set deleted=True on an edge.
- load_live_graph      — non-deleted nodes + edges for an engagement.
- load_full_graph      — ALL nodes + edges (incl. deleted) for writer warm-start.
- record_node_history  — append a pre-mutation snapshot of a GraphNode.
- record_edge_history  — append a pre-mutation snapshot of a GraphEdge.
- latest_node_history  — most recent GraphNodeHistory row by recorded_at DESC.
- latest_edge_history  — most recent GraphEdgeHistory row by recorded_at DESC.
- get_node             — fetch GraphNode by id or None.
- get_edge             — fetch GraphEdge by id or None.
- find_live_edge       — duplicate-triple lookup for the uniqueness guard.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import and_, desc, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.graph.models import GraphEdge, GraphEdgeHistory, GraphNode, GraphNodeHistory

# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def insert_node(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    node_type: str,
    label: str,
    properties: dict[str, Any],
) -> GraphNode:
    """Insert a new GraphNode row.  flush()+refresh() populate server-generated defaults
    (id, created_at, updated_at, deleted) before returning.

    The ``node_type`` parameter corresponds to the ``type`` column; the argument is
    named ``node_type`` to avoid shadowing the Python ``type`` builtin.
    The caller is responsible for committing the transaction.
    """
    node = GraphNode(
        engagement_id=engagement_id,
        type=node_type,
        label=label,
        properties=properties,
    )
    db.add(node)
    await db.flush()
    await db.refresh(node)
    return node


async def update_node_row(
    db: AsyncSession,
    *,
    node: GraphNode,
    label: str,
    properties: dict[str, Any],
) -> GraphNode:
    """Apply new label and properties to an existing GraphNode instance.

    The caller passes the fully-resolved values; no merging of partial updates
    is done here — that is the writer's responsibility.  Does NOT record history;
    call record_node_history() before this function to capture the pre-state.

    Returns the updated row after flush+refresh so server-generated updated_at
    is visible.  The caller is responsible for committing.
    """
    node.label = label
    node.properties = properties
    await db.flush()
    await db.refresh(node)
    return node


async def soft_delete_node(
    db: AsyncSession,
    *,
    node: GraphNode,
) -> None:
    """Soft-delete a node and cascade soft-delete to all its live incident edges.

    Cascade logic: any GraphEdge whose source_id == node.id OR target_id == node.id
    AND deleted == False is also marked deleted=True in the same flush.

    Does NOT record history for the node or edges — the writer records pre-state
    snapshots before calling this function.  The caller is responsible for committing.
    """
    node.deleted = True

    # Cascade: mark all live incident edges deleted.
    await db.execute(
        update(GraphEdge)
        .where(
            and_(
                or_(GraphEdge.source_id == node.id, GraphEdge.target_id == node.id),
                GraphEdge.deleted.is_(False),
            )
        )
        .values(deleted=True)
    )
    await db.flush()


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


async def insert_edge(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    source_id: UUID,
    target_id: UUID,
    relation: str,
    properties: dict[str, Any],
) -> GraphEdge:
    """Insert a new GraphEdge row.  flush()+refresh() populate server-generated
    defaults before returning.  The caller is responsible for committing.
    """
    edge = GraphEdge(
        engagement_id=engagement_id,
        source_id=source_id,
        target_id=target_id,
        relation=relation,
        properties=properties,
    )
    db.add(edge)
    await db.flush()
    await db.refresh(edge)
    return edge


async def soft_delete_edge(
    db: AsyncSession,
    *,
    edge: GraphEdge,
) -> None:
    """Soft-delete a single edge by setting deleted=True.

    Does NOT record history — the writer records the pre-state snapshot before
    calling this.  The caller is responsible for committing.
    """
    edge.deleted = True
    await db.flush()


# ---------------------------------------------------------------------------
# Graph reads
# ---------------------------------------------------------------------------


async def load_live_graph(
    db: AsyncSession,
    engagement_id: UUID,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Return all non-deleted nodes and non-deleted edges for an engagement.

    Used by the router GET /graph endpoint (can also be served from in-memory
    writer state; this is the Postgres-backed fallback path and the source for
    writer warm-start consistency checks).
    """
    node_result = await db.execute(
        select(GraphNode).where(
            GraphNode.engagement_id == engagement_id,
            GraphNode.deleted.is_(False),
        )
    )
    nodes = list(node_result.scalars().all())

    edge_result = await db.execute(
        select(GraphEdge).where(
            GraphEdge.engagement_id == engagement_id,
            GraphEdge.deleted.is_(False),
        )
    )
    edges = list(edge_result.scalars().all())

    return nodes, edges


async def load_full_graph(
    db: AsyncSession,
    engagement_id: UUID,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Return ALL nodes and edges for an engagement, including soft-deleted ones.

    Used by the writer warm-start: after a process restart the in-memory
    NetworkX graph is rebuilt from this full snapshot (including deleted nodes
    and edges) so that undo operations can find prior state and so the in-memory
    graph faithfully mirrors Postgres.

    Unlike load_live_graph, no deleted filter is applied.
    """
    node_result = await db.execute(
        select(GraphNode).where(GraphNode.engagement_id == engagement_id)
    )
    nodes = list(node_result.scalars().all())

    edge_result = await db.execute(
        select(GraphEdge).where(GraphEdge.engagement_id == engagement_id)
    )
    edges = list(edge_result.scalars().all())

    return nodes, edges


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


async def record_node_history(
    db: AsyncSession,
    *,
    node: GraphNode,
) -> GraphNodeHistory:
    """Append a GraphNodeHistory row capturing the node's CURRENT state.

    Call this BEFORE mutating the node so the history row captures the
    pre-mutation state that undo will restore.  flush()+refresh() populate the
    server-generated id and recorded_at.  The caller is responsible for committing.
    """
    history = GraphNodeHistory(
        engagement_id=node.engagement_id,
        node_id=node.id,
        label=node.label,
        properties=node.properties,
        deleted=node.deleted,
    )
    db.add(history)
    await db.flush()
    await db.refresh(history)
    return history


async def record_edge_history(
    db: AsyncSession,
    *,
    edge: GraphEdge,
) -> GraphEdgeHistory:
    """Append a GraphEdgeHistory row capturing the edge's CURRENT state.

    Call this BEFORE mutating the edge.  flush()+refresh() populate the
    server-generated id and recorded_at.  The caller is responsible for committing.
    """
    history = GraphEdgeHistory(
        engagement_id=edge.engagement_id,
        edge_id=edge.id,
        relation=edge.relation,
        properties=edge.properties,
        deleted=edge.deleted,
    )
    db.add(history)
    await db.flush()
    await db.refresh(history)
    return history


async def latest_node_history(
    db: AsyncSession,
    node_id: UUID,
) -> GraphNodeHistory | None:
    """Return the most recent GraphNodeHistory row for a node (by recorded_at DESC).

    Returns None if no history rows exist (e.g. the node has never been mutated).
    """
    result = await db.execute(
        select(GraphNodeHistory)
        .where(GraphNodeHistory.node_id == node_id)
        .order_by(desc(GraphNodeHistory.recorded_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def latest_edge_history(
    db: AsyncSession,
    edge_id: UUID,
) -> GraphEdgeHistory | None:
    """Return the most recent GraphEdgeHistory row for an edge (by recorded_at DESC).

    Returns None if no history rows exist.
    """
    result = await db.execute(
        select(GraphEdgeHistory)
        .where(GraphEdgeHistory.edge_id == edge_id)
        .order_by(desc(GraphEdgeHistory.recorded_at))
        .limit(1)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Point lookups
# ---------------------------------------------------------------------------


async def get_node(
    db: AsyncSession,
    node_id: UUID,
) -> GraphNode | None:
    """Return the GraphNode with the given id, or None if not found."""
    result = await db.execute(select(GraphNode).where(GraphNode.id == node_id))
    return result.scalar_one_or_none()


async def get_edge(
    db: AsyncSession,
    edge_id: UUID,
) -> GraphEdge | None:
    """Return the GraphEdge with the given id, or None if not found."""
    result = await db.execute(select(GraphEdge).where(GraphEdge.id == edge_id))
    return result.scalar_one_or_none()


async def find_live_edge(
    db: AsyncSession,
    engagement_id: UUID,
    source_id: UUID,
    target_id: UUID,
    relation: str,
) -> GraphEdge | None:
    """Return the live edge matching (engagement_id, source_id, target_id, relation),
    or None if no such live edge exists.

    "Live" means deleted == False.  Used by the writer consumer as a race-free
    duplicate-triple check: the check and the subsequent insert both execute inside
    the single consumer task, so there is no window for a second concurrent writer
    to slip in between.

    The partial unique index ``uq_graph_edges_live_triple`` is the DB-level backstop
    for the same invariant.
    """
    result = await db.execute(
        select(GraphEdge).where(
            GraphEdge.engagement_id == engagement_id,
            GraphEdge.source_id == source_id,
            GraphEdge.target_id == target_id,
            GraphEdge.relation == relation,
            GraphEdge.deleted.is_(False),
        )
    )
    return result.scalar_one_or_none()
