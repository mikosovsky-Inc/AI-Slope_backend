from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from app.modules.channels.schemas import InputModel, Language
from app.modules.videos.schemas import ScriptInput

ShortText = Annotated[str, Field(min_length=1, max_length=500)]


class ResearchQueries(InputModel):
    queries: list[ShortText] = Field(min_length=1, max_length=5)


class ResearchQuery(InputModel):
    text: ShortText
    language: Language
    limit: int = Field(default=6, ge=1, le=6)


class SourceDocument(InputModel):
    model_config = ConfigDict(revalidate_instances="always")
    source_url: Annotated[AnyHttpUrl, Field(max_length=2000)]
    source_title: ShortText
    content: str = Field(min_length=1, max_length=8000)
    language: Language
    metadata: dict = Field(default_factory=dict)


class ExtractedFact(InputModel):
    document_id: str
    quote: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)


class ExtractedFacts(InputModel):
    facts: list[ExtractedFact] = Field(max_length=30)


class FactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    document_id: UUID
    statement: str
    source_url: str
    source_title: str
    confidence: float
    metadata_json: dict


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    source_url: str
    source_title: str
    content: str
    metadata_json: dict
    retrieved_at: datetime


class ResearchRead(BaseModel):
    video_id: UUID
    documents: list[DocumentRead]
    facts: list[FactRead]


class RankedFact(InputModel):
    fact_id: str
    duration: float = Field(gt=0, le=180)
    visual_prompt: str = Field(min_length=1, max_length=4000)


class Top5Plan(InputModel):
    items: list[RankedFact] = Field(min_length=5, max_length=5)


class Citation(BaseModel):
    position: int
    fact: FactRead


class Top5ScriptRead(ScriptInput):
    id: UUID
    video_id: UUID
    citations: list[Citation]
