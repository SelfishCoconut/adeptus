"""slice-17: approval_requests scope-context columns

Adds the two render-only soft-scope columns to approval_requests so an
out_of_scope approval card can show why a target is out of scope. Both are
nullable and null for every non-out_of_scope request (Slice-16 rows unchanged).
No audit-schema change — the out_of_scope reason rides the existing payload.

Revision ID: c3e8a7b21f04
Revises: daa5fbffc234
Create Date: 2026-06-06 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3e8a7b21f04"
down_revision: str | Sequence[str] | None = "daa5fbffc234"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "approval_requests",
        sa.Column("out_of_scope_host", sa.String(length=253), nullable=True),
    )
    op.add_column(
        "approval_requests",
        sa.Column("scope_checked_against", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("approval_requests", "scope_checked_against")
    op.drop_column("approval_requests", "out_of_scope_host")
