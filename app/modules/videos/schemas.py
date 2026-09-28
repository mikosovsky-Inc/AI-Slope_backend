from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.channels.schemas import BlueprintInput, Budget, InputModel, Language
from app.modules.ideas.models import ContentFormat
from app.modules.videos.models import VideoStatus, VisualType


class SceneInput(InputModel):
    position: int = Field(ge=1)
    duration: Decimal = Field(gt=0, le=180, max_digits=8, decimal_places=3)
    narration: str = Field(min_length=1, max_length=4000)
    visual_prompt: str = Field(max_length=4000)
    visual_type: VisualType
    camera_motion: str = Field(min_length=1, max_length=100)
    mood: str = Field(max_length=200)
    caption_emphasis: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        max_length=20
    )


class ScriptInput(InputModel):
    title: str = Field(min_length=1, max_length=500)
    hook: str = Field(min_length=1, max_length=1000)
    language: Language
    duration_target: int = Field(ge=10, le=180)
    scenes: list[SceneInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def ordered_scenes(self) -> "ScriptInput":
        if [s.position for s in self.scenes] != list(range(1, len(self.scenes) + 1)):
            raise ValueError("Scene positions must be consecutive and start at 1")
        return self


class VideoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    idea_id: UUID
    title: str
    language: Language
    format: ContentFormat
    status: VideoStatus
    duration_target: int
    budget_limit_usd: Budget
    blueprint_snapshot: BlueprintInput
    created_at: datetime
    updated_at: datetime
