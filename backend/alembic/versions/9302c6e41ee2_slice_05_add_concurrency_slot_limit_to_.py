"""slice-05: add concurrency_slot_limit to engagements

Revision ID: 9302c6e41ee2
Revises: 9991fc77d99b
Create Date: 2026-06-03 00:45:59.146526

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9302c6e41ee2"
down_revision: str | Sequence[str] | None = "9991fc77d99b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add concurrency_slot_limit column to engagements.

    Additive migration; existing rows backfill to the default value of 3.
    """
    op.add_column(
        "engagements",
        sa.Column(
            "concurrency_slot_limit",
            sa.SmallInteger(),
            server_default=sa.text("3"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Drop concurrency_slot_limit from engagements."""
    op.drop_column("engagements", "concurrency_slot_limit")
