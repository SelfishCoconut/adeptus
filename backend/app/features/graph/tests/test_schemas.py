"""Unit tests for app.features.graph.schemas (task 2).

Covers: NodeType enum, NodeCreate/NodeUpdate/Node validation,
EdgeCreate, round-trip model_validate from attribute objects,
and the 64 KB properties size cap.
"""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError

from app.features.graph.schemas import (
    Edge,
    EdgeCreate,
    GraphHistory,
    GraphSnapshot,
    Node,
    NodeCreate,
    NodeHistoryEntry,
    NodeType,
    NodeUpdate,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)
_UUID = uuid.uuid4()
_UUID2 = uuid.uuid4()


def _make_node_orm(**overrides: object) -> SimpleNamespace:
    """Return a namespace that looks like a GraphNode ORM row."""
    defaults = dict(
        id=_UUID,
        engagement_id=_UUID2,
        type="host",
        label="10.0.0.1",
        properties={"os": "linux"},
        deleted=False,
        created_at=_NOW,
        updated_at=_NOW,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# NodeType
# ---------------------------------------------------------------------------


def test_node_type_all_members() -> None:
    expected = {
        "host",
        "port",
        "service",
        "url",
        "endpoint",
        "vulnerability",
        "credential",
        "note",
        "attack_path",
    }
    assert {m.value for m in NodeType} == expected


def test_node_type_is_str() -> None:
    assert NodeType.host == "host"
    assert isinstance(NodeType.host, str)


def test_node_type_rejects_bad_value() -> None:
    with pytest.raises(ValidationError):
        # cast() (not a `type: ignore`) feeds the invalid value past the type
        # checker so the runtime validation is what's under test — and it keeps
        # both mypy configs happy (make-lint's warn_unused_ignores vs pre-commit).
        NodeCreate(type=cast(NodeType, "router"), label="10.0.0.1")


# ---------------------------------------------------------------------------
# NodeCreate
# ---------------------------------------------------------------------------


def test_node_create_valid_minimal() -> None:
    nc = NodeCreate(type=NodeType.host, label="10.0.0.1")
    assert nc.type == NodeType.host
    assert nc.label == "10.0.0.1"
    assert nc.properties == {}


def test_node_create_valid_with_properties() -> None:
    nc = NodeCreate(type=NodeType.service, label="nginx", properties={"version": "1.24"})
    assert nc.properties == {"version": "1.24"}


def test_node_create_empty_label_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        NodeCreate(type=NodeType.host, label="")
    errors = exc_info.value.errors()
    assert any(e["loc"] == ("label",) for e in errors)


def test_node_create_label_too_long_rejected() -> None:
    with pytest.raises(ValidationError):
        NodeCreate(type=NodeType.host, label="x" * 513)


def test_node_create_oversized_properties_rejected() -> None:
    # Build a dict whose JSON is just over 64 KB
    big = {"key": "x" * (64 * 1024)}
    with pytest.raises(ValidationError) as exc_info:
        NodeCreate(type=NodeType.host, label="target", properties=big)
    errors = exc_info.value.errors()
    assert any("64 KB" in str(e["msg"]) for e in errors)


def test_node_create_properties_exactly_at_limit_accepted() -> None:
    # A properties blob whose JSON serialization is exactly 64 KB is accepted.
    # Build a value where the serialized length == 65536 bytes.
    # {"k":"<padding>"} — key + structural chars consume fixed overhead.
    target_size = 64 * 1024
    overhead = len(b'{"k": ""}')  # 9 bytes
    padding = "x" * (target_size - overhead)
    props = {"k": padding}
    serialized_len = len(json.dumps(props).encode("utf-8"))
    assert serialized_len == target_size
    nc = NodeCreate(type=NodeType.host, label="target", properties=props)
    assert nc.properties == props


# ---------------------------------------------------------------------------
# NodeUpdate
# ---------------------------------------------------------------------------


def test_node_update_label_only() -> None:
    nu = NodeUpdate(label="new-label")
    assert nu.label == "new-label"
    assert nu.properties is None


def test_node_update_properties_only() -> None:
    nu = NodeUpdate(properties={"k": "v"})
    assert nu.label is None
    assert nu.properties == {"k": "v"}


def test_node_update_both_fields() -> None:
    nu = NodeUpdate(label="l", properties={"k": "v"})
    assert nu.label == "l"
    assert nu.properties == {"k": "v"}


def test_node_update_neither_field_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        NodeUpdate()
    errors = exc_info.value.errors()
    assert any("label" in str(e["msg"]) or "properties" in str(e["msg"]) for e in errors)


def test_node_update_empty_label_rejected() -> None:
    with pytest.raises(ValidationError):
        NodeUpdate(label="")


def test_node_update_oversized_properties_rejected() -> None:
    big = {"key": "x" * (64 * 1024)}
    with pytest.raises(ValidationError) as exc_info:
        NodeUpdate(properties=big)
    errors = exc_info.value.errors()
    assert any("64 KB" in str(e["msg"]) for e in errors)


# ---------------------------------------------------------------------------
# Node round-trip from ORM attributes
# ---------------------------------------------------------------------------


def test_node_model_validate_from_orm() -> None:
    orm_row = _make_node_orm()
    node = Node.model_validate(orm_row)
    assert node.id == _UUID
    assert node.engagement_id == _UUID2
    assert node.type == NodeType.host
    assert node.label == "10.0.0.1"
    assert node.properties == {"os": "linux"}
    assert node.deleted is False
    assert node.created_at == _NOW
    assert node.updated_at == _NOW


def test_node_model_validate_deleted_node() -> None:
    orm_row = _make_node_orm(deleted=True, label="gone")
    node = Node.model_validate(orm_row)
    assert node.deleted is True
    assert node.label == "gone"


def test_node_model_validate_all_node_types() -> None:
    for nt in NodeType:
        orm_row = _make_node_orm(type=nt.value, label=f"test-{nt.value}")
        node = Node.model_validate(orm_row)
        assert node.type == nt


# ---------------------------------------------------------------------------
# EdgeCreate
# ---------------------------------------------------------------------------


def test_edge_create_valid() -> None:
    ec = EdgeCreate(source_id=_UUID, target_id=_UUID2, relation="runs")
    assert ec.relation == "runs"
    assert ec.properties == {}


def test_edge_create_empty_relation_rejected() -> None:
    with pytest.raises(ValidationError):
        EdgeCreate(source_id=_UUID, target_id=_UUID2, relation="")


def test_edge_create_relation_too_long_rejected() -> None:
    with pytest.raises(ValidationError):
        EdgeCreate(source_id=_UUID, target_id=_UUID2, relation="r" * 129)


def test_edge_create_oversized_properties_rejected() -> None:
    big = {"key": "x" * (64 * 1024)}
    with pytest.raises(ValidationError) as exc_info:
        EdgeCreate(source_id=_UUID, target_id=_UUID2, relation="runs", properties=big)
    errors = exc_info.value.errors()
    assert any("64 KB" in str(e["msg"]) for e in errors)


# ---------------------------------------------------------------------------
# Edge round-trip from ORM attributes
# ---------------------------------------------------------------------------


def test_edge_model_validate_from_orm() -> None:
    edge_id = uuid.uuid4()
    eng_id = uuid.uuid4()
    orm_row = SimpleNamespace(
        id=edge_id,
        engagement_id=eng_id,
        source_id=_UUID,
        target_id=_UUID2,
        relation="runs",
        properties={"weight": 1},
        deleted=False,
        created_at=_NOW,
        updated_at=_NOW,
    )
    edge = Edge.model_validate(orm_row)
    assert edge.id == edge_id
    assert edge.relation == "runs"
    assert edge.properties == {"weight": 1}
    assert edge.deleted is False


# ---------------------------------------------------------------------------
# NodeHistoryEntry
# ---------------------------------------------------------------------------


def test_node_history_entry_from_orm() -> None:
    history_id = uuid.uuid4()
    node_id = uuid.uuid4()
    orm_row = SimpleNamespace(
        id=history_id,
        entity_id=node_id,
        label="old-label",
        properties={"os": "windows"},
        deleted=False,
        recorded_at=_NOW,
    )
    entry = NodeHistoryEntry.model_validate(orm_row)
    assert entry.id == history_id
    assert entry.entity_id == node_id
    assert entry.label == "old-label"
    assert entry.recorded_at == _NOW


# ---------------------------------------------------------------------------
# GraphSnapshot and GraphHistory
# ---------------------------------------------------------------------------


def test_graph_snapshot_empty() -> None:
    snap = GraphSnapshot(nodes=[], edges=[])
    assert snap.nodes == []
    assert snap.edges == []


def test_graph_history_empty() -> None:
    gh = GraphHistory(deleted_nodes=[], node_history=[])
    assert gh.deleted_nodes == []
    assert gh.node_history == []
