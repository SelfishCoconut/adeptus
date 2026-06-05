"""SQLAlchemy ORM model for the approvals feature: ApprovalRequest (Slice 16).

One row per AI-proposed *dangerous* command. The request is **engagement-shared**
(NOT user-private like ``chat_messages``) so any engagement member can approve/reject
it (§5.2). It links back to the initiating assistant turn (``chat_message_id``) so the
initiator's private chat renders the inline card, and to the executed ``tool_run`` once
approved. Decision attribution (``acted_by_user_id`` / ``self_approved``) is denormalized
here for live rendering; the **audit log** remains the source of truth (§8.2 / §17.4) —
this is the approval request's own ownership concept, NOT a provenance tag bolted onto a
shared entity.

No columns are added to any existing table: the chat-turn → approval link is a column on
*this* table, not on ``chat_messages``.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from app.core.db import Base

# JSONB on Postgres (production + migrations); generic JSON on SQLite so the in-memory
# unit-test engine can render the DDL. Same idiom as audit/graph/mcp models.
_JSON = JSONB().with_variant(JSON(), "sqlite")

# Canonical, DB-level source of truth for the approval-status vocabulary. The Pydantic
# ``ApprovalStatus`` StrEnum in schemas.py is checked against this tuple (test_schemas)
# so the enum and the CHECK constraint can never silently drift — same guard idiom as
# the audit feature's ``AUDIT_ACTIONS``.
APPROVAL_STATUSES: tuple[str, ...] = ("pending", "approved", "rejected")

_STATUS_CHECK_SQL = "status IN (" + ", ".join(f"'{s}'" for s in APPROVAL_STATUSES) + ")"


class ApprovalRequest(Base):
    """One AI-proposed dangerous command awaiting (or having received) a decision.

    State machine: ``pending → approved`` (then the command executes via the existing
    tool-run pipeline) | ``pending → rejected`` (never executes). Terminal states are
    immutable; the ``pending → approved|rejected`` transition is a guarded conditional
    UPDATE (``WHERE status='pending'``) so a double-/concurrent-decision can never run
    the command twice (Risk 1).
    """

    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(_STATUS_CHECK_SQL, name="ck_approval_requests_status"),
        # The Approvals tab's "pending" query + engagement-scoped newest-first listing.
        Index(
            "ix_approval_requests_engagement_status_created",
            "engagement_id",
            "status",
            text("created_at DESC"),
        ),
        # Render a chat turn's inline cards (join approval_requests on chat_message_id).
        Index("ix_approval_requests_chat_message_id", "chat_message_id"),
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
    # The assistant turn that proposed this command (drives the inline card). The
    # request's link to its origin turn — NOT a provenance column on chat_messages.
    chat_message_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The chat owner who proposed it; used to compute self_approved and attribute the
    # executed run (Resolved decision 3). An ownership concept like chat_messages.user_id.
    initiator_user_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    server_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Proposed args, verbatim — no redaction (§5.5).
    args: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False)
    preset_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The list of ApprovalReason values (§5.2 + unclassified_manifest). Non-empty.
    reasons: Mapped[list[str]] = mapped_column(_JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text("'pending'"),
    )
    # The member who approved/rejected; ON DELETE SET NULL so a deleted decider does not
    # erase the request — the audit log keeps the immutable hashed attribution (§17.4).
    acted_by_user_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # acted_by_user_id == initiator_user_id; null while pending. Mirrors the §5.2 audit
    # column for convenient live rendering (the audit row is still the source of truth).
    self_approved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    tool_run_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tool_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
