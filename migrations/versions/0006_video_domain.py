"""Video domain and durable status history."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

STATUSES = (
    "DRAFT",
    "IDEA_GENERATED",
    "RESEARCHING",
    "RESEARCHED",
    "SCRIPTING",
    "SCRIPT_READY",
    "GENERATING_ASSETS",
    "ASSETS_READY",
    "GENERATING_AUDIO",
    "READY_TO_RENDER",
    "RENDERING",
    "QUALITY_CHECK",
    "READY",
    "FAILED",
    "PUBLISHED",
)


def status_enum(name: str) -> sa.Enum:
    return sa.Enum(*STATUSES, name=name, native_enum=False, create_constraint=True)


def upgrade() -> None:
    op.create_table(
        "videos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "idea_id",
            sa.Uuid(),
            sa.ForeignKey("content_ideas.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column(
            "format",
            sa.Enum(
                "top5", "story", name="video_format", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column("status", status_enum("video_status"), nullable=False),
        sa.Column("duration_target", sa.Integer(), nullable=False),
        sa.Column("budget_limit_usd", sa.Numeric(10, 4), nullable=False),
        sa.Column("blueprint_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idea_id", name="uq_videos_idea"),
        sa.CheckConstraint("language IN ('pl', 'en')", name="ck_videos_language"),
        sa.CheckConstraint(
            "budget_limit_usd > 0 AND budget_limit_usd <= 100", name="ck_videos_budget"
        ),
        sa.CheckConstraint("duration_target BETWEEN 10 AND 180", name="ck_videos_duration"),
    )
    op.create_index("ix_videos_status_created", "videos", ["status", "created_at", "id"])
    op.create_table(
        "video_status_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "video_id", sa.Uuid(), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_status", status_enum("video_event_from"), nullable=True),
        sa.Column("to_status", status_enum("video_event_to"), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("video_id", "sequence", name="uq_video_events_sequence"),
        sa.CheckConstraint("sequence >= 1", name="ck_video_events_sequence"),
    )
    op.create_table(
        "video_scripts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "video_id", sa.Uuid(), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("hook", sa.String(1000), nullable=False),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("duration_target", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("video_id", name="uq_scripts_video"),
        sa.CheckConstraint("language IN ('pl', 'en')", name="ck_scripts_language"),
        sa.CheckConstraint("duration_target BETWEEN 10 AND 180", name="ck_scripts_duration"),
    )
    op.create_table(
        "scenes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "script_id",
            sa.Uuid(),
            sa.ForeignKey("video_scripts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("duration", sa.Numeric(8, 3), nullable=False),
        sa.Column("narration", sa.String(4000), nullable=False),
        sa.Column("visual_prompt", sa.String(4000), nullable=False),
        sa.Column(
            "visual_type",
            sa.Enum(
                "image",
                "video",
                "stock",
                "none",
                name="scene_visual_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("camera_motion", sa.String(100), nullable=False),
        sa.Column("mood", sa.String(200), nullable=False),
        sa.Column("caption_emphasis", sa.JSON(), nullable=False),
        sa.UniqueConstraint("script_id", "position", name="uq_scenes_script_position"),
        sa.CheckConstraint("position >= 1", name="ck_scenes_position"),
        sa.CheckConstraint("duration > 0 AND duration <= 180", name="ck_scenes_duration"),
    )


def downgrade() -> None:
    op.drop_table("scenes")
    op.drop_table("video_scripts")
    op.drop_table("video_status_events")
    op.drop_table("videos")
