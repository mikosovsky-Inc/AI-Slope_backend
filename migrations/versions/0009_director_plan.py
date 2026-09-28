"""Persist direction and estimated visual budgets."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scenes", sa.Column("visual_style", sa.String(2000), nullable=False, server_default="")
    )
    op.alter_column("scenes", "visual_style", server_default=None)
    op.add_column("scenes", sa.Column("importance", sa.Float(), nullable=True))
    op.add_column("scenes", sa.Column("generation_priority", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_scene_importance", "scenes", "importance >= 0 AND importance <= 1"
    )
    op.create_check_constraint("ck_scene_priority", "scenes", "generation_priority >= 1")
    op.create_unique_constraint(
        "uq_scene_generation_priority", "scenes", ["script_id", "generation_priority"]
    )
    op.create_table(
        "director_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "video_id", sa.Uuid(), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("visual_budget_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("requested_video_ratio", sa.Float(), nullable=False),
        sa.Column("pricing_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("video_id", name="uq_director_plan_video"),
        sa.CheckConstraint("visual_budget_usd >= 0", name="ck_director_budget"),
        sa.CheckConstraint(
            "estimated_cost_usd >= 0 AND estimated_cost_usd <= visual_budget_usd",
            name="ck_director_estimate",
        ),
        sa.CheckConstraint(
            "requested_video_ratio >= 0 AND requested_video_ratio <= 1", name="ck_director_ratio"
        ),
    )


def downgrade() -> None:
    op.drop_table("director_plans")
    op.drop_constraint("uq_scene_generation_priority", "scenes", type_="unique")
    op.drop_constraint("ck_scene_priority", "scenes", type_="check")
    op.drop_constraint("ck_scene_importance", "scenes", type_="check")
    op.drop_column("scenes", "generation_priority")
    op.drop_column("scenes", "importance")
    op.drop_column("scenes", "visual_style")
