"""Pydantic v2 request/response models for the graph feature (task 2).

Schemas match the Slice 07 OpenAPI contract exactly — field names, types, enums,
and validation constraints are authoritative here.
"""

import json
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROPERTIES_MAX_BYTES = 64 * 1024  # 64 KB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_properties_size(value: dict[str, Any]) -> dict[str, Any]:
    """Reject a properties blob whose JSON serialization exceeds 64 KB."""
    serialized = json.dumps(value).encode("utf-8")
    if len(serialized) > _PROPERTIES_MAX_BYTES:
        raise ValueError(
            f"properties JSON exceeds the 64 KB limit "
            f"({len(serialized)} bytes > {_PROPERTIES_MAX_BYTES})"
        )
    return value


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class NodeType(StrEnum):
    """Allowed graph node types (§8.1 entity list)."""

    host = "host"
    port = "port"
    service = "service"
    url = "url"
    endpoint = "endpoint"
    vulnerability = "vulnerability"
    credential = "credential"
    note = "note"
    attack_path = "attack_path"


# ---------------------------------------------------------------------------
# Node schemas
# ---------------------------------------------------------------------------


class NodeCreate(BaseModel):
    """Request body for POST .../graph/nodes."""

    type: NodeType
    label: str = Field(min_length=1, max_length=512)
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("properties")
    @classmethod
    def validate_properties_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _check_properties_size(value)


class NodeUpdate(BaseModel):
    """Request body for PATCH .../graph/nodes/{node_id}.

    At least one of ``label`` or ``properties`` must be present.
    ``properties`` fully replaces the prior blob when provided.
    """

    label: str | None = Field(default=None, min_length=1, max_length=512)
    properties: dict[str, Any] | None = None

    @field_validator("properties")
    @classmethod
    def validate_properties_size(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            _check_properties_size(value)
        return value

    @model_validator(mode="after")
    def at_least_one_field(self) -> "NodeUpdate":
        if self.label is None and self.properties is None:
            raise ValueError("At least one of 'label' or 'properties' must be provided.")
        return self


class Node(BaseModel):
    """Response model for a graph node."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    engagement_id: UUID
    type: NodeType
    label: str
    properties: dict[str, Any]
    deleted: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Edge schemas
# ---------------------------------------------------------------------------


class EdgeCreate(BaseModel):
    """Request body for POST .../graph/edges."""

    source_id: UUID
    target_id: UUID
    relation: str = Field(min_length=1, max_length=128)
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("properties")
    @classmethod
    def validate_properties_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _check_properties_size(value)


class Edge(BaseModel):
    """Response model for a graph edge."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    engagement_id: UUID
    source_id: UUID
    target_id: UUID
    relation: str
    properties: dict[str, Any]
    deleted: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Composite / history schemas
# ---------------------------------------------------------------------------


class GraphSnapshot(BaseModel):
    """Full live graph (non-deleted nodes + edges) for one engagement."""

    nodes: list[Node]
    edges: list[Edge]


class NodeHistoryEntry(BaseModel):
    """A single pre-mutation snapshot row from ``graph_node_history``."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    entity_id: UUID  # maps to the ORM ``node_id`` column via alias in repository
    label: str
    properties: dict[str, Any]
    deleted: bool
    recorded_at: datetime


class GraphHistory(BaseModel):
    """History view: soft-deleted nodes and the per-entity edit history."""

    deleted_nodes: list[Node]
    node_history: list[NodeHistoryEntry]
