"""SQLAlchemy ORM models for the findings feature: Finding, FindingHistory
(the two findings_* tables, Slice 19).

A Finding is an ordinary feature-table row that *references* a GraphNode by a
nullable FK (§8.1) — it is NOT a graph entity and never routes through the
single-writer process (Decision 1, see service.py). No provenance columns: the
hash-chained audit log is the source of truth for who changed what (§8.2/§17.4).

Forward-compatibility (Slice 20, §9.1): advanced classifications (CVSS, OWASP
Risk, MITRE ATT&CK) will be added later as *additive* nullable columns / a join
table. This slice ships only the Simple ``severity`` and must not box those out —
hence no composite NOT-NULL or uniqueness that would force a backfill.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func, text

from app.core.db import Base

# Canonical CHECK-constraint vocabularies — mirrored by the Pydantic StrEnums in
# schemas.py and by the slice migration. Kept here so the ORM (and the SQLite test
# DDL) enforce the same domain the Postgres CHECK does.
_SEVERITY_VALUES = ("critical", "high", "medium", "low", "info")
_VERIFICATION_VALUES = ("unverified", "verified", "false_positive")
_REMEDIATION_VALUES = ("open", "fixed", "risk_accepted")


def _check_in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class Finding(Base):
    """A human-authored finding belonging to one engagement (§9.1/§9.2).

    Severity is the single Simple primary classification. Verification and
    remediation are independent free-transition lifecycles (Decision 3 — no
    enforced state machine). ``node_id`` is an optional link to the graph entity
    the finding concerns (§8.1); ON DELETE SET NULL so the finding outlives a
    hard-deleted node (Risk 3).
    """

    __tablename__ = "findings"
    __table_args__ = (
        CheckConstraint(_check_in("severity", _SEVERITY_VALUES), name="ck_findings_severity"),
        CheckConstraint(
            _check_in("verification_status", _VERIFICATION_VALUES),
            name="ck_findings_verification_status",
        ),
        CheckConstraint(
            _check_in("remediation_status", _REMEDIATION_VALUES),
            name="ck_findings_remediation_status",
        ),
        Index("ix_findings_engagement_id", "engagement_id"),
        # Partial index: only live (non-deleted) findings — fast live-list load.
        Index(
            "ix_findings_engagement_live",
            "engagement_id",
            postgresql_where=text("deleted = false"),
        ),
        # Lookup "findings for this node" (Slice 22/34, per-node UI later).
        Index("ix_findings_node_id", "node_id"),
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
    # Optional link to the GraphNode this finding concerns. SET NULL (not CASCADE):
    # hard-deleting a node must not destroy a documented finding (Risk 3).
    node_id: Mapped[UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("graph_nodes.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'unverified'")
    )
    remediation_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'open'")
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


class FindingHistory(Base):
    """Append-only pre-mutation snapshots of Finding state (§8.2 "any ... finding
    can be reverted to a prior state").

    One row is written *before* each mutation, capturing the state a revert would
    restore. This slice ships the table + writes snapshots but adds NO finding
    ``/undo`` endpoint (resolved decision D2): a non-authorship-aware revert would
    let one engagement member silently clobber another's edit. A finding revert
    must arrive later as an authorship-aware revert (Slice 09 pattern, feeding
    Slice 25 retest + Slice 33 replay); history persistence now makes that cheap.
    No provenance columns — the audit log is the source of truth.
    """

    __tablename__ = "finding_history"
    __table_args__ = (
        # Composite index ordered by recorded_at DESC so latest-prior lookup is fast.
        Index("ix_finding_history_finding_id", "finding_id", text("recorded_at DESC")),
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
    finding_id: Mapped[UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("findings.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(16), nullable=False)
    remediation_status: Mapped[str] = mapped_column(String(16), nullable=False)
    node_id: Mapped[UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
