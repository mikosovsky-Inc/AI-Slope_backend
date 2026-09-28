from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, Numeric, UniqueConstraint
from sqlmodel import Field

from app.db.base import Base


class DirectorPlan(Base, table=True):
    __tablename__ = "director_plans"
    __table_args__ = (
        UniqueConstraint("video_id", name="uq_director_plan_video"),
        CheckConstraint("visual_budget_usd >= 0", name="ck_director_budget"),
        CheckConstraint(
            "estimated_cost_usd >= 0 AND estimated_cost_usd <= visual_budget_usd",
            name="ck_director_estimate",
        ),
        CheckConstraint(
            "requested_video_ratio >= 0 AND requested_video_ratio <= 1", name="ck_director_ratio"
        ),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    video_id: UUID = Field(foreign_key="videos.id", ondelete="CASCADE")
    visual_budget_usd: Decimal = Field(sa_type=Numeric(12, 6))
    estimated_cost_usd: Decimal = Field(sa_type=Numeric(12, 6))
    requested_video_ratio: float
    pricing_snapshot: dict = Field(sa_type=JSON)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )
