"""slice-16: approval_requests table

Revision ID: daa5fbffc234
Revises: cdfed859bb1f
Create Date: 2026-06-05 21:49:01.673548

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "daa5fbffc234"
down_revision: str | Sequence[str] | None = "cdfed859bb1f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSONB on Postgres; generic JSON on SQLite (mirrors the model's dialect variant).
_JSON = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("engagement_id", sa.UUID(), nullable=False),
        sa.Column("chat_message_id", sa.UUID(), nullable=False),
        sa.Column("initiator_user_id", sa.UUID(), nullable=False),
        sa.Column("server_name", sa.String(length=100), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("args", _JSON, nullable=False),
        sa.Column("preset_name", sa.String(length=100), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("reasons", _JSON, nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("acted_by_user_id", sa.UUID(), nullable=True),
        sa.Column("self_approved", sa.Boolean(), nullable=True),
        sa.Column("tool_run_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_approval_requests_status",
        ),
        sa.ForeignKeyConstraint(["acted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["chat_message_id"], ["chat_messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["engagement_id"], ["engagements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["initiator_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tool_run_id"], ["tool_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_approval_requests_chat_message_id",
        "approval_requests",
        ["chat_message_id"],
        unique=False,
    )
    # The Approvals-tab "pending" query + engagement-scoped newest-first listing. The
    # created_at DESC expression is preserved (autogenerate emits it as a literal_column).
    op.create_index(
        "ix_approval_requests_engagement_status_created",
        "approval_requests",
        ["engagement_id", "status", sa.literal_column("created_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_approval_requests_engagement_status_created", table_name="approval_requests")
    op.drop_index("ix_approval_requests_chat_message_id", table_name="approval_requests")
    op.drop_table("approval_requests")
