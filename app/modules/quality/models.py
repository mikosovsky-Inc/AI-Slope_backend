from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, UniqueConstraint
from sqlmodel import Field

from app.db.base import Base
from app.modules.videos.models import enum_type


class QualityStatus(StrEnum):
    CHECKING = "checking"
    PASSED = "passed"
    REPAIRING = "repairing"
    REPAIRED = "repaired"
    FAILED = "failed"


class QualityCheck(Base, table=True):
    __tablename__ = "quality_checks"
    __table_args__ = (
        UniqueConstraint("render_task_id", name="uq_quality_render"),
        UniqueConstraint("task_id", name="uq_quality_task"),
        UniqueConstraint("video_id", "attempt", name="uq_quality_video_attempt"),
        CheckConstraint("attempt >= 1", name="ck_quality_attempt"),
        CheckConstraint(
            "(status IN ('checking', 'repairing') AND completed_at IS NULL) OR "
            "(status IN ('passed', 'repaired', 'failed') AND completed_at IS NOT NULL "
            "AND completed_at >= created_at)",
            name="ck_quality_timestamps",
        ),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    video_id: UUID = Field(foreign_key="videos.id", ondelete="CASCADE", index=True)
    render_task_id: UUID = Field(foreign_key="tasks.id", ondelete="CASCADE")
    task_id: UUID = Field(foreign_key="tasks.id", ondelete="CASCADE")
    final_asset_id: UUID | None = Field(default=None, foreign_key="assets.id", ondelete="SET NULL")
    attempt: int
    status: QualityStatus = Field(
        default=QualityStatus.CHECKING, sa_type=enum_type(QualityStatus, "quality_status")
    )
    policy_json: dict = Field(default_factory=dict, sa_type=JSON)
    report_json: dict = Field(default_factory=dict, sa_type=JSON)
    repair_tasks: list[dict] = Field(default_factory=list, sa_type=JSON)
    rerender_task_id: UUID | None = Field(default=None, foreign_key="tasks.id", ondelete="SET NULL")
    error: str | None = Field(default=None, max_length=100)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
