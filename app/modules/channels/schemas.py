from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.channels.models import AutopilotMode, ChannelStatus

Name = Annotated[str, Field(min_length=1, max_length=120)]
Idea = Annotated[str, Field(min_length=10, max_length=4000)]
Language = Literal["pl", "en"]
Frequency = Annotated[int, Field(strict=True, ge=1, le=24)]
Budget = Annotated[Decimal, Field(gt=0, le=100, max_digits=10, decimal_places=4)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FormatMix(InputModel):
    top5: float = Field(default=0.7, ge=0, le=1)
    story: float = Field(default=0.3, ge=0, le=1)

    @model_validator(mode="after")
    def sum_to_one(self) -> Self:
        if abs(self.top5 + self.story - 1) > 0.000001:
            raise ValueError("Format weights must sum to 1")
        return self


class VideoStyle(InputModel):
    duration_target: int = Field(default=45, ge=10, le=180)
    pace: Literal["slow", "medium", "fast"] = "fast"
    hook_max_seconds: float = Field(default=2, gt=0, le=5)


class VisualStyle(InputModel):
    description: str = Field(default="", max_length=2000)
    video_scene_ratio: float = Field(default=0.25, ge=0, le=1)


class BlueprintConfiguration(InputModel):
    target_audience: str = Field(default="", max_length=2000)
    tone: str = Field(default="", max_length=500)
    formats: FormatMix = Field(default_factory=FormatMix)
    video_style: VideoStyle = Field(default_factory=VideoStyle)
    visual_style: VisualStyle = Field(default_factory=VisualStyle)


class PillarInput(InputModel):
    name: Name
    description: str = Field(default="", max_length=2000)


class BlueprintInput(InputModel):
    configuration: BlueprintConfiguration = Field(default_factory=BlueprintConfiguration)
    content_pillars: list[PillarInput] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def unique_pillars(self) -> Self:
        names = [pillar.name.casefold() for pillar in self.content_pillars]
        if len(set(names)) != len(names):
            raise ValueError("Content pillar names must be unique")
        return self


class ChannelCreate(InputModel):
    name: Name | None = None
    idea: Idea
    language: Language
    videos_per_day: Frequency = 2
    budget_per_video_usd: Budget = Decimal("0.20")
    autopilot_mode: AutopilotMode = AutopilotMode.MANUAL


class ChannelUpdate(InputModel):
    name: Name | None = None
    idea: Idea | None = None
    language: Language | None = None
    videos_per_day: Frequency | None = None
    budget_per_video_usd: Budget | None = None
    autopilot_mode: AutopilotMode | None = None
    blueprint: BlueprintInput | None = None

    @model_validator(mode="after")
    def require_values(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Provide at least one field")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Explicit null values are not allowed")
        return self


class PillarRead(PillarInput):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    position: int


class BlueprintRead(BaseModel):
    id: UUID
    configuration: BlueprintConfiguration
    content_pillars: list[PillarRead]
    updated_at: datetime


class ChannelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    owner_id: UUID
    name: str
    idea: str
    language: Language
    videos_per_day: int
    budget_per_video_usd: Decimal
    autopilot_mode: AutopilotMode
    status: ChannelStatus
    created_at: datetime
    updated_at: datetime


class ChannelDetail(ChannelRead):
    blueprint: BlueprintRead


class ChannelPage(BaseModel):
    items: list[ChannelRead]
    total: int
    limit: int
    offset: int
