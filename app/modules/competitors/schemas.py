from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.modules.intelligence.research import CompetitorResearchResult


class CompetitorRead(CompetitorResearchResult):
    id: UUID
    channel_id: UUID
    updated_at: datetime


class CompetitorPage(BaseModel):
    items: list[CompetitorRead]
    total: int
    limit: int
    offset: int


class ResearchSummary(BaseModel):
    processed: int
    items: list[CompetitorRead]
