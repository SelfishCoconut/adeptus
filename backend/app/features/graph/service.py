"""Business logic (orchestration + invariants) for the graph feature.

Domain exceptions raised here and in the writer are translated to HTTP codes
in router.py via the core error-handler registry (Starlette MRO-based lookup):

  EngagementNotFound  → NotFoundError → 404
  NodeNotFound        → NotFoundError → 404
  EdgeNotFound        → NotFoundError → 404
  NoHistory           → NotFoundError → 404
  DuplicateEdge       → ConflictError → 409
  EngagementArchived  → ConflictError → 409

Membership chokepoint (§17.1 / §4 no-admin-bypass):
  Every public function first calls _require_member(), which delegates to
  engagements.repository.get_engagement_for_member().  Both "engagement does
  not exist" and "caller is not a member" collapse to EngagementNotFound (→404)
  so a non-member cannot infer that the engagement exists (no existence
  disclosure).  Admin role is never consulted — the fused query checks only for
  an explicit member row (§4 no-admin-bypass, matching the MCP service posture).

Archived guard (§4 read-only):
  _require_writable() raises EngagementArchived (→409) if the engagement's
  status field equals "archived".  Called on every WRITE path.  READ paths
  (get_graph, get_graph_history) skip the writable check so archived data
  remains accessible.

Single-writer delegation (ADR-0001):
  All writes go through writer.submit_*; domain errors propagate unchanged.
  Reads go through writer.read_* (in-memory, lazy warm-start from Postgres).
"""

from __future__ import annotations

from typing import cast
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.features.engagements import repository as eng_repo
from app.features.engagements.models import Engagement
from app.features.graph import writer
from app.features.graph.errors import (
    EngagementArchived,
    NodeNotFound,
)
from app.features.graph.models import GraphNode, GraphNodeHistory
from app.features.graph.schemas import (
    Edge,
    EdgeCreate,
    GraphHistory,
    GraphSnapshot,
    Node,
    NodeCreate,
    NodeHistoryEntry,
    NodeUpdate,
)

# ---------------------------------------------------------------------------
# EngagementNotFound — local definition following the mcp.service pattern
# ---------------------------------------------------------------------------


class EngagementNotFound(NotFoundError):
    """Raised when the engagement does not exist OR the caller is not a member.

    Collapses both cases into the same 404 to avoid existence disclosure (§17.1).
    No admin bypass — the membership query never consults role (§4).
    Mirrors mcp.service.EngagementNotFound exactly.
    """

    def __init__(self, message: str = "Engagement not found") -> None:
        super().__init__(message)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _require_member(
    db: AsyncSession,
    engagement_id: UUID,
    user_id: UUID,
) -> Engagement:
    """Fused existence + membership check — the §17.1 isolation chokepoint.

    Calls get_engagement_for_member which returns (Engagement, EngagementMember)
    or None.  None covers both "engagement missing" and "caller not a member"
    so neither case discloses whether the engagement exists.

    Returns the Engagement object (caller may inspect .status for the archived
    guard without an extra round-trip).

    Raises:
        EngagementNotFound: engagement_id does not exist OR user_id has no
                            explicit member row (admin role ignored, §4).
    """
    pair = await eng_repo.get_engagement_for_member(db, engagement_id, user_id)
    if pair is None:
        raise EngagementNotFound(f"Engagement {engagement_id} not found")
    engagement, _ = pair
    return engagement


def _require_writable(engagement: Engagement) -> None:
    """Raise EngagementArchived (→409) if the engagement is archived.

    Called on every WRITE path (§4: archived engagements are read-only).
    Read paths (get_graph, get_graph_history) do NOT call this helper so that
    data remains accessible for inspection even after archiving.

    Raises:
        EngagementArchived: engagement.status == "archived".
    """
    if engagement.status == "archived":
        raise EngagementArchived(f"Engagement {engagement.id} is archived; writes are not allowed")


# ---------------------------------------------------------------------------
# Public read functions
# ---------------------------------------------------------------------------


