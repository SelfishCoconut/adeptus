"""Service tests for the graph feature (task 5).

The writer and repository (get_engagement_for_member) are fully mocked with
AsyncMock / MagicMock so these tests have no database or event-loop-task
dependency.  Covers the invariants required by the slice spec:

  - test_write_non_member_returns_404
  - test_write_archived_engagement_returns_409
  - test_read_archived_engagement_allowed
  - test_create_edge_missing_endpoint_404
  - test_undo_no_history_404

Mocking strategy (mirrors engagements/tests/test_service.py):
  - ``db``: a bare AsyncMock (the service only passes it to the mocked
    eng_repo.get_engagement_for_member — no real SQL is executed).
  - ``engagements.repository.get_engagement_for_member``: patched via
    ``unittest.mock.patch`` to return the desired (Engagement, Member) tuple
    or None.
  - ``graph.writer.*``: patched to return schema objects or raise domain errors.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.features.graph import service
from app.features.graph.errors import (
    DuplicateEdge,
    EngagementArchived,
    NodeNotFound,
    NoHistory,
)
from app.features.graph.schemas import (
    Edge,
    EdgeCreate,
    GraphSnapshot,
    Node,
    NodeCreate,
    NodeType,
    NodeUpdate,
)
from app.features.graph.service import EngagementNotFound

# ---------------------------------------------------------------------------
# Helpers — lightweight mock objects
# ---------------------------------------------------------------------------

NOW = datetime(2026, 6, 3, 12, 0, 0, tzinfo=UTC)


def _make_engagement(
    *,
    engagement_id: UUID | None = None,
    status: str = "active",
) -> MagicMock:
    eng = MagicMock()
    eng.id = engagement_id or uuid4()
    eng.status = status
    return eng


def _make_member(*, engagement_id: UUID | None = None, user_id: UUID | None = None) -> MagicMock:
    m = MagicMock()
    m.engagement_id = engagement_id or uuid4()
    m.user_id = user_id or uuid4()
    m.role = "member"
    return m


def _make_node(
    *,
    node_id: UUID | None = None,
    engagement_id: UUID | None = None,
    deleted: bool = False,
) -> Node:
    eid = engagement_id or uuid4()
    return Node(
        id=node_id or uuid4(),
        engagement_id=eid,
        type=NodeType.host,
        label="10.0.0.1",
        properties={},
        deleted=deleted,
        created_at=NOW,
        updated_at=NOW,
    )


def _make_edge(
    *,
    edge_id: UUID | None = None,
    engagement_id: UUID | None = None,
    source_id: UUID | None = None,
    target_id: UUID | None = None,
) -> Edge:
    return Edge(
        id=edge_id or uuid4(),
        engagement_id=engagement_id or uuid4(),
        source_id=source_id or uuid4(),
        target_id=target_id or uuid4(),
        relation="runs",
        properties={},
        deleted=False,
        created_at=NOW,
        updated_at=NOW,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db() -> AsyncMock:
    return AsyncMock()


# ---------------------------------------------------------------------------
# Membership chokepoint — 404 for non-members and missing engagements
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_non_member_returns_404(db: AsyncMock) -> None:
    """Any write on an engagement where the caller is not a member raises
    EngagementNotFound (→404), regardless of whether the engagement exists."""
    engagement_id = uuid4()
    user_id = uuid4()
    payload = NodeCreate(type=NodeType.host, label="target")

    with patch(
        "app.features.graph.service.eng_repo.get_engagement_for_member",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(EngagementNotFound):
            await service.create_node(db, engagement_id, user_id, payload)


@pytest.mark.asyncio
async def test_read_non_member_returns_404(db: AsyncMock) -> None:
    """get_graph also raises EngagementNotFound for non-members (read path, same gate)."""
    engagement_id = uuid4()
    user_id = uuid4()

    with patch(
        "app.features.graph.service.eng_repo.get_engagement_for_member",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(EngagementNotFound):
            await service.get_graph(db, engagement_id, user_id)


# ---------------------------------------------------------------------------
# Archived guard — 409 on writes, allowed on reads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_archived_engagement_returns_409(db: AsyncMock) -> None:
    """Writes against an archived engagement raise EngagementArchived (→409)."""
    engagement_id = uuid4()
    user_id = uuid4()
    eng = _make_engagement(engagement_id=engagement_id, status="archived")
    member = _make_member(engagement_id=engagement_id, user_id=user_id)
    payload = NodeCreate(type=NodeType.host, label="target")

    with patch(
        "app.features.graph.service.eng_repo.get_engagement_for_member",
        new=AsyncMock(return_value=(eng, member)),
    ):
        with pytest.raises(EngagementArchived):
            await service.create_node(db, engagement_id, user_id, payload)


@pytest.mark.asyncio
async def test_write_archived_update_node_returns_409(db: AsyncMock) -> None:
    """update_node also rejects archived engagement writes."""
    engagement_id = uuid4()
    user_id = uuid4()
    node_id = uuid4()
    eng = _make_engagement(engagement_id=engagement_id, status="archived")
    member = _make_member(engagement_id=engagement_id, user_id=user_id)
    payload = NodeUpdate(label="new-label")

    with patch(
        "app.features.graph.service.eng_repo.get_engagement_for_member",
        new=AsyncMock(return_value=(eng, member)),
    ):
        with pytest.raises(EngagementArchived):
            await service.update_node(db, engagement_id, node_id, user_id, payload)


@pytest.mark.asyncio
async def test_read_archived_engagement_allowed(db: AsyncMock) -> None:
    """get_graph on an archived engagement succeeds (reads are allowed, §4)."""
    engagement_id = uuid4()
    user_id = uuid4()
    eng = _make_engagement(engagement_id=engagement_id, status="archived")
    member = _make_member(engagement_id=engagement_id, user_id=user_id)
    snapshot = GraphSnapshot(nodes=[], edges=[])

    with (
        patch(
            "app.features.graph.service.eng_repo.get_engagement_for_member",
            new=AsyncMock(return_value=(eng, member)),
        ),
        patch(
            "app.features.graph.service.writer.read_graph",
            new=AsyncMock(return_value=snapshot),
        ),
    ):
        result = await service.get_graph(db, engagement_id, user_id)

    assert result == snapshot


# ---------------------------------------------------------------------------
# create_edge — endpoint validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_edge_missing_source_404(db: AsyncMock) -> None:
    """create_edge raises NodeNotFound (→404) when source node is missing."""
    engagement_id = uuid4()
    user_id = uuid4()
    eng = _make_engagement(engagement_id=engagement_id, status="active")
    member = _make_member(engagement_id=engagement_id, user_id=user_id)

    # Only the target node exists live; source is absent from the snapshot.
    target_node = _make_node(engagement_id=engagement_id)
    full_snapshot = GraphSnapshot(nodes=[target_node], edges=[])
    payload = EdgeCreate(
        source_id=uuid4(),  # unknown id — not in snapshot
        target_id=target_node.id,
        relation="runs",
    )

    with (
        patch(
            "app.features.graph.service.eng_repo.get_engagement_for_member",
            new=AsyncMock(return_value=(eng, member)),
        ),
        patch(
            "app.features.graph.service.writer.read_full",
            new=AsyncMock(return_value=full_snapshot),
        ),
    ):
        with pytest.raises(NodeNotFound):
            await service.create_edge(db, engagement_id, user_id, payload)


@pytest.mark.asyncio
async def test_create_edge_missing_target_404(db: AsyncMock) -> None:
    """create_edge raises NodeNotFound (→404) when target node is missing."""
    engagement_id = uuid4()
    user_id = uuid4()
    eng = _make_engagement(engagement_id=engagement_id, status="active")
    member = _make_member(engagement_id=engagement_id, user_id=user_id)

    source_node = _make_node(engagement_id=engagement_id)
    full_snapshot = GraphSnapshot(nodes=[source_node], edges=[])
    payload = EdgeCreate(
        source_id=source_node.id,
        target_id=uuid4(),  # unknown — not in snapshot
        relation="runs",
    )

    with (
        patch(
            "app.features.graph.service.eng_repo.get_engagement_for_member",
            new=AsyncMock(return_value=(eng, member)),
        ),
        patch(
            "app.features.graph.service.writer.read_full",
            new=AsyncMock(return_value=full_snapshot),
        ),
    ):
        with pytest.raises(NodeNotFound):
            await service.create_edge(db, engagement_id, user_id, payload)


@pytest.mark.asyncio
async def test_create_edge_missing_endpoint_404(db: AsyncMock) -> None:
    """create_edge raises NodeNotFound when a deleted node is used as an endpoint.

    A soft-deleted node is present in the full snapshot but has deleted=True, so
    it must NOT count as a valid live endpoint.
    """
    engagement_id = uuid4()
    user_id = uuid4()
    eng = _make_engagement(engagement_id=engagement_id, status="active")
    member = _make_member(engagement_id=engagement_id, user_id=user_id)

    deleted_node = _make_node(engagement_id=engagement_id, deleted=True)
    live_node = _make_node(engagement_id=engagement_id, deleted=False)
    full_snapshot = GraphSnapshot(nodes=[deleted_node, live_node], edges=[])
    payload = EdgeCreate(
        source_id=deleted_node.id,  # deleted — not valid
        target_id=live_node.id,
        relation="runs",
    )

    with (
        patch(
            "app.features.graph.service.eng_repo.get_engagement_for_member",
            new=AsyncMock(return_value=(eng, member)),
        ),
        patch(
            "app.features.graph.service.writer.read_full",
            new=AsyncMock(return_value=full_snapshot),
        ),
    ):
        with pytest.raises(NodeNotFound):
            await service.create_edge(db, engagement_id, user_id, payload)


# ---------------------------------------------------------------------------
# undo_node / undo_edge — NoHistory propagation → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_undo_no_history_404(db: AsyncMock) -> None:
    """undo_node propagates NoHistory (→404) from the writer consumer unchanged."""
    engagement_id = uuid4()
    user_id = uuid4()
    node_id = uuid4()
    eng = _make_engagement(engagement_id=engagement_id, status="active")
    member = _make_member(engagement_id=engagement_id, user_id=user_id)

    with (
        patch(
            "app.features.graph.service.eng_repo.get_engagement_for_member",
            new=AsyncMock(return_value=(eng, member)),
        ),
        patch(
            "app.features.graph.service.writer.submit_undo_node",
            new=AsyncMock(side_effect=NoHistory("No prior state for node")),
        ),
    ):
        with pytest.raises(NoHistory):
            await service.undo_node(db, engagement_id, node_id, user_id)


@pytest.mark.asyncio
async def test_undo_edge_no_history_404(db: AsyncMock) -> None:
    """undo_edge propagates NoHistory (→404) from the writer consumer unchanged."""
    engagement_id = uuid4()
    user_id = uuid4()
    edge_id = uuid4()
    eng = _make_engagement(engagement_id=engagement_id, status="active")
    member = _make_member(engagement_id=engagement_id, user_id=user_id)

    with (
        patch(
            "app.features.graph.service.eng_repo.get_engagement_for_member",
            new=AsyncMock(return_value=(eng, member)),
        ),
        patch(
            "app.features.graph.service.writer.submit_undo_edge",
            new=AsyncMock(side_effect=NoHistory("No prior state for edge")),
        ),
    ):
        with pytest.raises(NoHistory):
            await service.undo_edge(db, engagement_id, edge_id, user_id)


# ---------------------------------------------------------------------------
# Happy-path delegation — writer is called with correct args
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_node_delegates_to_writer(db: AsyncMock) -> None:
    """create_node delegates to writer.submit_create_node and returns its result."""
    engagement_id = uuid4()
    user_id = uuid4()
    eng = _make_engagement(engagement_id=engagement_id, status="active")
    member = _make_member(engagement_id=engagement_id, user_id=user_id)
    expected_node = _make_node(engagement_id=engagement_id)
    payload = NodeCreate(type=NodeType.host, label="10.0.0.5", properties={"os": "linux"})

    with (
        patch(
            "app.features.graph.service.eng_repo.get_engagement_for_member",
            new=AsyncMock(return_value=(eng, member)),
        ),
        patch(
            "app.features.graph.service.writer.submit_create_node",
            new=AsyncMock(return_value=expected_node),
        ) as mock_submit,
        patch(
            "app.features.graph.service.repo.push_undo_entry",
            new=AsyncMock(),
        ),
    ):
        result = await service.create_node(db, engagement_id, user_id, payload)

    assert result == expected_node
    mock_submit.assert_called_once_with(
        engagement_id,
        node_type="host",
        label="10.0.0.5",
        properties={"os": "linux"},
    )


@pytest.mark.asyncio
async def test_delete_node_archived_returns_409(db: AsyncMock) -> None:
    """delete_node raises EngagementArchived (→409) on an archived engagement."""
    engagement_id = uuid4()
    user_id = uuid4()
    node_id = uuid4()
    eng = _make_engagement(engagement_id=engagement_id, status="archived")
    member = _make_member(engagement_id=engagement_id, user_id=user_id)

    with patch(
        "app.features.graph.service.eng_repo.get_engagement_for_member",
        new=AsyncMock(return_value=(eng, member)),
    ):
        with pytest.raises(EngagementArchived):
            await service.delete_node(db, engagement_id, node_id, user_id)


@pytest.mark.asyncio
async def test_create_edge_succeeds_with_live_nodes(db: AsyncMock) -> None:
    """create_edge succeeds when both source and target are live nodes."""
    engagement_id = uuid4()
    user_id = uuid4()
    eng = _make_engagement(engagement_id=engagement_id, status="active")
    member = _make_member(engagement_id=engagement_id, user_id=user_id)
    source_node = _make_node(engagement_id=engagement_id)
    target_node = _make_node(engagement_id=engagement_id)
    full_snapshot = GraphSnapshot(nodes=[source_node, target_node], edges=[])
    expected_edge = _make_edge(
        engagement_id=engagement_id,
        source_id=source_node.id,
        target_id=target_node.id,
    )
    payload = EdgeCreate(
        source_id=source_node.id,
        target_id=target_node.id,
        relation="runs",
    )

    with (
        patch(
            "app.features.graph.service.eng_repo.get_engagement_for_member",
            new=AsyncMock(return_value=(eng, member)),
        ),
        patch(
            "app.features.graph.service.writer.read_full",
            new=AsyncMock(return_value=full_snapshot),
        ),
        patch(
            "app.features.graph.service.writer.submit_create_edge",
            new=AsyncMock(return_value=expected_edge),
        ) as mock_submit,
        patch(
            "app.features.graph.service.repo.push_undo_entry",
            new=AsyncMock(),
        ),
    ):
        result = await service.create_edge(db, engagement_id, user_id, payload)

    assert result == expected_edge
    mock_submit.assert_called_once_with(
        engagement_id,
        source_id=source_node.id,
        target_id=target_node.id,
        relation="runs",
        properties={},
    )


@pytest.mark.asyncio
async def test_duplicate_edge_409_propagates(db: AsyncMock) -> None:
    """DuplicateEdge raised by the writer propagates unchanged (→409)."""
    engagement_id = uuid4()
    user_id = uuid4()
    eng = _make_engagement(engagement_id=engagement_id, status="active")
    member = _make_member(engagement_id=engagement_id, user_id=user_id)
    source_node = _make_node(engagement_id=engagement_id)
    target_node = _make_node(engagement_id=engagement_id)
    full_snapshot = GraphSnapshot(nodes=[source_node, target_node], edges=[])
    payload = EdgeCreate(
        source_id=source_node.id,
        target_id=target_node.id,
        relation="runs",
    )

    with (
        patch(
            "app.features.graph.service.eng_repo.get_engagement_for_member",
            new=AsyncMock(return_value=(eng, member)),
        ),
        patch(
            "app.features.graph.service.writer.read_full",
            new=AsyncMock(return_value=full_snapshot),
        ),
        patch(
            "app.features.graph.service.writer.submit_create_edge",
            new=AsyncMock(side_effect=DuplicateEdge()),
        ),
    ):
        with pytest.raises(DuplicateEdge):
            await service.create_edge(db, engagement_id, user_id, payload)


# ---------------------------------------------------------------------------
# Personal undo stack (Slice 09) — staleness helper (pure)
# ---------------------------------------------------------------------------


def _entry(*, target_updated_at: datetime, op_type: str = "create_node") -> SimpleNamespace:
    """A minimal stand-in for a GraphUserUndoStack row for the pure helper."""
    return SimpleNamespace(
        id=uuid4(),
        op_type=op_type,
        entity_kind="node",
        entity_id=uuid4(),
        summary="Created host x",
        recorded_at=NOW,
        target_updated_at=target_updated_at,
        undone=False,
    )


def test_is_entry_stale_when_updated_at_differs() -> None:
    """A later write bumps updated_at past the baseline → stale."""
    entry = _entry(target_updated_at=NOW)
    current = SimpleNamespace(updated_at=NOW + timedelta(seconds=1))
    assert service._is_entry_stale(entry, current) is True  # type: ignore[arg-type]


def test_is_entry_fresh_when_unchanged() -> None:
    """No mutation since the write (updated_at == baseline) → fresh."""
    entry = _entry(target_updated_at=NOW)
    current = SimpleNamespace(updated_at=NOW)
    assert service._is_entry_stale(entry, current) is False  # type: ignore[arg-type]


def test_missing_entity_is_stale() -> None:
    """A hard-deleted / missing entity is treated as stale."""
    entry = _entry(target_updated_at=NOW)
    assert service._is_entry_stale(entry, None) is True  # type: ignore[arg-type]


def test_same_user_later_edit_makes_entry_stale() -> None:
    """Decision 1: ANY later write — including the owner's own — makes it stale."""
    entry = _entry(target_updated_at=NOW)
    # The owner edited the same entity again later: updated_at advanced.
    current = SimpleNamespace(updated_at=NOW + timedelta(microseconds=5))
    assert service._is_entry_stale(entry, current) is True  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Personal undo stack (Slice 09) — push on write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_node_pushes_undo_entry(db: AsyncMock) -> None:
    """A successful create_node pushes one create_node entry for the caller."""
    engagement_id, user_id = uuid4(), uuid4()
    eng = _make_engagement(engagement_id=engagement_id, status="active")
    member = _make_member(engagement_id=engagement_id, user_id=user_id)
    node = _make_node(engagement_id=engagement_id)
    payload = NodeCreate(type=NodeType.host, label="10.0.0.5")

    with (
        patch(
            "app.features.graph.service.eng_repo.get_engagement_for_member",
            new=AsyncMock(return_value=(eng, member)),
        ),
        patch(
            "app.features.graph.service.writer.submit_create_node",
            new=AsyncMock(return_value=node),
        ),
        patch("app.features.graph.service.repo.push_undo_entry", new=AsyncMock()) as mock_push,
    ):
        await service.create_node(db, engagement_id, user_id, payload)

    mock_push.assert_awaited_once()
    assert mock_push.await_args is not None
    kwargs = mock_push.await_args.kwargs
    assert kwargs["op_type"] == "create_node"
    assert kwargs["entity_kind"] == "node"
    assert kwargs["entity_id"] == node.id
    assert kwargs["user_id"] == user_id
    assert kwargs["target_updated_at"] == node.updated_at
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_update_node_pushes_undo_entry(db: AsyncMock) -> None:
    """A successful update_node pushes one update_node entry."""
    engagement_id, user_id, node_id = uuid4(), uuid4(), uuid4()
    eng = _make_engagement(engagement_id=engagement_id, status="active")
    member = _make_member(engagement_id=engagement_id, user_id=user_id)
    node = _make_node(node_id=node_id, engagement_id=engagement_id)
    payload = NodeUpdate(label="new-label")

    with (
        patch(
            "app.features.graph.service.eng_repo.get_engagement_for_member",
            new=AsyncMock(return_value=(eng, member)),
        ),
        patch(
            "app.features.graph.service.writer.submit_update_node",
            new=AsyncMock(return_value=node),
        ),
        patch("app.features.graph.service.repo.push_undo_entry", new=AsyncMock()) as mock_push,
    ):
        await service.update_node(db, engagement_id, node_id, user_id, payload)

    assert mock_push.await_args is not None
    kwargs = mock_push.await_args.kwargs
    assert kwargs["op_type"] == "update_node"
    assert kwargs["entity_id"] == node_id


