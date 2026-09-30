from contextlib import ExitStack
from uuid import UUID

from sqlmodel import Session, select

from app.core.config import Settings
from app.core.observability import ObservedProvider
from app.integrations.elevenlabs.factory import create_tts_provider
from app.integrations.llm.factory import create_llm_provider
from app.integrations.runpod.factory import create_generation_provider
from app.integrations.storage.factory import create_storage_provider
from app.modules.audio.service import generate_scene_audio
from app.modules.competitors.dependencies import get_competitor_research_provider
from app.modules.competitors.service import research_competitors
from app.modules.director.dependencies import get_director
from app.modules.ideas.service import generate_ideas
from app.modules.intelligence.service import analyze_channel
from app.modules.research.provider import get_research_provider
from app.modules.research.service import generate_top5, run_research
from app.modules.scripts.service import generate_script, owned_video
from app.modules.tasks.models import Task, TaskKind
from app.modules.videos.models import Scene, VideoScript
from app.shared.generation import (
    GenerationJobRef,
    ImageGenerationRequest,
    ProviderJobStatus,
    VideoGenerationRequest,
)


class PollLater(Exception):
    pass


class ReviewRequired(Exception):
    pass


def execute_operation(db: Session, task: Task, settings: Settings) -> dict:
    from app.modules.revisions.service import check_revision_task

    check_revision_task(db, task)
    if task.checkpoint.get("recovery_result") is not None:
        return task.checkpoint["recovery_result"]
    if task.parameters.get("reuse_asset_id"):
        from app.modules.assets.models import AssetType
        from app.modules.production.service import result_asset

        # Reused media remains scoped to the same owned video and scene.
        owned_video(db, task.owner_id, task.video_id)
        asset = result_asset(
            db, task, AssetType(task.kind.value), identifier=task.parameters["reuse_asset_id"]
        )
        return {"asset_id": str(asset.id), "reused": True}

    def managed(stack, factory):
        provider = factory(settings)
        stack.callback(provider.close)
        return ObservedProvider(provider, factory.__name__.removeprefix("create_"))

    if task.parameters.get("scheduler_plan_id"):
        from app.modules.scheduler.service import scheduled_task_skip_reason

        reason = scheduled_task_skip_reason(db, task, settings)
        if reason:
            return {"skipped": True, "reason": reason}
    if task.parameters.get("quality_check_id"):
        from app.modules.quality.models import QualityCheck, QualityStatus
        from app.modules.videos.models import VideoStatus

        check = db.get(QualityCheck, UUID(task.parameters["quality_check_id"]))
        video = owned_video(db, task.owner_id, task.video_id)
        if (
            check is None
            or check.video_id != task.video_id
            or check.status != QualityStatus.REPAIRING
            or video.status != VideoStatus.QUALITY_CHECK
            or not any(item["task_id"] == str(task.id) for item in check.repair_tasks)
        ):
            raise ValueError("Scene repair is no longer authorized by its quality check")
        db.commit()
    with ExitStack() as stack:
        kind = task.kind
        args = (db, task.owner_id, task.video_id)
        if kind in (
            TaskKind.ANALYZE,
            TaskKind.IDEAS,
            TaskKind.STORY,
            TaskKind.RESEARCH,
            TaskKind.TOP5,
        ):
            llm = managed(stack, create_llm_provider)
            if task.video_id:
                from app.modules.costs.llm import BudgetedLLM

                llm = BudgetedLLM(llm, db, task.video_id, settings)
        if kind == TaskKind.ANALYZE:
            result = analyze_channel(db, task.owner_id, task.channel_id, llm)
        elif kind == TaskKind.IDEAS:
            result = generate_ideas(
                db, task.owner_id, task.channel_id, task.parameters.get("count", 10), llm
            )
        elif kind == TaskKind.COMPETITORS:
            result = research_competitors(
                db, task.owner_id, task.channel_id, get_competitor_research_provider()
            )
        elif kind == TaskKind.STORY:
            result = generate_script(*args, llm)
        elif kind == TaskKind.RESEARCH:
            from app.modules.production.fixtures import demo_research

            research = (
                demo_research(settings)
                if task.parameters.get("demo_research")
                else get_research_provider()
            )
            result = run_research(*args, llm, research)
        elif kind == TaskKind.TOP5:
            result = generate_top5(*args, llm)
        elif kind == TaskKind.DIRECT:
            result = get_director(settings).direct(*args)
        elif kind == TaskKind.AUDIO:
            result = generate_scene_audio(
                *args,
                task.scene_id,
                managed(stack, create_tts_provider),
                managed(stack, create_storage_provider),
                settings,
                regeneration_id=UUID(task.parameters["quality_check_id"])
                if task.parameters.get("quality_check_id")
                else (
                    task.id
                    if task.parameters.get("panel_regeneration")
                    or task.parameters.get("revision_id")
                    else None
                ),
            )
        elif kind == TaskKind.QUALITY:
            from app.modules.quality.provider import create_visual_quality_provider
            from app.modules.quality.service import QualityService

            visual_provider = create_visual_quality_provider()
            stack.callback(visual_provider.close)
            return QualityService(
                settings, managed(stack, create_storage_provider), visual_provider
            ).run(db, task)
        elif kind == TaskKind.RENDER:
            from app.modules.render.service import RenderService

            return RenderService(settings, managed(stack, create_storage_provider)).render(db, task)
        elif kind in (TaskKind.IMAGE, TaskKind.VIDEO):
            return visual(db, task, managed(stack, create_generation_provider), settings)
        else:
            raise ValueError("Unsupported task kind")
        return result.model_dump(mode="json")


