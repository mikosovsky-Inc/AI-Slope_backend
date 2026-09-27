"""Create channels, blueprints and content pillars."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("idea", sa.String(4000), nullable=False),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("videos_per_day", sa.Integer(), nullable=False),
        sa.Column("budget_per_video_usd", sa.Numeric(10, 4), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "active",
                "paused",
                name="channel_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "autopilot_mode",
            sa.Enum(
                "manual",
                "semi_auto",
                name="autopilot_mode",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint("language IN ('pl', 'en')", name="ck_channels_language"),
        sa.CheckConstraint("videos_per_day BETWEEN 1 AND 24", name="ck_channels_frequency"),
        sa.CheckConstraint(
            "budget_per_video_usd > 0 AND budget_per_video_usd <= 100", name="ck_channels_budget"
        ),
    )
    op.create_index("ix_channels_owner_created", "channels", ["owner_id", "created_at", "id"])
    op.create_index("ix_channels_status", "channels", ["status"])
    op.create_table(
        "channel_blueprints",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["channel_id"], ["channels.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("channel_id", name="uq_blueprints_channel"),
    )
    op.create_table(
        "content_pillars",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("blueprint_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(2000), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["blueprint_id"], ["channel_blueprints.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("blueprint_id", "position", name="uq_pillars_position"),
        sa.CheckConstraint("position >= 0", name="ck_pillars_position"),
    )


def downgrade() -> None:
    op.drop_table("content_pillars")
    op.drop_table("channel_blueprints")
    op.drop_table("channels")
