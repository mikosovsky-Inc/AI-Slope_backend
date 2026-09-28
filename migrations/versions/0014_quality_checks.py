"""Persist quality reports and bounded scene repair progress."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

KINDS = (
    "'analyze','ideas','competitors','story','research','top5','direct',"
    "'image','video','audio','render'"
)


def upgrade() -> None:
    op.drop_constraint("task_kind", "tasks", type_="check")
    op.create_check_constraint("task_kind", "tasks", f"kind IN ({KINDS},'quality')")
    op.create_table(
        "quality_checks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "video_id", sa.Uuid(), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "render_task_id",
            sa.Uuid(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id", sa.Uuid(), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("final_asset_id", sa.Uuid(), sa.ForeignKey("assets.id", ondelete="SET NULL")),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "checking",
                "passed",
                "repairing",
                "repaired",
                "failed",
                name="quality_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("policy_json", sa.JSON(), nullable=False),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.Column("repair_tasks", sa.JSON(), nullable=False),
        sa.Column("rerender_task_id", sa.Uuid(), sa.ForeignKey("tasks.id", ondelete="SET NULL")),
        sa.Column("error", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("render_task_id", name="uq_quality_render"),
        sa.UniqueConstraint("task_id", name="uq_quality_task"),
        sa.UniqueConstraint("video_id", "attempt", name="uq_quality_video_attempt"),
        sa.CheckConstraint("attempt >= 1", name="ck_quality_attempt"),
        sa.CheckConstraint(
            "(status IN ('checking', 'repairing') AND completed_at IS NULL) OR "
            "(status IN ('passed', 'repaired', 'failed') AND completed_at IS NOT NULL "
            "AND completed_at >= created_at)",
            name="ck_quality_timestamps",
        ),
    )
    op.create_index("ix_quality_checks_video_id", "quality_checks", ["video_id"])


def downgrade() -> None:
    op.drop_table("quality_checks")
    # Existing quality tasks must be explicitly archived/removed before downgrade.
    op.drop_constraint("task_kind", "tasks", type_="check")
    op.create_check_constraint("task_kind", "tasks", f"kind IN ({KINDS})")