async def get_graph(
    db: AsyncSession,
    engagement_id: UUID,
    user_id: UUID,
) -> GraphSnapshot:
    """Return the live (non-deleted) graph snapshot from the in-memory writer.

    READ path — no archived guard; archived engagement data remains visible.

    Raises:
        EngagementNotFound: caller not a member or engagement missing (→404).
    """
    await _require_member(db, engagement_id, user_id)
    return await writer.read_graph(engagement_id)


async def get_graph_history(
    db: AsyncSession,
    engagement_id: UUID,
    user_id: UUID,
    include_deleted: bool = True,
) -> GraphHistory:
    """Return soft-deleted nodes and the per-entity node history.

    READ path — no archived guard.  ``include_deleted`` is honoured for
    deleted_nodes; node_history always returns all recorded history rows.

    Assembles GraphHistory from two repository queries:
      - deleted_nodes: GraphNode rows where deleted=True (if include_deleted).
      - node_history: recent GraphNodeHistory rows for the engagement, mapped
        node_id → entity_id (NodeHistoryEntry uses entity_id as the alias).

    Raises:
        EngagementNotFound: caller not a member or engagement missing (→404).
    """
    await _require_member(db, engagement_id, user_id)

    # Deleted nodes.
    deleted_nodes: list[Node] = []
    if include_deleted:
        result = await db.execute(
            select(GraphNode).where(
                GraphNode.engagement_id == engagement_id,
                GraphNode.deleted.is_(True),
            )
        )
        deleted_nodes = [Node.model_validate(row) for row in result.scalars().all()]

    # Node history rows — all history for this engagement, newest first.
    hist_result = await db.execute(
        select(GraphNodeHistory)
        .where(GraphNodeHistory.engagement_id == engagement_id)
        .order_by(desc(GraphNodeHistory.recorded_at))
    )
    history_rows = list(hist_result.scalars().all())

    node_history = [
        NodeHistoryEntry(
            id=cast(UUID, row.id),
            entity_id=cast(UUID, row.node_id),  # ORM column node_id → schema field entity_id
            label=row.label,
            properties=dict(row.properties) if row.properties else {},
            deleted=row.deleted,
            recorded_at=row.recorded_at,
        )
        for row in history_rows
    ]

    return GraphHistory(deleted_nodes=deleted_nodes, node_history=node_history)


# ---------------------------------------------------------------------------
# Public write functions — nodes
# ---------------------------------------------------------------------------


async def create_node(
    db: AsyncSession,
    engagement_id: UUID,
    user_id: UUID,
    payload: NodeCreate,
) -> Node:
    """Create a new graph node (write — serialized through the single writer).

    Raises:
        EngagementNotFound:  caller not a member or engagement missing (→404).
        EngagementArchived:  engagement is archived (→409).
    """
    engagement = await _require_member(db, engagement_id, user_id)
    _require_writable(engagement)
    return await writer.submit_create_node(
        engagement_id,
        node_type=payload.type.value,
        label=payload.label,
        properties=payload.properties,
    )


async def update_node(
    db: AsyncSession,
    engagement_id: UUID,
    node_id: UUID,
    user_id: UUID,
    payload: NodeUpdate,
) -> Node:
    """Update a node's label and/or properties (write — serialized; records a history entry).

    Raises:
        EngagementNotFound:  caller not a member or engagement missing (→404).
        EngagementArchived:  engagement is archived (→409).
        NodeNotFound:        node_id not found, already deleted, or belongs to a
                             different engagement (→404; raised by the writer consumer).
    """
    engagement = await _require_member(db, engagement_id, user_id)
    _require_writable(engagement)
    return await writer.submit_update_node(
        engagement_id,
        node_id,
        label=payload.label,
        properties=payload.properties,
    )


async def delete_node(
    db: AsyncSession,
    engagement_id: UUID,
    node_id: UUID,
    user_id: UUID,
) -> None:
    """Soft-delete a node and cascade soft-delete to its incident edges (write — serialized).

    Raises:
        EngagementNotFound:  caller not a member or engagement missing (→404).
        EngagementArchived:  engagement is archived (→409).
        NodeNotFound:        node_id not found or already deleted (→404).
    """
    engagement = await _require_member(db, engagement_id, user_id)
    _require_writable(engagement)
    await writer.submit_soft_delete_node(engagement_id, node_id)


