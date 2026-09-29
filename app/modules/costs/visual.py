from sqlmodel import Session, select

from app.core.config import Settings
from app.modules.costs.models import CostEvent
from app.modules.costs.service import BudgetService
from app.modules.director.pricing import ConfiguredVisualEstimator, VisualEstimates
from app.modules.scripts.service import owned_video
from app.modules.tasks.models import Task, TaskKind
from app.modules.videos.models import Scene, VideoScript, VideoStatus, VisualType


def prepare_visual(db: Session, task: Task, settings: Settings) -> None:
    if settings.external_providers_mode == "mock":
        return
    video = owned_video(db, task.owner_id, task.video_id, lock=True)
    if db.exec(
        select(CostEvent.id).where(
            CostEvent.video_id == video.id, CostEvent.operation_key == f"visual:{task.id}"
        )
    ).first():
        db.commit()
        return
    scene = db.exec(
        select(Scene)
        .join(VideoScript)
        .where(Scene.id == task.scene_id, VideoScript.video_id == video.id)
    ).one()
    estimator = ConfiguredVisualEstimator(
        VisualEstimates(
            image_usd=settings.director_image_estimate_usd,
            video_second_usd=settings.director_video_second_estimate_usd,
        )
    )
    kind = TaskKind(task.checkpoint.get("budget_visual_kind", task.kind.value))
    amount = (
        estimator.image_cost() if kind == TaskKind.IMAGE else estimator.video_cost(scene.duration)
    )
    # Never alter an already submitted request or the repair type in a frozen QC manifest.
    if (
        kind == TaskKind.VIDEO
        and video.status == VideoStatus.SCRIPT_READY
        and scene.visual_type == VisualType.VIDEO
        and not task.checkpoint.get("submit_started")
        and not task.parameters.get("quality_check_id")
        and not BudgetService.can_spend(db, video, amount)
        and BudgetService.can_spend(db, video, estimator.image_cost())
    ):
        kind, amount = TaskKind.IMAGE, estimator.image_cost()
        scene.visual_type, scene.camera_motion = VisualType.IMAGE, "zoom_in"
        db.add(scene)
    task.checkpoint = task.checkpoint | {"budget_visual_kind": kind.value}
    db.add(task)
    BudgetService.reserve(
        db,
        video.id,
        key=f"visual:{task.id}",
        amount=amount,
        provider="runpod",
        operation=kind.value,
        model=settings.runpod_image_model
        if kind == TaskKind.IMAGE
        else settings.runpod_video_model,
    )
    db.commit()
