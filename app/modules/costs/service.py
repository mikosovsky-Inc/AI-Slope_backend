"""Reserve estimated expenditure under the video's database row lock."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select

from app.modules.costs.models import CostEvent
from app.modules.costs.schemas import VideoBudget
from app.modules.ideas.models import ContentIdea
from app.modules.videos.models import Video


class BudgetExceeded(Exception):
    pass


class BudgetService:
    @staticmethod
    def spent(db: Session, video_id: UUID) -> Decimal:
        return db.exec(
            select(
                func.coalesce(
                    func.sum(
                        func.coalesce(CostEvent.actual_cost_usd, CostEvent.estimated_cost_usd)
                    ),
                    0,
                )
            ).where(CostEvent.video_id == video_id)
        ).one()

    @classmethod
    def remaining(cls, db: Session, video: Video) -> Decimal:
        return max(Decimal(0), video.budget_limit_usd - cls.spent(db, video.id))

    @classmethod
    def can_spend(cls, db: Session, video: Video, amount: Decimal) -> bool:
        if not amount.is_finite() or amount < 0:
            raise ValueError("Cost must be finite and nonnegative")
        return amount <= cls.remaining(db, video)

    @classmethod
    def require(cls, db: Session, video: Video, amount: Decimal) -> None:
        if not cls.can_spend(db, video, amount):
            raise BudgetExceeded("Video budget exhausted")

    @classmethod
    def reserve(
        cls,
        db: Session,
        video_id: UUID,
        *,
        key: str,
        amount: Decimal,
        provider: str,
        operation: str,
        model: str,
    ) -> CostEvent:
        # Caller commits reservation BEFORE contacting the provider. Failures retain it.
        video = db.exec(
            select(Video)
            .where(Video.id == video_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()
        existing = db.exec(
            select(CostEvent).where(CostEvent.video_id == video_id, CostEvent.operation_key == key)
        ).first()
        if existing:
            return existing
        cls.require(db, video, amount)
        idea = db.get(ContentIdea, video.idea_id)
        event = CostEvent(
            video_id=video_id,
            channel_id=idea.channel_id,
            operation_key=key,
            provider=provider,
            operation=operation,
            model=model,
            estimated_cost_usd=amount,
            metadata_json={"outcome": "reserved", "pricing": "configured_estimate"},
        )
        db.add(event)
        db.flush()
        return event


def read_budget(db: Session, owner_id: UUID, video_id: UUID) -> VideoBudget:
    from app.modules.scripts.service import owned_video

    video = owned_video(db, owner_id, video_id)
    spent = BudgetService.spent(db, video_id)
    return VideoBudget(
        video_id=video.id,
        limit_usd=video.budget_limit_usd,
        committed_usd=spent,
        remaining_usd=max(Decimal(0), video.budget_limit_usd - spent),
    )
