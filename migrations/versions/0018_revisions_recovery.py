"""Versioned scene snapshots and audited task recovery."""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "video_id", sa.Uuid(), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "producing",
                "ready",
                "cancelled",
                name="revision_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "base_render_id",
            sa.Uuid(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("render_task_id", sa.Uuid(), sa.ForeignKey("tasks.id", ondelete="SET NULL")),
        sa.Column("final_asset_id", sa.Uuid(), sa.ForeignKey("assets.id", ondelete="SET NULL")),
        sa.Column("scenes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("video_id", "number", name="uq_revision_number"),
        sa.CheckConstraint("number >= 1", name="ck_revision_number"),
    )
    op.create_index("ix_video_revisions_video_id", "video_revisions", ["video_id"])
    op.create_table(
        "task_recoveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "task_id", sa.Uuid(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "number", name="uq_recovery_number"),
    )
    op.create_index("ix_task_recoveries_task_id", "task_recoveries", ["task_id"])


def downgrade() -> None:
    op.drop_table("task_recoveries")
    op.drop_table("video_revisions")
