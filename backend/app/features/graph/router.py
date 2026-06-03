"""FastAPI routes for the graph feature (task 6). HTTP-level concerns only.

All writes are serialized through the service → single writer (ADR-0001).
Domain exceptions subclass NotFoundError or ConflictError and are translated to
HTTP codes by the registered core error handlers:

  EngagementNotFound  → NotFoundError  → 404
  NodeNotFound        → NotFoundError  → 404
  EdgeNotFound        → NotFoundError  → 404
  NoHistory           → NotFoundError  → 404
  DuplicateEdge       → ConflictError  → 409
  EngagementArchived  → ConflictError  → 409

401 is produced automatically by the get_current_user dependency when the
session cookie is absent or invalid.  422 is produced automatically by Pydantic
body validation.  The router does NOT catch or translate any domain exception
itself — all HTTP mapping is via the registered handlers.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.features.auth.deps import get_current_user
from app.features.auth.models import User
from app.features.graph import service
from app.features.graph.schemas import (
    Edge,
    EdgeCreate,
    GraphHistory,
    GraphSnapshot,
    Node,
    NodeCreate,
    NodeUpdate,
)

router = APIRouter(prefix="/api/v1", tags=["graph"])


# ---------------------------------------------------------------------------
# GET /api/v1/engagements/{engagement_id}/graph
# operationId: get_graph
# ---------------------------------------------------------------------------


@router.get(
    "/engagements/{engagement_id}/graph",
    response_model=GraphSnapshot,
    operation_id="get_graph",
)
async def get_graph(
    engagement_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> GraphSnapshot:
    """Return the full live graph (non-deleted nodes + edges) for an engagement.

    Served from the in-memory single writer (warm-starts from Postgres on first
    access after a restart).  Membership-gated (§17.1 — non-member returns 404).
    Read-only path: archived engagements are accessible.
    """
    return await service.get_graph(
        db,
        engagement_id=engagement_id,
        user_id=current_user.id,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# GET /api/v1/engagements/{engagement_id}/graph/history
# operationId: get_graph_history
# ---------------------------------------------------------------------------


@router.get(
    "/engagements/{engagement_id}/graph/history",
    response_model=GraphHistory,
    operation_id="get_graph_history",
)
async def get_graph_history(
    engagement_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    include_deleted: Annotated[bool, Query()] = True,
) -> GraphHistory:
    """Return soft-deleted nodes and the per-entity node history.

    Membership-gated.  Read-only path: archived engagements are accessible.
    ``include_deleted`` defaults to true (matching the contract).
    """
    return await service.get_graph_history(
        db,
        engagement_id=engagement_id,
        user_id=current_user.id,  # type: ignore[arg-type]
        include_deleted=include_deleted,
    )


# ---------------------------------------------------------------------------
# POST /api/v1/engagements/{engagement_id}/graph/nodes
# operationId: create_node
# ---------------------------------------------------------------------------


@router.post(
    "/engagements/{engagement_id}/graph/nodes",
    response_model=Node,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_node",
)
async def create_node(
    engagement_id: UUID,
    body: NodeCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Node:
    """Create a graph node (serialized through the single writer).

    Membership-gated.  Returns 409 for archived engagements; 422 for bad type
    or invalid body (automatic Pydantic validation).
    """
    return await service.create_node(
        db,
        engagement_id=engagement_id,
        user_id=current_user.id,  # type: ignore[arg-type]
        payload=body,
    )


# ---------------------------------------------------------------------------
# PATCH /api/v1/engagements/{engagement_id}/graph/nodes/{node_id}
# operationId: update_node
# ---------------------------------------------------------------------------


@router.patch(
    "/engagements/{engagement_id}/graph/nodes/{node_id}",
    response_model=Node,
    operation_id="update_node",
)
async def update_node(
    engagement_id: UUID,
    node_id: UUID,
    body: NodeUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Node:
    """Update a node's label and/or properties (serialized; records a history entry).

    Membership-gated.  Returns 404 when the node is not found or deleted.
    Returns 409 for archived engagements.
    """
    return await service.update_node(
        db,
        engagement_id=engagement_id,
        node_id=node_id,
        user_id=current_user.id,  # type: ignore[arg-type]
        payload=body,
    )


# ---------------------------------------------------------------------------
# DELETE /api/v1/engagements/{engagement_id}/graph/nodes/{node_id}
# operationId: delete_node
# ---------------------------------------------------------------------------


@router.delete(
    "/engagements/{engagement_id}/graph/nodes/{node_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_node",
)
async def delete_node(
    engagement_id: UUID,
    node_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    response: Response,
) -> None:
    """Soft-delete a node and cascade to its incident edges (serialized).

    Returns 204 on success (no body).  Returns 404 when not found or already
    deleted; 409 when the engagement is archived.
    """
    await service.delete_node(
        db,
        engagement_id=engagement_id,
        node_id=node_id,
        user_id=current_user.id,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# POST /api/v1/engagements/{engagement_id}/graph/nodes/{node_id}/undo
# operationId: undo_node
# ---------------------------------------------------------------------------


@router.post(
    "/engagements/{engagement_id}/graph/nodes/{node_id}/undo",
    response_model=Node,
    operation_id="undo_node",
)
async def undo_node(
    engagement_id: UUID,
    node_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Node:
    """Revert a node to its immediately-prior state from history (serialized).

    Returns 404 when no prior state exists (NoHistory).
    Returns 409 for archived engagements.
    """
    return await service.undo_node(
        db,
        engagement_id=engagement_id,
        node_id=node_id,
        user_id=current_user.id,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# POST /api/v1/engagements/{engagement_id}/graph/edges
# operationId: create_edge
# ---------------------------------------------------------------------------


@router.post(
    "/engagements/{engagement_id}/graph/edges",
    response_model=Edge,
    status_code=status.HTTP_201_CREATED,
    operation_id="create_edge",
)
async def create_edge(
    engagement_id: UUID,
    body: EdgeCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Edge:
    """Create a directed edge between two existing non-deleted nodes (serialized).

    Duplicate live (source, target, relation) triples are rejected with 409
    (DuplicateEdge — checked race-free inside the writer consumer).
    Missing or deleted endpoints return 404.
    """
    return await service.create_edge(
        db,
        engagement_id=engagement_id,
        user_id=current_user.id,  # type: ignore[arg-type]
        payload=body,
    )


# ---------------------------------------------------------------------------
# DELETE /api/v1/engagements/{engagement_id}/graph/edges/{edge_id}
# operationId: delete_edge
# ---------------------------------------------------------------------------


@router.delete(
    "/engagements/{engagement_id}/graph/edges/{edge_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="delete_edge",
)
async def delete_edge(
    engagement_id: UUID,
    edge_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
    response: Response,
) -> None:
    """Soft-delete an edge (serialized).

    Returns 204 on success (no body).  Returns 404 when not found or already
    deleted; 409 when the engagement is archived.
    """
    await service.delete_edge(
        db,
        engagement_id=engagement_id,
        edge_id=edge_id,
        user_id=current_user.id,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# POST /api/v1/engagements/{engagement_id}/graph/edges/{edge_id}/undo
# operationId: undo_edge
# ---------------------------------------------------------------------------


@router.post(
    "/engagements/{engagement_id}/graph/edges/{edge_id}/undo",
    response_model=Edge,
    operation_id="undo_edge",
)
async def undo_edge(
    engagement_id: UUID,
    edge_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Edge:
    """Revert an edge to its prior state from history (serialized).

    Returns 404 when no prior state exists (NoHistory) or edge not found.
    Returns 409 for archived engagements.
    """
    return await service.undo_edge(
        db,
        engagement_id=engagement_id,
        edge_id=edge_id,
        user_id=current_user.id,  # type: ignore[arg-type]
    )
