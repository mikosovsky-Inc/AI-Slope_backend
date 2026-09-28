"""Store TTS cost estimates and reported usage."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cost_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "video_id", sa.Uuid(), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "channel_id",
            sa.Uuid(),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "generation_job_id",
            sa.Uuid(),
            sa.ForeignKey("generation_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("operation", sa.String(100), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("actual_cost_usd", sa.Numeric(12, 6), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("generation_job_id", name="uq_cost_generation_job"),
        sa.CheckConstraint("estimated_cost_usd >= 0", name="ck_cost_estimate"),
        sa.CheckConstraint("actual_cost_usd >= 0", name="ck_cost_actual"),
    )
    op.create_index("ix_cost_events_video_id", "cost_events", ["video_id"])
    op.create_index("ix_cost_events_channel_id", "cost_events", ["channel_id"])


def downgrade() -> None:
    op.drop_table("cost_events")
