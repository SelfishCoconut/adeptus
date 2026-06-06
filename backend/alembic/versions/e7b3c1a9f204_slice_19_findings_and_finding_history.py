"""Slice 19: findings + finding_history tables + finding_* audit actions

Creates the ``findings`` table (Simple severity + verification/remediation
lifecycle, optional node_id FK with ON DELETE SET NULL so a finding outlives a
hard-deleted node) and the append-only ``finding_history`` snapshot table, and
widens the ``audit_entries.action`` CHECK with the five Slice-19 actions
(``finding_created``, ``finding_updated``, ``finding_verification_changed``,
``finding_remediation_changed``, ``finding_deleted``).

The CHECK widening only drops + recreates the constraint — no audit rows are
rewritten and no hashes are recomputed, so the tamper-evident chain is intact
across the migration (§14 / Slice 19 Risk 2).

Revision ID: e7b3c1a9f204
Revises: b7f4c2a9e150
Create Date: 2026-06-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7b3c1a9f204"
down_revision: str | Sequence[str] | None = "b7f4c2a9e150"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Audit action vocabularies, hardcoded so the migration is self-contained (the live
# AUDIT_ACTIONS tuple in models.py keeps evolving; a migration must not depend on it).
_ACTIONS_BEFORE = (
    "login",
    "logout",
    "login_failed",
    "tool_run",
    "tool_run_completed",
    "graph_node_created",
    "graph_node_updated",
    "graph_node_deleted",
    "graph_edge_created",
    "graph_edge_deleted",
    "approval_granted",
    "approval_rejected",
    "ai_call",
    "approval_auto_granted",
    "autonomy_granted",
    "autonomy_revoked",
)
_ACTIONS_AFTER = (
    *_ACTIONS_BEFORE,
    "finding_created",
    "finding_updated",
    "finding_verification_changed",
    "finding_remediation_changed",
    "finding_deleted",
)


def _action_check(actions: Sequence[str]) -> str:
    return "action IN (" + ", ".join(f"'{a}'" for a in actions) + ")"


def upgrade() -> None:
    op.create_table(
        "findings",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("engagement_id", sa.UUID(), nullable=False),
        sa.Column("node_id", sa.UUID(), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column(
            "verification_status",
            sa.String(length=16),
            server_default=sa.text("'unverified'"),
            nullable=False,
        ),
        sa.Column(
            "remediation_status",
            sa.String(length=16),
            server_default=sa.text("'open'"),
            nullable=False,
        ),
        sa.Column("deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'low', 'info')",
            name="ck_findings_severity",
        ),
        sa.CheckConstraint(
            "verification_status IN ('unverified', 'verified', 'false_positive')",
            name="ck_findings_verification_status",
        ),
        sa.CheckConstraint(
            "remediation_status IN ('open', 'fixed', 'risk_accepted')",
            name="ck_findings_remediation_status",
        ),
        sa.ForeignKeyConstraint(["engagement_id"], ["engagements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_id"], ["graph_nodes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_findings_engagement_id", "findings", ["engagement_id"], unique=False)
    op.create_index(
        "ix_findings_engagement_live",
        "findings",
        ["engagement_id"],
        unique=False,
        postgresql_where=sa.text("deleted = false"),
    )
    op.create_index("ix_findings_node_id", "findings", ["node_id"], unique=False)

    op.create_table(
        "finding_history",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("engagement_id", sa.UUID(), nullable=False),
        sa.Column("finding_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("verification_status", sa.String(length=16), nullable=False),
        sa.Column("remediation_status", sa.String(length=16), nullable=False),
        sa.Column("node_id", sa.UUID(), nullable=True),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["engagement_id"], ["engagements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["finding_id"], ["findings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_finding_history_finding_id",
        "finding_history",
        ["finding_id", sa.literal_column("recorded_at DESC")],
        unique=False,
    )

    # Widen the audit action CHECK with the Slice-19 finding_* actions.
    op.drop_constraint("ck_audit_entries_action", "audit_entries", type_="check")
    op.create_check_constraint(
        "ck_audit_entries_action", "audit_entries", _action_check(_ACTIONS_AFTER)
    )


def downgrade() -> None:
    op.drop_constraint("ck_audit_entries_action", "audit_entries", type_="check")
    op.create_check_constraint(
        "ck_audit_entries_action", "audit_entries", _action_check(_ACTIONS_BEFORE)
    )

    op.drop_index("ix_finding_history_finding_id", table_name="finding_history")
    op.drop_table("finding_history")
    op.drop_index("ix_findings_engagement_live", table_name="findings")
    op.drop_index("ix_findings_node_id", table_name="findings")
    op.drop_index("ix_findings_engagement_id", table_name="findings")
    op.drop_table("findings")
