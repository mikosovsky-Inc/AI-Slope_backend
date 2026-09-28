from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, Enum, Index, Numeric, UniqueConstraint
from sqlmodel import Field

from app.db.base import Base
from app.modules.ideas.models import ContentFormat


class VideoStatus(StrEnum):
    DRAFT = "DRAFT"
    IDEA_GENERATED = "IDEA_GENERATED"
    RESEARCHING = "RESEARCHING"
    RESEARCHED = "RESEARCHED"
    SCRIPTING = "SCRIPTING"
    SCRIPT_READY = "SCRIPT_READY"
    GENERATING_ASSETS = "GENERATING_ASSETS"
    ASSETS_READY = "ASSETS_READY"
    GENERATING_AUDIO = "GENERATING_AUDIO"
    READY_TO_RENDER = "READY_TO_RENDER"
    RENDERING = "RENDERING"
    QUALITY_CHECK = "QUALITY_CHECK"
    READY = "READY"
    FAILED = "FAILED"
    PUBLISHED = "PUBLISHED"


class VisualType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    STOCK = "stock"
    NONE = "none"


def enum_type(enum: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum,
        values_callable=lambda values: [v.value for v in values],
        native_enum=False,
        create_constraint=True,
        name=name,
    )


class Video(Base, table=True):
    __tablename__ = "videos"
    __table_args__ = (
        UniqueConstraint("idea_id", name="uq_videos_idea"),
        Index("ix_videos_status_created", "status", "created_at", "id"),
        CheckConstraint("language IN ('pl', 'en')", name="ck_videos_language"),
        CheckConstraint(
            "budget_limit_usd > 0 AND budget_limit_usd <= 100", name="ck_videos_budget"
        ),
        CheckConstraint("duration_target BETWEEN 10 AND 180", name="ck_videos_duration"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    idea_id: UUID = Field(foreign_key="content_ideas.id", ondelete="CASCADE")
    title: str = Field(max_length=500)
    language: str = Field(max_length=2)
    format: ContentFormat = Field(sa_type=enum_type(ContentFormat, "video_format"))
    status: VideoStatus = Field(
        default=VideoStatus.DRAFT, sa_type=enum_type(VideoStatus, "video_status")
    )
    duration_target: int
    budget_limit_usd: Decimal = Field(sa_type=Numeric(10, 4))
    blueprint_snapshot: dict = Field(sa_type=JSON)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )


class VideoStatusEvent(Base, table=True):
    __tablename__ = "video_status_events"
    __table_args__ = (
        UniqueConstraint("video_id", "sequence", name="uq_video_events_sequence"),
        CheckConstraint("sequence >= 1", name="ck_video_events_sequence"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    video_id: UUID = Field(foreign_key="videos.id", ondelete="CASCADE")
    sequence: int
    from_status: VideoStatus | None = Field(
        default=None, sa_type=enum_type(VideoStatus, "video_event_from")
    )
    to_status: VideoStatus = Field(sa_type=enum_type(VideoStatus, "video_event_to"))
    reason: str = Field(max_length=500)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )


class VideoScript(Base, table=True):
    __tablename__ = "video_scripts"
    __table_args__ = (
        UniqueConstraint("video_id", name="uq_scripts_video"),
        CheckConstraint("language IN ('pl', 'en')", name="ck_scripts_language"),
        CheckConstraint("duration_target BETWEEN 10 AND 180", name="ck_scripts_duration"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    video_id: UUID = Field(foreign_key="videos.id", ondelete="CASCADE")
    title: str = Field(max_length=500)
    outline: dict = Field(default_factory=dict, sa_type=JSON)
    story: dict = Field(default_factory=dict, sa_type=JSON)
    hook: str = Field(max_length=1000)
    language: str = Field(max_length=2)
    duration_target: int
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )


class Scene(Base, table=True):
    __tablename__ = "scenes"
    __table_args__ = (
        UniqueConstraint("script_id", "position", name="uq_scenes_script_position"),
        UniqueConstraint("script_id", "generation_priority", name="uq_scene_generation_priority"),
        CheckConstraint("importance >= 0 AND importance <= 1", name="ck_scene_importance"),
        CheckConstraint("generation_priority >= 1", name="ck_scene_priority"),
        CheckConstraint("position >= 1", name="ck_scenes_position"),
        CheckConstraint("duration > 0 AND duration <= 180", name="ck_scenes_duration"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    script_id: UUID = Field(foreign_key="video_scripts.id", ondelete="CASCADE")
    position: int
    duration: Decimal = Field(sa_type=Numeric(8, 3))
    narration: str = Field(max_length=4000)
    visual_prompt: str = Field(max_length=4000)
    visual_type: VisualType = Field(sa_type=enum_type(VisualType, "scene_visual_type"))
    visual_style: str = Field(default="", max_length=2000)
    importance: float | None = None
    generation_priority: int | None = None
    camera_motion: str = Field(max_length=100)
    mood: str = Field(max_length=200)
    caption_emphasis: list[str] = Field(default_factory=list, sa_type=JSON)
