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

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.features.engagements import repository as eng_repo
from app.features.engagements.models import Engagement
from app.features.graph import repository as repo
from app.features.graph import writer
from app.features.graph.errors import (
    EdgeNotFound,
    EngagementArchived,
    NodeNotFound,
    NoHistory,
)
from app.features.graph.models import (
    GraphEdge,
    GraphNode,
    GraphNodeHistory,
    GraphUserUndoStack,
)
from app.features.graph.schemas import (
    Edge,
    EdgeCreate,
    GraphHistory,
    GraphSnapshot,
    Node,
    NodeCreate,
    NodeHistoryEntry,
    NodeUpdate,
    UndoResult,
    UndoStack,
    UndoStackEntry,
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
# Personal undo stack (Slice 09) — staleness, summaries, push
# ---------------------------------------------------------------------------


def _is_entry_stale(
    entry: GraphUserUndoStack,
    current_entity: GraphNode | GraphEdge | None,
) -> bool:
    """Pure staleness predicate for a personal-undo entry (Decision 1).

    An entry is stale if its target entity changed AT ALL after the entry was
    recorded — ANY later write, by anyone, including the entry's own owner. A
    missing / hard-deleted entity is also stale. We compare the entity's current
    ``updated_at`` to the baseline captured immediately after the user's write.

    The writer sets ``updated_at`` via ``onupdate=func.now()`` on every mutation,
    and all writes are serialized through the single writer (ADR-0001), so any
    later mutation strictly follows and bumps ``updated_at`` past the baseline.
    A stale entry is never silently applied — it is dropped and surfaced (§8.2).
    """
    if current_entity is None:
        return True
    return current_entity.updated_at != entry.target_updated_at


def _node_op_summary(op_type: str, node_type: str, label: str) -> str:
    """Human-readable label for a node undo entry, e.g. 'Created host 10.0.0.5'."""
    verb = {"create_node": "Created", "update_node": "Updated", "delete_node": "Deleted"}[op_type]
    return f"{verb} {node_type} {label}"[:256]


def _edge_op_summary(op_type: str, relation: str) -> str:
    """Human-readable label for an edge undo entry, e.g. 'Created runs edge'."""
    verb = {"create_edge": "Created", "delete_edge": "Deleted"}[op_type]
    return f"{verb} {relation} edge"[:256]


async def _read_current_node(db: AsyncSession, node_id: UUID) -> GraphNode | None:
    """Read the authoritative GraphNode row for a staleness check.

    We read from Postgres directly (like get_graph_history) rather than the
    writer's in-memory snapshot: the in-memory Node's ``updated_at`` is NOT
    re-stamped on soft-delete, so the DB row is the only reliable staleness
    baseline. This is a READ — it never mutates a graph entity, so the
    single-writer invariant (ADR-0001) is untouched.
    """
    return await repo.get_node(db, node_id)


async def _read_current_edge(db: AsyncSession, edge_id: UUID) -> GraphEdge | None:
    """Read the authoritative GraphEdge row for a staleness check (see _read_current_node)."""
    return await repo.get_edge(db, edge_id)


async def _push_undo(
    db: AsyncSession,
    *,
    engagement_id: UUID,
    user_id: UUID,
    op_type: str,
    entity_kind: str,
    entity_id: UUID,
    target_updated_at: datetime,
    summary: str,
) -> None:
    """Record one personal-undo entry for a human write and commit it.

    Called AFTER the writer has committed the graph mutation in its own session.
    This touches ONLY graph_user_undo_stack via the request's db session — never a
    graph entity — so it does NOT go through the writer queue and cannot violate
    the single-writer invariant (ADR-0001). Together with pop_undo_stack this is
    one of the two chokepoints where Slice 10 will attach audit emission
    (Decision 4); no audit module is imported here.
    """
    await repo.push_undo_entry(
        db,
        engagement_id=engagement_id,
        user_id=user_id,
        op_type=op_type,
        entity_kind=entity_kind,
        entity_id=entity_id,
        target_updated_at=target_updated_at,
        summary=summary,
    )
    await db.commit()


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
    node = await writer.submit_create_node(
        engagement_id,
        node_type=payload.type.value,
        label=payload.label,
        properties=payload.properties,
    )
    await _push_undo(
        db,
        engagement_id=engagement_id,
        user_id=user_id,
        op_type="create_node",
        entity_kind="node",
        entity_id=node.id,
        target_updated_at=node.updated_at,
        summary=_node_op_summary("create_node", node.type.value, node.label),
    )
    return node


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
    node = await writer.submit_update_node(
        engagement_id,
        node_id,
        label=payload.label,
        properties=payload.properties,
    )
    await _push_undo(
        db,
        engagement_id=engagement_id,
        user_id=user_id,
        op_type="update_node",
        entity_kind="node",
        entity_id=node.id,
        target_updated_at=node.updated_at,
        summary=_node_op_summary("update_node", node.type.value, node.label),
    )
    return node


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
    # Re-read the now soft-deleted row for the staleness baseline (updated_at was
    # re-stamped by the delete) and the summary fields. submit returns None.
    deleted = await _read_current_node(db, node_id)
    if deleted is not None:
        await _push_undo(
            db,
            engagement_id=engagement_id,
            user_id=user_id,
            op_type="delete_node",
            entity_kind="node",
            entity_id=node_id,
            target_updated_at=cast(datetime, deleted.updated_at),
            summary=_node_op_summary("delete_node", deleted.type, deleted.label),
        )


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

    edge = await writer.submit_create_edge(
        engagement_id,
        source_id=payload.source_id,
        target_id=payload.target_id,
        relation=payload.relation,
        properties=payload.properties,
    )
    await _push_undo(
        db,
        engagement_id=engagement_id,
        user_id=user_id,
        op_type="create_edge",
        entity_kind="edge",
        entity_id=edge.id,
        target_updated_at=edge.updated_at,
        summary=_edge_op_summary("create_edge", edge.relation),
    )
    return edge


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
    # Re-read the now soft-deleted edge for the staleness baseline + summary.
    deleted = await _read_current_edge(db, edge_id)
    if deleted is not None:
        await _push_undo(
            db,
            engagement_id=engagement_id,
            user_id=user_id,
            op_type="delete_edge",
            entity_kind="edge",
            entity_id=edge_id,
            target_updated_at=cast(datetime, deleted.updated_at),
            summary=_edge_op_summary("delete_edge", deleted.relation),
        )


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


# ---------------------------------------------------------------------------
# Personal undo stack (Slice 09) — get + pop
#
# No lifespan/warm-start hook is needed (task 7): unlike the in-memory writer,
# the personal stack is fully persisted in Postgres (graph_user_undo_stack), so
# it survives a process restart with no rebuild. The staleness baseline
# (target_updated_at) is captured at push time and stored — never recomputed.
# ---------------------------------------------------------------------------


async def _current_entity(
    db: AsyncSession,
    entry: GraphUserUndoStack,
) -> GraphNode | GraphEdge | None:
    """Read the entry's target entity (node or edge) for a staleness check."""
    entity_id = cast(UUID, entry.entity_id)
    if entry.entity_kind == "node":
        return await repo.get_node(db, entity_id)
    return await repo.get_edge(db, entity_id)


def _to_entry(row: GraphUserUndoStack, *, stale: bool) -> UndoStackEntry:
    """Map an ORM stack row to the API entry, setting the computed stale flag."""
    entry = UndoStackEntry.model_validate(row)
    entry.stale = stale
    return entry


async def _build_stack(
    db: AsyncSession,
    engagement_id: UUID,
    user_id: UUID,
) -> UndoStack:
    """Assemble the owner's current active stack (newest-first) with stale flags."""
    rows = await repo.list_active_undo_stack(db, engagement_id, user_id)
    entries: list[UndoStackEntry] = []
    for row in rows:
        current = await _current_entity(db, row)
        entries.append(_to_entry(row, stale=_is_entry_stale(row, current)))
    return UndoStack(depth=len(entries), entries=entries)


async def _apply_inverse(engagement_id: UUID, row: GraphUserUndoStack) -> None:
    """Apply the inverse of a recorded write through the single writer (ADR-0001).

    No new write primitive is added — the inverse composes the Slice 07
    ``writer.submit_*`` calls:
      create_node → soft-delete the created node
      update_node / delete_node → submit_undo_node (one step back through history)
      create_edge → soft-delete the created edge
      delete_edge → submit_undo_edge
    """
    op_type = row.op_type
    entity_id = cast(UUID, row.entity_id)
    if op_type == "create_node":
        await writer.submit_soft_delete_node(engagement_id, entity_id)
    elif op_type in ("update_node", "delete_node"):
        await writer.submit_undo_node(engagement_id, entity_id)
    elif op_type == "create_edge":
        await writer.submit_soft_delete_edge(engagement_id, entity_id)
    elif op_type == "delete_edge":
        await writer.submit_undo_edge(engagement_id, entity_id)


async def get_undo_stack(
    db: AsyncSession,
    engagement_id: UUID,
    user_id: UUID,
) -> UndoStack:
    """Return the caller's personal undo stack for this engagement (newest-first).

    READ path — no archived guard; stale entries are flagged (not dropped). Each
    entry's ``stale`` flag is computed against current graph state (Decision 1).

    Raises:
        EngagementNotFound: caller not a member or engagement missing (→404).
    """
    await _require_member(db, engagement_id, user_id)
    return await _build_stack(db, engagement_id, user_id)


async def pop_undo_stack(
    db: AsyncSession,
    engagement_id: UUID,
    user_id: UUID,
) -> UndoResult:
    """Undo the caller's most recent still-valid personal write (write — the
    inverse is serialized through the single writer).

    Walks the active stack newest-first. Stale entries (a teammate — or the owner
    — modified the target since, per Decision 1) are dropped and surfaced in
    ``skipped_stale``; they are NEVER silently applied (§8.2). The first fresh
    entry is undone via ``_apply_inverse`` and returned. If the entity has since
    vanished or has no prior state, that entry is treated as stale too.

    Per Decision 2, an empty stack (or one where every remaining entry was stale)
    returns ``UndoResult(undone=None, ...)`` — NOT an error/422. A pop never
    pushes a new entry (no redo).

    AUDIT SEAM (Slice 10): this is the single chokepoint for "a human graph write
    was undone" (the counterpart to repository.push_undo_entry). Slice 10 attaches
    audit emission here. Per Decision 4 NO audit module is imported or called in
    this slice — the seam is left clean and documented only.

    Raises:
        EngagementNotFound: caller not a member or engagement missing (→404).
        EngagementArchived: engagement is archived (→409).
    """
    engagement = await _require_member(db, engagement_id, user_id)
    _require_writable(engagement)

    skipped: list[UndoStackEntry] = []
    rows = await repo.list_active_undo_stack(db, engagement_id, user_id)

    for row in rows:  # newest-first
        current = await _current_entity(db, row)
        if _is_entry_stale(row, current):
            await repo.mark_undo_entry_undone(db, row)
            skipped.append(_to_entry(row, stale=True))
            continue

        # Fresh entry — apply the inverse through the single writer.
        try:
            await _apply_inverse(engagement_id, row)
        except (NodeNotFound, EdgeNotFound, NoHistory):
            # Entity vanished or no prior state — drop as stale, keep walking.
            await repo.mark_undo_entry_undone(db, row)
            skipped.append(_to_entry(row, stale=True))
            continue

        await repo.mark_undo_entry_undone(db, row)
        undone = _to_entry(row, stale=False)
        await db.commit()
        stack = await _build_stack(db, engagement_id, user_id)
        return UndoResult(undone=undone, skipped_stale=skipped, stack=stack)

    # Nothing applied: empty stack, or every remaining entry was stale (Decision 2).
    await db.commit()
    stack = await _build_stack(db, engagement_id, user_id)
    return UndoResult(undone=None, skipped_stale=skipped, stack=stack)
