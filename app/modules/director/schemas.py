from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.director.pricing import VisualEstimates
from app.modules.videos.models import VisualType


class DirectedScene(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    position: int
    duration: Decimal
    visual_prompt: str
    visual_type: VisualType
    camera_motion: Literal["zoom_in", "zoom_out", "pan_left", "pan_right", "static"]
    visual_style: str
    importance: float = Field(ge=0, le=1)
    generation_priority: int = Field(ge=1)


class DirectorRead(BaseModel):
    id: UUID
    video_id: UUID
    visual_budget_usd: Decimal
    estimated_cost_usd: Decimal
    requested_video_ratio: float
    pricing_snapshot: VisualEstimates
    created_at: datetime
    scenes: list[DirectedScene]