async def undo_node(
    db: AsyncSession,
    engagement_id: UUID,
    node_id: UUID,
    user_id: UUID,
) -> Node:
    """Revert a node to its immediately-prior state from history (write — serialized).

    NoHistory (→404) propagates unchanged from the writer consumer: if there is
    no prior history entry for node_id, the writer raises NoHistory which the
    router translates to 404.

    Raises:
        EngagementNotFound:  caller not a member or engagement missing (→404).
        EngagementArchived:  engagement is archived (→409).
        NodeNotFound:        node_id not found (→404).
        NoHistory:           no prior state to revert to (→404).
    """
    engagement = await _require_member(db, engagement_id, user_id)
    _require_writable(engagement)
    # NoHistory propagates from the writer unchanged — let it bubble up.
    return await writer.submit_undo_node(engagement_id, node_id)


# ---------------------------------------------------------------------------
# Public write functions — edges
# ---------------------------------------------------------------------------


async def create_edge(
    db: AsyncSession,
    engagement_id: UUID,
    user_id: UUID,
    payload: EdgeCreate,
) -> Edge:
    """Create a directed edge between two existing non-deleted nodes (write — serialized).

    Validates that both endpoints exist and are non-deleted BEFORE enqueuing the
    command.  Uses the in-memory full graph snapshot (includes deleted) from the
    writer to avoid an extra DB round-trip.  If either node is missing or deleted,
    raises NodeNotFound (→404).

    The duplicate-live-triple check is NOT done here — it is performed INSIDE the
    writer consumer (so it is race-free under the single-writer guarantee); any
    DuplicateEdge exception propagates unchanged to the router (→409).

    Raises:
        EngagementNotFound:  caller not a member or engagement missing (→404).
        EngagementArchived:  engagement is archived (→409).
        NodeNotFound:        source or target node is missing or deleted (→404).
        DuplicateEdge:       a live edge with the same triple already exists (→409).
    """
    engagement = await _require_member(db, engagement_id, user_id)
    _require_writable(engagement)

    # Validate both endpoints in one in-memory read (warm-starts the writer).
    snapshot = await writer.read_full(engagement_id)
    live_node_ids = {n.id for n in snapshot.nodes if not n.deleted}

    if payload.source_id not in live_node_ids:
        raise NodeNotFound(f"Source node {payload.source_id} not found or deleted")
    if payload.target_id not in live_node_ids:
        raise NodeNotFound(f"Target node {payload.target_id} not found or deleted")

    return await writer.submit_create_edge(
        engagement_id,
        source_id=payload.source_id,
        target_id=payload.target_id,
        relation=payload.relation,
        properties=payload.properties,
    )


async def delete_edge(
    db: AsyncSession,
    engagement_id: UUID,
    edge_id: UUID,
    user_id: UUID,
) -> None:
    """Soft-delete an edge (write — serialized).

    Raises:
        EngagementNotFound:  caller not a member or engagement missing (→404).
        EngagementArchived:  engagement is archived (→409).
        EdgeNotFound:        edge_id not found or already deleted (→404).
    """
    engagement = await _require_member(db, engagement_id, user_id)
    _require_writable(engagement)
    await writer.submit_soft_delete_edge(engagement_id, edge_id)


async def undo_edge(
    db: AsyncSession,
    engagement_id: UUID,
    edge_id: UUID,
    user_id: UUID,
) -> Edge:
    """Revert an edge to its prior state from history (write — serialized).

    NoHistory (→404) propagates from the writer consumer unchanged.

    Raises:
        EngagementNotFound:  caller not a member or engagement missing (→404).
        EngagementArchived:  engagement is archived (→409).
        EdgeNotFound:        edge_id not found (→404).
        NoHistory:           no prior state to revert to (→404).
    """
    engagement = await _require_member(db, engagement_id, user_id)
    _require_writable(engagement)
    return await writer.submit_undo_edge(engagement_id, edge_id)
