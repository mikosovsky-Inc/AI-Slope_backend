from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, UniqueConstraint
from sqlmodel import Field

from app.db.base import Base


class Competitor(Base, table=True):
    __tablename__ = "competitors"
    __table_args__ = (
        UniqueConstraint("channel_id", "url", name="uq_competitors_channel_url"),
        CheckConstraint(
            "platform IN ('youtube', 'tiktok', 'instagram')", name="ck_competitor_platform"
        ),
        CheckConstraint("language IN ('pl', 'en')", name="ck_competitor_language"),
        CheckConstraint("typical_length_seconds > 0", name="ck_competitor_length"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    channel_id: UUID = Field(foreign_key="channels.id", ondelete="CASCADE", index=True)
    name: str = Field(max_length=120)
    platform: str = Field(max_length=16)
    url: str = Field(max_length=2000)
    language: str = Field(max_length=2)
    niche: str = Field(max_length=2000)
    observed_formats: list[str] = Field(default_factory=list, sa_type=JSON)
    typical_length_seconds: int | None = None
    publishing_frequency: str = Field(max_length=500)
    notes: str = Field(max_length=2000)
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )


class CompetitorContent(Base, table=True):
    __tablename__ = "competitor_contents"
    __table_args__ = (
        UniqueConstraint("competitor_id", "position", name="uq_competitor_content_position"),
        CheckConstraint("position >= 0", name="ck_competitor_content_position"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    competitor_id: UUID = Field(foreign_key="competitors.id", ondelete="CASCADE", index=True)
    title: str = Field(max_length=500)
    position: int
