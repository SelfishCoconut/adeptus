"""slice-04: add preset_name and status to tool_runs

Revision ID: 9991fc77d99b
Revises: de0c0fb1d2ce
Create Date: 2026-06-02 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9991fc77d99b"
down_revision: str | Sequence[str] | None = "de0c0fb1d2ce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add preset_name and status columns to tool_runs.

    Additive migration; existing rows default to 'completed'.
    """
    op.add_column(
        "tool_runs",
        sa.Column("preset_name", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "tool_runs",
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'completed'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Drop preset_name and status columns from tool_runs."""
    op.drop_column("tool_runs", "status")
    op.drop_column("tool_runs", "preset_name")
