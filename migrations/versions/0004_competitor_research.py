"""Persist competitor benchmarks and example titles."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "competitors",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "channel_id",
            sa.Uuid(),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("url", sa.String(2000), nullable=False),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("niche", sa.String(2000), nullable=False),
        sa.Column("observed_formats", sa.JSON(), nullable=False),
        sa.Column("typical_length_seconds", sa.Integer(), nullable=True),
        sa.Column("publishing_frequency", sa.String(500), nullable=False),
        sa.Column("notes", sa.String(2000), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("channel_id", "url", name="uq_competitors_channel_url"),
        sa.CheckConstraint(
            "platform IN ('youtube', 'tiktok', 'instagram')", name="ck_competitor_platform"
        ),
        sa.CheckConstraint("language IN ('pl', 'en')", name="ck_competitor_language"),
        sa.CheckConstraint("typical_length_seconds > 0", name="ck_competitor_length"),
    )
    op.create_index("ix_competitors_channel_id", "competitors", ["channel_id"])
    op.create_table(
        "competitor_contents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "competitor_id",
            sa.Uuid(),
            sa.ForeignKey("competitors.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint("competitor_id", "position", name="uq_competitor_content_position"),
        sa.CheckConstraint("position >= 0", name="ck_competitor_content_position"),
    )
    op.create_index(
        "ix_competitor_contents_competitor_id", "competitor_contents", ["competitor_id"]
    )


def downgrade() -> None:
    op.drop_table("competitor_contents")
    op.drop_table("competitors")
