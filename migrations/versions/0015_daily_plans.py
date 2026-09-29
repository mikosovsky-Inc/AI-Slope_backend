"""Durable per-channel daily planner state."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_tasks_channel_kind_status", "tasks", ["channel_id", "kind", "status"])
    op.create_table(
        "daily_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "channel_id",
            sa.Uuid(),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "complete",
                "waiting_approval",
                "waiting_task",
                "blocked",
                name="daily_plan_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.String(100)),
        sa.Column("idea_task_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("channel_id", "day", name="uq_daily_plan_channel_day"),
        sa.CheckConstraint("target BETWEEN 1 AND 24", name="ck_daily_plan_target"),
        sa.CheckConstraint("window_end > window_start", name="ck_daily_plan_window"),
    )
    op.create_index("ix_daily_plans_channel_id", "daily_plans", ["channel_id"])


def downgrade() -> None:
    op.drop_table("daily_plans")
    op.drop_index("ix_tasks_channel_kind_status", table_name="tasks")