def visual(db: Session, task: Task, provider, settings: Settings) -> dict:
    from app.modules.costs.visual import prepare_visual

    prepare_visual(db, task, settings)
    owned_video(db, task.owner_id, task.video_id)
    scene = db.exec(
        select(Scene)
        .join(VideoScript)
        .where(
            Scene.id == task.scene_id,
            VideoScript.video_id == task.video_id,
        )
    ).one()
    from app.modules.render.visuals import materialize_visual, output_key, visual_kind

    kind = visual_kind(task)

    data = {
        "request_id": str(task.id),
        "prompt": scene.visual_prompt,
        "parameters": {"output_key": output_key(task), "output_bucket": settings.s3_bucket},
    }
    completed = task.checkpoint.get("completed_visual")
    if completed is not None:
        storage = create_storage_provider(settings)
        try:
            asset = materialize_visual(
                db, task, settings, storage, completed, float(scene.duration)
            )
            return {"asset_id": str(asset.id), "status": "succeeded"}
        finally:
            storage.close()
    reference = task.checkpoint.get("provider_job")
    if reference and settings.external_providers_mode == "mock":
        # Mock jobs are process-local and free; reconstruct after a worker restart.
        result = (
            provider.generate_image(ImageGenerationRequest(**data))
            if kind == TaskKind.IMAGE
            else provider.generate_video(
                VideoGenerationRequest(**data, duration_seconds=float(scene.duration))
            )
        )
        result = provider.get_status(result.job)
        result = provider.get_status(result.job)
    elif reference:
        result = provider.get_status(GenerationJobRef.model_validate(reference))
    else:
        if task.checkpoint.get("submit_started"):
            raise ReviewRequired
        task.checkpoint = task.checkpoint | {"submit_started": True}
        db.add(task)
        db.commit()
        result = (
            provider.generate_image(ImageGenerationRequest(**data))
            if kind == TaskKind.IMAGE
            else provider.generate_video(
                VideoGenerationRequest(**data, duration_seconds=float(scene.duration))
            )
        )
        task.checkpoint = task.checkpoint | {"provider_job": result.job.model_dump(mode="json")}
        db.add(task)
        db.commit()
        if settings.external_providers_mode == "mock":
            result = provider.get_status(result.job)
            result = provider.get_status(result.job)
    if result.status in (ProviderJobStatus.QUEUED, ProviderJobStatus.RUNNING):
        raise PollLater
    if result.status != ProviderJobStatus.SUCCEEDED:
        raise ValueError("Provider generation failed")
    task.checkpoint = task.checkpoint | {"completed_visual": result.output or {}}
    db.add(task)
    db.commit()
    storage = create_storage_provider(settings)
    try:
        asset = materialize_visual(
            db, task, settings, storage, result.output or {}, float(scene.duration)
        )
        return result.model_dump(mode="json") | {"asset_id": str(asset.id)}
    finally:
        storage.close()


def recover_result(db: Session, task: Task) -> dict | None:
    """Recover committed domain results without repeating an uncertain external call."""
    from app.modules.revisions.service import check_revision_task
    from app.modules.scripts.service import get_script
    from app.modules.videos.models import Video, VideoStatus

    check_revision_task(db, task)

    if task.checkpoint.get("recovery_result") is not None:
        return task.checkpoint["recovery_result"]
    if task.kind in (TaskKind.STORY, TaskKind.TOP5):
        script = db.exec(select(VideoScript).where(VideoScript.video_id == task.video_id)).first()
        if script:
            return get_script(db, task.owner_id, task.video_id).model_dump(mode="json")
    if task.kind == TaskKind.RESEARCH:
        video = db.get(Video, task.video_id)
        if video and video.status == VideoStatus.RESEARCHED:
            from app.modules.research.service import read_research

            return read_research(db, task.owner_id, task.video_id).model_dump(mode="json")
    return None
