"""Create content ideas with explicit lifecycle and channel ownership."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_ideas",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "channel_id",
            sa.Uuid(),
            sa.ForeignKey("channels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("title_key", sa.String(1000), nullable=False),
        sa.Column("concept", sa.String(2000), nullable=False),
        sa.Column("content_pillar", sa.String(120), nullable=False),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column(
            "format",
            sa.Enum(
                "top5", "story", native_enum=False, create_constraint=True, name="content_format"
            ),
            nullable=False,
        ),
        sa.Column("hook_idea", sa.String(1000), nullable=False),
        sa.Column("rationale", sa.String(2000), nullable=False),
        sa.Column("novelty_heuristic", sa.JSON(), nullable=False),
        sa.Column("visual_potential_heuristic", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "candidate",
                "approved",
                "rejected",
                "used",
                native_enum=False,
                create_constraint=True,
                name="idea_status",
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("channel_id", "title_key", name="uq_ideas_channel_title"),
        sa.CheckConstraint("language IN ('pl', 'en')", name="ck_ideas_language"),
    )
    op.create_index("ix_ideas_channel_created", "content_ideas", ["channel_id", "created_at", "id"])
    op.create_index("ix_ideas_channel_status", "content_ideas", ["channel_id", "status"])


def downgrade() -> None:
    op.drop_table("content_ideas")
