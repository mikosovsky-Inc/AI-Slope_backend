"""Persist job correlation and safe error classification."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("request_id", sa.String(64), nullable=True))
    op.add_column("tasks", sa.Column("error_category", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "error_category")
    op.drop_column("tasks", "request_id")
