from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.tasks.models import TaskKind, TaskStatus


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: TaskKind
    status: TaskStatus
    video_id: UUID | None
    attempts: int
    error: str | None
    result: dict | None
    created_at: datetime
    completed_at: datetime | None
    request_id: str | None = None
    error_category: str | None = None
