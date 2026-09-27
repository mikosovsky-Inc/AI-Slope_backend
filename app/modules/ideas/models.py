from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, Enum, Index, UniqueConstraint
from sqlmodel import Field

from app.db.base import Base


class IdeaStatus(StrEnum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    REJECTED = "rejected"
    USED = "used"


class ContentFormat(StrEnum):
    TOP5 = "top5"
    STORY = "story"


class ContentIdea(Base, table=True):
    __tablename__ = "content_ideas"
    __table_args__ = (
        UniqueConstraint("channel_id", "title_key", name="uq_ideas_channel_title"),
        Index("ix_ideas_channel_created", "channel_id", "created_at", "id"),
        Index("ix_ideas_channel_status", "channel_id", "status"),
        CheckConstraint("language IN ('pl', 'en')", name="ck_ideas_language"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    channel_id: UUID = Field(foreign_key="channels.id", ondelete="CASCADE")
    title: str = Field(max_length=500)
    title_key: str = Field(max_length=1000)
    concept: str = Field(max_length=2000)
    # Snapshot: re-analysis replaces ContentPillar rows; historical ideas must survive.
    content_pillar: str = Field(max_length=120)
    language: str = Field(max_length=2)
    format: ContentFormat = Field(
        sa_type=Enum(
            ContentFormat,
            values_callable=lambda e: [v.value for v in e],
            native_enum=False,
            create_constraint=True,
            name="content_format",
        )
    )
    hook_idea: str = Field(max_length=1000)
    rationale: str = Field(max_length=2000)
    novelty_heuristic: dict = Field(sa_type=JSON)
    visual_potential_heuristic: dict = Field(sa_type=JSON)
    status: IdeaStatus = Field(
        default=IdeaStatus.CANDIDATE,
        sa_type=Enum(
            IdeaStatus,
            values_callable=lambda e: [v.value for v in e],
            native_enum=False,
            create_constraint=True,
            name="idea_status",
        ),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )
