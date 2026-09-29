from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.modules.tasks.models import TaskKind, TaskStatus


class AdminJob(BaseModel):
    id: UUID
    owner_id: UUID
    channel_id: UUID | None
    video_id: UUID | None
    scene_id: UUID | None
    kind: TaskKind
    queue: str
    status: TaskStatus
    attempts: int
    max_attempts: int
    request_id: str | None
    error: str | None
    error_category: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    available_at: datetime


class AdminJobPage(BaseModel):
    items: list[AdminJob]
    total: int
    limit: int
    offset: int
    counts: dict[TaskStatus, int]
