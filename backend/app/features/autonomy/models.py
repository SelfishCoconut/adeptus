"""SQLAlchemy ORM model for the autonomy feature: AutonomyGrant (Slice 18).

A *standing-autonomy grant* is a per-engagement, per-reason-category delegation (§5.2):
once granted, the AI auto-approves future gated commands whose classification reasons are
**all** covered by active grants, with no human click. Grants are engagement-shared (any
member may grant/revoke, §5.2) and revocable; ``revoked_at IS NULL`` means active.

The **audit log** remains the source of truth for every grant, revoke, and auto-approval
(§14/§17.4); the columns here are the grant's own ownership/lifecycle state for live
rendering, not a provenance smear on a shared entity.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from app.core.db import Base

# The delegable gate-reason categories (§5.2). ``unclassified_manifest`` is **never**
# delegable — the un-manifested-tool fail-safe must always gate (Slice 16 escape hatch).
# This is the delegable subset of ``ApprovalReason``; ``test_schemas`` guards that this
# tuple == every ApprovalReason value except ``unclassified_manifest`` (no silent drift).
DELEGABLE_REASONS: tuple[str, ...] = (
    "target_write",
    "aggressive_scan",
    "credential_attack",
    "out_of_scope",
)

_REASON_CHECK_SQL = "reason IN (" + ", ".join(f"'{r}'" for r in DELEGABLE_REASONS) + ")"


class AutonomyGrant(Base):
    """One standing-autonomy grant for a (engagement, reason) pair.

    Lifecycle: created active (``revoked_at IS NULL``) → revoked (``revoked_at`` set). A
    partial unique index enforces at most ONE active grant per (engagement, reason), so a
    re-grant of an already-active category is a 409, and revoke + re-grant is allowed.
    """

    __tablename__ = "autonomy_grants"
    __table_args__ = (
        CheckConstraint(_REASON_CHECK_SQL, name="ck_autonomy_grants_reason"),
        # At most one ACTIVE grant per (engagement, reason). Partial unique index works on
        # both Postgres (prod/migrations) and SQLite (in-memory unit tests).
        Index(
            "uq_autonomy_grants_active_reason",
            "engagement_id",
            "reason",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
            sqlite_where=text("revoked_at IS NULL"),
        ),
        # List a turn's / panel's active grants for an engagement.
        Index("ix_autonomy_grants_engagement", "engagement_id"),
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
    # One of DELEGABLE_REASONS.
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    # The member who granted it. ON DELETE SET NULL so a deleted grantor does not erase the
    # grant — the audit log keeps the immutable hashed attribution (§17.4).
    granted_by_user_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    revoked_by_user_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
