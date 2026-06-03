"""SQLAlchemy ORM models for the graph feature: GraphNode, GraphEdge,
GraphNodeHistory, GraphEdgeHistory. Owns the four graph_* tables (task 1)."""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from app.core.db import Base

# JSONB on Postgres (production + migrations); generic JSON on SQLite so the
# in-memory unit-test engine can render the DDL. Without the variant, JSONB has
# no SQLite compiler and create_all() fails for every test that builds the shared
# Base.metadata.
_PROPS_JSON = JSONB().with_variant(JSON(), "sqlite")


class GraphNode(Base):
    """A graph entity (host, port, service, url, endpoint, vulnerability,
    credential, note, attack_path) belonging to one engagement."""

    __tablename__ = "graph_nodes"
    __table_args__ = (
        Index("ix_graph_nodes_engagement_id", "engagement_id"),
        # Partial index: only live (non-deleted) nodes — fast live-graph load.
        Index(
            "ix_graph_nodes_engagement_live",
            "engagement_id",
            postgresql_where=text("deleted = false"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    engagement_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(512), nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(
        _PROPS_JSON, nullable=False, server_default=text("'{}'")
    )
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class GraphEdge(Base):
    """A directed edge between two GraphNode entities within an engagement."""

    __tablename__ = "graph_edges"
    __table_args__ = (
        Index("ix_graph_edges_engagement_id", "engagement_id"),
        Index("ix_graph_edges_source_id", "source_id"),
        Index("ix_graph_edges_target_id", "target_id"),
        # Partial unique index: no two *live* edges may share the same
        # (engagement_id, source_id, target_id, relation) triple. A soft-deleted
        # edge does not block re-creating the same triple.
        Index(
            "uq_graph_edges_live_triple",
            "engagement_id",
            "source_id",
            "target_id",
            "relation",
            unique=True,
            postgresql_where=text("deleted = false"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    engagement_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("graph_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("graph_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    relation: Mapped[str] = mapped_column(String(128), nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(
        _PROPS_JSON, nullable=False, server_default=text("'{}'")
    )
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class GraphNodeHistory(Base):
    """Append-only pre-mutation snapshots of GraphNode state, enabling per-entity
    undo. One row is written *before* each mutation, capturing the state that undo
    would restore. No provenance columns — the audit log (Slice 10) is the source
    of truth for who made each change."""

    __tablename__ = "graph_node_history"
    __table_args__ = (
        # Composite index ordered by recorded_at DESC so latest-prior lookup is fast.
        Index("ix_graph_node_history_node_id", "node_id", text("recorded_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    engagement_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("graph_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(512), nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(_PROPS_JSON, nullable=False)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GraphEdgeHistory(Base):
    """Append-only pre-mutation snapshots of GraphEdge state, enabling per-entity
    undo. Same shape as GraphNodeHistory. No provenance columns."""

    __tablename__ = "graph_edge_history"
    __table_args__ = (Index("ix_graph_edge_history_edge_id", "edge_id", text("recorded_at DESC")),)

    id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    engagement_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("engagements.id", ondelete="CASCADE"),
        nullable=False,
    )
    edge_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("graph_edges.id", ondelete="CASCADE"),
        nullable=False,
    )
    relation: Mapped[str] = mapped_column(String(128), nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(_PROPS_JSON, nullable=False)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