@pytest.mark.asyncio
async def test_delete_node_pushes_undo_entry(db: AsyncMock) -> None:
    """delete_node re-reads the soft-deleted row and pushes a delete_node entry."""
    engagement_id, user_id, node_id = uuid4(), uuid4(), uuid4()
    eng = _make_engagement(engagement_id=engagement_id, status="active")
    member = _make_member(engagement_id=engagement_id, user_id=user_id)
    deleted_row = SimpleNamespace(
        id=node_id, type="host", label="10.0.0.9", updated_at=NOW + timedelta(seconds=2)
    )

    with (
        patch(
            "app.features.graph.service.eng_repo.get_engagement_for_member",
            new=AsyncMock(return_value=(eng, member)),
        ),
        patch("app.features.graph.service.writer.submit_soft_delete_node", new=AsyncMock()),
        patch(
            "app.features.graph.service.repo.get_node",
            new=AsyncMock(return_value=deleted_row),
        ),
        patch("app.features.graph.service.repo.push_undo_entry", new=AsyncMock()) as mock_push,
    ):
        await service.delete_node(db, engagement_id, node_id, user_id)

    assert mock_push.await_args is not None
    kwargs = mock_push.await_args.kwargs
    assert kwargs["op_type"] == "delete_node"
    assert kwargs["entity_id"] == node_id
    assert kwargs["target_updated_at"] == deleted_row.updated_at


@pytest.mark.asyncio
async def test_create_edge_pushes_undo_entry(db: AsyncMock) -> None:
    """A successful create_edge pushes one create_edge entry."""
    engagement_id, user_id = uuid4(), uuid4()
    eng = _make_engagement(engagement_id=engagement_id, status="active")
    member = _make_member(engagement_id=engagement_id, user_id=user_id)
    source, target = (
        _make_node(engagement_id=engagement_id),
        _make_node(engagement_id=engagement_id),
    )
    edge = _make_edge(engagement_id=engagement_id, source_id=source.id, target_id=target.id)
    payload = EdgeCreate(source_id=source.id, target_id=target.id, relation="runs")

    with (
        patch(
            "app.features.graph.service.eng_repo.get_engagement_for_member",
            new=AsyncMock(return_value=(eng, member)),
        ),
        patch(
            "app.features.graph.service.writer.read_full",
            new=AsyncMock(return_value=GraphSnapshot(nodes=[source, target], edges=[])),
        ),
        patch(
            "app.features.graph.service.writer.submit_create_edge",
            new=AsyncMock(return_value=edge),
        ),
        patch("app.features.graph.service.repo.push_undo_entry", new=AsyncMock()) as mock_push,
    ):
        await service.create_edge(db, engagement_id, user_id, payload)

    assert mock_push.await_args is not None
    kwargs = mock_push.await_args.kwargs
    assert kwargs["op_type"] == "create_edge"
    assert kwargs["entity_kind"] == "edge"
    assert kwargs["entity_id"] == edge.id


@pytest.mark.asyncio
async def test_per_entity_undo_does_not_push(db: AsyncMock) -> None:
    """Slice 07 per-entity undo (undo_node) must NOT push onto the personal stack."""
    engagement_id, user_id, node_id = uuid4(), uuid4(), uuid4()
    eng = _make_engagement(engagement_id=engagement_id, status="active")
    member = _make_member(engagement_id=engagement_id, user_id=user_id)
    node = _make_node(node_id=node_id, engagement_id=engagement_id)

    with (
        patch(
            "app.features.graph.service.eng_repo.get_engagement_for_member",
            new=AsyncMock(return_value=(eng, member)),
        ),
        patch(
            "app.features.graph.service.writer.submit_undo_node",
            new=AsyncMock(return_value=node),
        ),
        patch("app.features.graph.service.repo.push_undo_entry", new=AsyncMock()) as mock_push,
    ):
        await service.undo_node(db, engagement_id, node_id, user_id)

    mock_push.assert_not_awaited()
