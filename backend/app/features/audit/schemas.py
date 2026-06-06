"""Pydantic v2 schemas for the audit feature: AuditAction, AuditEntryRead, AuditPage.

``AuditContent`` (the value object the hasher consumes) is re-exported from
``hashing`` so callers have a single import surface for the audit feature.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.features.audit.hashing import AuditContent

__all__ = ["AuditAction", "AuditContent", "AuditEntryRead", "AuditPage"]


class AuditAction(StrEnum):
    """The audit action vocabulary (§14). Mirrors ``models.AUDIT_ACTIONS`` exactly —
    guarded by ``test_schemas.test_action_enum_matches_db_vocabulary``.

    ``APPROVAL_GRANTED`` / ``APPROVAL_REJECTED`` (Slice 16, carry ``self_approved``) and
    ``AI_CALL`` (Slice 11+) are reserved here with no caller in this slice.
    """

    LOGIN = "login"
    LOGOUT = "logout"
    LOGIN_FAILED = "login_failed"
    TOOL_RUN = "tool_run"
    TOOL_RUN_COMPLETED = "tool_run_completed"
    GRAPH_NODE_CREATED = "graph_node_created"
    GRAPH_NODE_UPDATED = "graph_node_updated"
    GRAPH_NODE_DELETED = "graph_node_deleted"
    GRAPH_EDGE_CREATED = "graph_edge_created"
    GRAPH_EDGE_DELETED = "graph_edge_deleted"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_REJECTED = "approval_rejected"
    AI_CALL = "ai_call"
    # Slice 18 — standing autonomy: a command auto-approved by an active grant, plus the
    # grant/revoke lifecycle events. ``approval_auto_granted`` carries ``self_approved``.
    APPROVAL_AUTO_GRANTED = "approval_auto_granted"
    AUTONOMY_GRANTED = "autonomy_granted"
    AUTONOMY_REVOKED = "autonomy_revoked"
    # Slice 19 — findings model + lifecycle: one action per finding mutation (§9.1/§9.2/§14).
    FINDING_CREATED = "finding_created"
    FINDING_UPDATED = "finding_updated"
    FINDING_VERIFICATION_CHANGED = "finding_verification_changed"
    FINDING_REMEDIATION_CHANGED = "finding_remediation_changed"
    FINDING_DELETED = "finding_deleted"


class AuditEntryRead(BaseModel):
    """One audit entry as exposed by the read API (newest-first listings)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    seq: int
    action: AuditAction
    actor_user_id: UUID | None
    engagement_id: UUID | None
    target_type: str | None
    target_id: str | None
    self_approved: bool | None
    payload: dict[str, Any]
    created_at: datetime
    prev_hash: str
    entry_hash: str


class AuditPage(BaseModel):
    """A page of audit entries with an opaque cursor for the next (older) page."""

    items: list[AuditEntryRead]
    next_cursor: str | None
