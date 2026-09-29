from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.channels.schemas import ChannelDetail, InputModel
from app.modules.scheduler.models import PlanStatus
from app.modules.tasks.schemas import TaskRead
from app.modules.videos.models import VideoStatus
from app.modules.videos.schemas import SceneInput, VideoRead


class CostSummary(BaseModel):
    currency: Literal["USD"] = "USD"
    estimated_usd: Decimal = Decimal(0)
    actual_usd: Decimal = Decimal(0)
    effective_usd: Decimal = Decimal(0)
    events: int = 0
    pending_actual_events: int = 0


class CostRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    video_id: UUID
    provider: str
    operation: str
    model: str
    estimated_cost_usd: Decimal
    actual_cost_usd: Decimal | None
    created_at: datetime


class CostPage(BaseModel):
    summary: CostSummary
    items: list[CostRead]
    total: int
    limit: int
    offset: int


class VideoPage(BaseModel):
    items: list[VideoRead]
    total: int
    limit: int
    offset: int


class Dashboard(BaseModel):
    channels: int
    active_channels: int
    videos: int
    videos_by_status: dict[VideoStatus, int]
    costs: CostSummary
    recent_videos: list[VideoRead]


class PlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    day: str
    status: PlanStatus
    target: int
    reason: str | None


class Overview(BaseModel):
    channel: ChannelDetail
    videos_by_status: dict[VideoStatus, int]
    ideas_by_status: dict[str, int]
    costs: CostSummary
    latest_plan: PlanRead | None


class SceneRead(SceneInput):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    script_id: UUID
    visual_style: str
    importance: float | None
    generation_priority: int | None


class ScenePatch(InputModel):
    narration: str | None = Field(default=None, min_length=1, max_length=4000)
    visual_prompt: str | None = Field(default=None, min_length=1, max_length=4000)
    mood: str | None = Field(default=None, max_length=200)
    caption_emphasis: list[Annotated[str, Field(min_length=1, max_length=100)]] | None = Field(
        default=None, max_length=20
    )

    @model_validator(mode="after")
    def supplied(self) -> "ScenePatch":
        if not self.model_fields_set or any(
            getattr(self, field) is None for field in self.model_fields_set
        ):
            raise ValueError("Provide at least one non-null field")
        return self


class Regenerate(InputModel):
    kind: Literal["image", "video", "audio"]


class Retry(InputModel):
    task_id: UUID


class StatusRead(BaseModel):
    video_id: UUID
    status: VideoStatus
    updated_at: datetime
    tasks: list[TaskRead]
    has_active_tasks: bool
