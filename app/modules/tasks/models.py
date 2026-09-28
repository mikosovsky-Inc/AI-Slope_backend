from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, UniqueConstraint
from sqlmodel import Field

from app.db.base import Base
from app.modules.videos.models import enum_type


class TaskKind(StrEnum):
    ANALYZE = "analyze"
    IDEAS = "ideas"
    COMPETITORS = "competitors"
    STORY = "story"
    RESEARCH = "research"
    TOP5 = "top5"
    DIRECT = "direct"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


QUEUES = {
    TaskKind.ANALYZE: "content",
    TaskKind.IDEAS: "content",
    TaskKind.COMPETITORS: "research",
    TaskKind.STORY: "content",
    TaskKind.RESEARCH: "research",
    TaskKind.TOP5: "content",
    TaskKind.DIRECT: "content",
    TaskKind.IMAGE: "image",
    TaskKind.VIDEO: "video",
    TaskKind.AUDIO: "audio",
}
ALL_QUEUES = ("content", "research", "image", "video", "audio", "render", "quality")


class Task(Base, table=True):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_task_owner_key"),
        Index("ix_task_delivery", "status", "available_at"),
        CheckConstraint("attempts >= 0 AND max_attempts BETWEEN 1 AND 10", name="ck_task_attempts"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    owner_id: UUID = Field(foreign_key="users.id", ondelete="CASCADE", index=True)
    channel_id: UUID | None = Field(default=None, foreign_key="channels.id", ondelete="CASCADE")
    video_id: UUID | None = Field(
        default=None, foreign_key="videos.id", ondelete="CASCADE", index=True
    )
    scene_id: UUID | None = Field(default=None, foreign_key="scenes.id", ondelete="CASCADE")
    kind: TaskKind = Field(sa_type=enum_type(TaskKind, "task_kind"))
    status: TaskStatus = Field(
        default=TaskStatus.QUEUED, sa_type=enum_type(TaskStatus, "task_status")
    )
    idempotency_key: str = Field(max_length=200)
    parameters: dict = Field(default_factory=dict, sa_type=JSON)
    checkpoint: dict = Field(default_factory=dict, sa_type=JSON)
    result: dict | None = Field(default=None, sa_type=JSON)
    error: str | None = Field(default=None, max_length=100)
    attempts: int = Field(default=0)
    max_attempts: int = Field(default=3)
    run_token: UUID | None = None
    available_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )
    delivery_after: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )
    started_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )
