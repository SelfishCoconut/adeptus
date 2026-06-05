"""Unit tests for audit schemas (Slice 10 task 3)."""

from datetime import UTC, datetime
from uuid import uuid4

from app.features.audit.hashing import GENESIS_HASH
from app.features.audit.models import AUDIT_ACTIONS
from app.features.audit.schemas import AuditAction, AuditEntryRead, AuditPage


def test_action_enum_matches_db_vocabulary() -> None:
    # The StrEnum and the DB CHECK-constraint tuple must never drift (same values, order).
    assert tuple(a.value for a in AuditAction) == AUDIT_ACTIONS


def test_reserved_actions_present() -> None:
    for reserved in ("approval_granted", "approval_rejected", "ai_call"):
        assert reserved in {a.value for a in AuditAction}


class _Row:
    """Stand-in for an ORM AuditEntry (exercises from_attributes)."""

    def __init__(self) -> None:
        self.id = uuid4()
        self.seq = 7
        self.action = "graph_node_created"
        self.actor_user_id = uuid4()
        self.engagement_id = uuid4()
        self.target_type = "node"
        self.target_id = str(uuid4())
        self.self_approved = None
        self.payload = {"label": "10.0.0.5"}
        self.created_at = datetime(2026, 6, 5, tzinfo=UTC)
        self.prev_hash = GENESIS_HASH
        self.entry_hash = "a" * 64


def test_audit_entry_read_from_orm_object() -> None:
    read = AuditEntryRead.model_validate(_Row())
    assert read.action is AuditAction.GRAPH_NODE_CREATED
    assert read.payload == {"label": "10.0.0.5"}
    assert read.self_approved is None


def test_audit_page_shape() -> None:
    page = AuditPage(items=[AuditEntryRead.model_validate(_Row())], next_cursor="abc")
    assert len(page.items) == 1
    assert page.next_cursor == "abc"
    assert AuditPage(items=[], next_cursor=None).next_cursor is None
