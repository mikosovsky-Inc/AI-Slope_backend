from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.channels.schemas import InputModel
from app.modules.ideas.models import ContentFormat, IdeaStatus


class Heuristic(InputModel):
    score: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=1000)


class GeneratedIdea(InputModel):
    title: str = Field(min_length=1, max_length=500)
    concept: str = Field(min_length=1, max_length=2000)
    content_pillar: str = Field(min_length=1, max_length=120)
    format: ContentFormat
    hook_idea: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=2000)
    novelty_heuristic: Heuristic
    visual_potential_heuristic: Heuristic


class GeneratedIdeas(InputModel):
    items: list[GeneratedIdea] = Field(min_length=10, max_length=20)


class IdeaRead(GeneratedIdea):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    channel_id: UUID
    language: str
    status: IdeaStatus
    created_at: datetime
    updated_at: datetime


class IdeaBatch(BaseModel):
    items: list[IdeaRead]


class IdeaPage(IdeaBatch):
    total: int
    limit: int
    offset: int
