from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, Numeric, UniqueConstraint
from sqlmodel import Field

from app.db.base import Base


class CostEvent(Base, table=True):
    __tablename__ = "cost_events"
    __table_args__ = (
        UniqueConstraint("generation_job_id", name="uq_cost_generation_job"),
        CheckConstraint("estimated_cost_usd >= 0", name="ck_cost_estimate"),
        CheckConstraint("actual_cost_usd >= 0", name="ck_cost_actual"),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    video_id: UUID = Field(foreign_key="videos.id", ondelete="CASCADE", index=True)
    channel_id: UUID = Field(foreign_key="channels.id", ondelete="CASCADE", index=True)
    generation_job_id: UUID | None = Field(
        default=None, foreign_key="generation_jobs.id", ondelete="SET NULL"
    )
    provider: str = Field(max_length=100)
    operation: str = Field(max_length=100)
    model: str = Field(max_length=200)
    estimated_cost_usd: Decimal = Field(sa_type=Numeric(12, 6))
    actual_cost_usd: Decimal | None = Field(default=None, sa_type=Numeric(12, 6))
    metadata_json: dict = Field(default_factory=dict, sa_type=JSON)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )
