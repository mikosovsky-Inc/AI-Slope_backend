from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, UniqueConstraint
from sqlmodel import Field

from app.db.base import Base


class ResearchDocument(Base, table=True):
    __tablename__ = "research_documents"
    __table_args__ = (UniqueConstraint("video_id", "source_url", name="uq_research_document_url"),)
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    video_id: UUID = Field(foreign_key="videos.id", ondelete="CASCADE", index=True)
    source_url: str = Field(max_length=2000)
    source_title: str = Field(max_length=500)
    content: str = Field(max_length=8000)
    metadata_json: dict = Field(default_factory=dict, sa_type=JSON)
    retrieved_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )


class ResearchFact(Base, table=True):
    __tablename__ = "research_facts"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_research_confidence"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    document_id: UUID = Field(foreign_key="research_documents.id", ondelete="CASCADE", index=True)
    statement: str = Field(max_length=2000)
    source_url: str = Field(max_length=2000)
    source_title: str = Field(max_length=500)
    confidence: float
    metadata_json: dict = Field(default_factory=dict, sa_type=JSON)


class SceneResearchFact(Base, table=True):
    __tablename__ = "scene_research_facts"
    scene_id: UUID = Field(foreign_key="scenes.id", ondelete="CASCADE", primary_key=True)
    fact_id: UUID = Field(foreign_key="research_facts.id", ondelete="CASCADE", primary_key=True)
