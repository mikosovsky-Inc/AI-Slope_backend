from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.scheduler.models import PlanStatus


class PlanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel_id: UUID
    day: date
    status: PlanStatus
    target: int
    videos_today: int
    created_video_ids: list[UUID] = Field(default_factory=list)
    queued_idea_task_id: UUID | None = None
    reason: str | None = None


class TickResult(BaseModel):
    scanned: int = 0
    planned: int = 0
    videos_created: int = 0
    idea_tasks_created: int = 0
    errors: int = 0
