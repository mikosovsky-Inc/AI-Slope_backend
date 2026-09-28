from decimal import ROUND_DOWN, Decimal
from uuid import UUID

from sqlmodel import Session, select

from app.modules.channels.schemas import BlueprintInput
from app.modules.director.models import DirectorPlan
from app.modules.director.pricing import VisualCostEstimator
from app.modules.director.schemas import DirectedScene, DirectorRead
from app.modules.scripts.service import owned_video
from app.modules.videos.models import Scene, VideoScript, VideoStatus, VisualType


class DirectorNotReady(Exception):
    pass


class DirectorBudgetExceeded(Exception):
    pass


class DirectorPlanNotFound(Exception):
    pass


def read_plan(db: Session, plan: DirectorPlan) -> DirectorRead:
    scenes = db.exec(
        select(Scene)
        .join(VideoScript)
        .where(VideoScript.video_id == plan.video_id)
        .order_by(Scene.position)
        .execution_options(populate_existing=True)
    ).all()
    return DirectorRead(
        **plan.model_dump(), scenes=[DirectedScene.model_validate(s) for s in scenes]
    )


class DirectorService:
    def __init__(self, estimator: VisualCostEstimator, visual_budget_fraction: Decimal) -> None:
        if not Decimal(0) < visual_budget_fraction <= Decimal(1):
            raise ValueError("Visual budget fraction must be in (0, 1]")
        self.estimator = estimator
        self.visual_budget_fraction = visual_budget_fraction

    def get(self, db: Session, owner_id: UUID, video_id: UUID) -> DirectorRead:
        owned_video(db, owner_id, video_id)
        plan = db.exec(select(DirectorPlan).where(DirectorPlan.video_id == video_id)).one_or_none()
        if plan is None:
            raise DirectorPlanNotFound
        return read_plan(db, plan)

    def direct(self, db: Session, owner_id: UUID, video_id: UUID) -> DirectorRead:
        try:
            video = owned_video(db, owner_id, video_id, lock=True)
            existing = db.exec(
                select(DirectorPlan).where(DirectorPlan.video_id == video_id)
            ).one_or_none()
            if existing:
                result = read_plan(db, existing)
                db.commit()
                return result
            if video.status != VideoStatus.SCRIPT_READY:
                raise DirectorNotReady
            scenes = db.exec(
                select(Scene)
                .join(VideoScript)
                .where(VideoScript.video_id == video_id)
                .order_by(Scene.position)
            ).all()
            if not scenes or [s.position for s in scenes] != list(range(1, len(scenes) + 1)):
                raise DirectorNotReady
            total = sum((s.duration for s in scenes), Decimal(0))
            if abs(total - video.duration_target) > Decimal(video.duration_target) * Decimal("0.1"):
                raise DirectorNotReady
            blueprint = BlueprintInput.model_validate(video.blueprint_snapshot)
            style = blueprint.configuration.visual_style
            visual_budget = (video.budget_limit_usd * self.visual_budget_fraction).quantize(
                Decimal("0.000001"), rounding=ROUND_DOWN
            )
            image_cost = self.estimator.image_cost()
            cost = image_cost * len(scenes)
            if cost > visual_budget:
                raise DirectorBudgetExceeded
            # Priority is a transparent heuristic: hook, ending, then longer scenes.
            importance = {
                s.id: (
                    1.0
                    if i == 0
                    else 0.9
                    if i == len(scenes) - 1
                    else round(0.4 + 0.4 * float(s.duration / total), 6)
                )
                for i, s in enumerate(scenes)
            }
            ranked = sorted(scenes, key=lambda s: (-importance[s.id], s.position))
            cap = int(Decimal(str(style.video_scene_ratio)) * len(scenes))
            video_ids = set()
            for scene in ranked:
                extra = self.estimator.video_cost(scene.duration) - image_cost
                if len(video_ids) < cap and cost + extra <= visual_budget:
                    video_ids.add(scene.id)
                    cost += extra
            for priority, scene in enumerate(ranked, 1):
                scene.visual_type = VisualType.VIDEO if scene.id in video_ids else VisualType.IMAGE
                scene.camera_motion = "static" if scene.id in video_ids else "zoom_in"
                scene.visual_style = style.description
                suffix = "\nVisual style: " + style.description if style.description else ""
                base = scene.visual_prompt or scene.narration
                scene.visual_prompt = base[: 4000 - len(suffix)] + suffix
                scene.importance = importance[scene.id]
                scene.generation_priority = priority
                db.add(scene)
            plan = DirectorPlan(
                video_id=video_id,
                visual_budget_usd=visual_budget,
                estimated_cost_usd=cost,
                requested_video_ratio=style.video_scene_ratio,
                pricing_snapshot=self.estimator.snapshot().model_dump(mode="json"),
            )
            db.add(plan)
            db.flush()
            db.refresh(plan)
            result = read_plan(db, plan)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
