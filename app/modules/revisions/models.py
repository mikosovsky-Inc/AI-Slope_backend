from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, UniqueConstraint
from sqlmodel import Field

from app.db.base import Base
from app.modules.videos.models import enum_type


class RevisionStatus(StrEnum):
    DRAFT = "draft"
    PRODUCING = "producing"
    READY = "ready"
    CANCELLED = "cancelled"


class VideoRevision(Base, table=True):
    __tablename__ = "video_revisions"
    __table_args__ = (
        UniqueConstraint("video_id", "number", name="uq_revision_number"),
        CheckConstraint("number >= 1", name="ck_revision_number"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    video_id: UUID = Field(foreign_key="videos.id", ondelete="CASCADE", index=True)
    number: int
    status: RevisionStatus = Field(
        default=RevisionStatus.DRAFT, sa_type=enum_type(RevisionStatus, "revision_status")
    )
    base_render_id: UUID = Field(foreign_key="tasks.id", ondelete="CASCADE")
    render_task_id: UUID | None = Field(default=None, foreign_key="tasks.id", ondelete="SET NULL")
    final_asset_id: UUID | None = Field(default=None, foreign_key="assets.id", ondelete="SET NULL")
    scenes: list[dict] = Field(sa_type=JSON)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )


class TaskRecovery(Base, table=True):
    __tablename__ = "task_recoveries"
    __table_args__ = (UniqueConstraint("task_id", "number", name="uq_recovery_number"),)
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    task_id: UUID = Field(foreign_key="tasks.id", ondelete="CASCADE", index=True)
    actor_id: UUID = Field(foreign_key="users.id", ondelete="CASCADE")
    number: int
    action: str = Field(max_length=30)
    note: str = Field(max_length=500)
    evidence: dict = Field(default_factory=dict, sa_type=JSON)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )
