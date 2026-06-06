"""Slice 18: autonomy_grants table + standing-autonomy audit actions

Adds the ``autonomy_grants`` table (per-engagement, per-reason standing-autonomy grants,
with a partial unique index enforcing one ACTIVE grant per category) and expands the
``audit_entries.action`` CHECK with the three Slice-18 actions (``approval_auto_granted``,
``autonomy_granted``, ``autonomy_revoked``).

Revision ID: b7f4c2a9e150
Revises: c3e8a7b21f04
Create Date: 2026-06-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b7f4c2a9e150"
down_revision: str | Sequence[str] | None = "c3e8a7b21f04"
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
)
_ACTIONS_AFTER = (
    *_ACTIONS_BEFORE,
    "approval_auto_granted",
    "autonomy_granted",
    "autonomy_revoked",
)


def _action_check(actions: Sequence[str]) -> str:
    return "action IN (" + ", ".join(f"'{a}'" for a in actions) + ")"


def upgrade() -> None:
    op.create_table(
        "autonomy_grants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("engagement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("granted_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revoked_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "reason IN ('target_write', 'aggressive_scan', 'credential_attack', 'out_of_scope')",
            name="ck_autonomy_grants_reason",
        ),
        sa.ForeignKeyConstraint(["engagement_id"], ["engagements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["granted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["revoked_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_autonomy_grants_engagement", "autonomy_grants", ["engagement_id"])
    # Partial unique index: at most one ACTIVE grant per (engagement, reason).
    op.create_index(
        "uq_autonomy_grants_active_reason",
        "autonomy_grants",
        ["engagement_id", "reason"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    # Expand the audit action CHECK with the Slice-18 actions.
    op.drop_constraint("ck_audit_entries_action", "audit_entries", type_="check")
    op.create_check_constraint(
        "ck_audit_entries_action", "audit_entries", _action_check(_ACTIONS_AFTER)
    )


def downgrade() -> None:
    op.drop_constraint("ck_audit_entries_action", "audit_entries", type_="check")
    op.create_check_constraint(
        "ck_audit_entries_action", "audit_entries", _action_check(_ACTIONS_BEFORE)
    )

    op.drop_index("uq_autonomy_grants_active_reason", table_name="autonomy_grants")
    op.drop_index("ix_autonomy_grants_engagement", table_name="autonomy_grants")
    op.drop_table("autonomy_grants")
