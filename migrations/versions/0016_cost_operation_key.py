"""Idempotent cost reservations for provider operations."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cost_events", sa.Column("operation_key", sa.String(200), nullable=True))
    op.create_unique_constraint(
        "uq_cost_video_operation", "cost_events", ["video_id", "operation_key"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_cost_video_operation", "cost_events", type_="unique")
    op.drop_column("cost_events", "operation_key")
