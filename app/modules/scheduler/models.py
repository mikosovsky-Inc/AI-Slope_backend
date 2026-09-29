from datetime import UTC, date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, UniqueConstraint
from sqlmodel import Field

from app.db.base import Base
from app.modules.videos.models import enum_type


class PlanStatus(StrEnum):
    COMPLETE = "complete"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_TASK = "waiting_task"
    BLOCKED = "blocked"


class DailyPlan(Base, table=True):
    __tablename__ = "daily_plans"
    __table_args__ = (
        UniqueConstraint("channel_id", "day", name="uq_daily_plan_channel_day"),
        CheckConstraint("target BETWEEN 1 AND 24", name="ck_daily_plan_target"),
        CheckConstraint("window_end > window_start", name="ck_daily_plan_window"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    channel_id: UUID = Field(foreign_key="channels.id", ondelete="CASCADE", index=True)
    day: date
    timezone: str = Field(max_length=100)
    window_start: datetime = Field(sa_type=DateTime(timezone=True))
    window_end: datetime = Field(sa_type=DateTime(timezone=True))
    target: int
    status: PlanStatus = Field(sa_type=enum_type(PlanStatus, "daily_plan_status"))
    reason: str | None = Field(default=None, max_length=100)
    idea_task_ids: list[str] = Field(default_factory=list, sa_type=JSON)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )
