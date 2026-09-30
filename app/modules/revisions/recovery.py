from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import Session, select

from app.core.config import Settings
from app.modules.assets.models import Asset
from app.modules.panel.service import idle, lock_owner
from app.modules.revisions.models import RevisionStatus, TaskRecovery, VideoRevision
from app.modules.revisions.schemas import RecoveryInput, RecoveryRead
from app.modules.scripts.service import owned_video
from app.modules.tasks.models import Task, TaskKind, TaskStatus
from app.modules.tasks.schemas import TaskRead
from app.modules.videos.models import VideoStatus
from app.shared.generation import GenerationJobRef


def owned_task(db: Session, owner_id: UUID, task_id: UUID) -> Task:
    task = db.exec(
        select(Task).where(Task.id == task_id, Task.owner_id == owner_id).with_for_update()
    ).first()
    if task is None:
        raise HTTPException(404, "Task not found")
    return task


def history(db: Session, owner_id: UUID, task_id: UUID) -> list[RecoveryRead]:
    owned_task(db, owner_id, task_id)
    return [
        RecoveryRead.model_validate(r)
        for r in db.exec(
            select(TaskRecovery)
            .where(TaskRecovery.task_id == task_id)
            .order_by(TaskRecovery.number)
        ).all()
    ]


def recover(
    db: Session, owner_id: UUID, task_id: UUID, data: RecoveryInput, settings: Settings
) -> TaskRead:
    lock_owner(db, owner_id)
    task = owned_task(db, owner_id, task_id)
    if task.video_id is None:
        raise HTTPException(409, "Recovery currently supports video tasks")
    video = owned_video(db, owner_id, task.video_id, lock=True)
    if task.status not in (TaskStatus.FAILED, TaskStatus.NEEDS_REVIEW):
        raise HTTPException(409, "Only failed or uncertain tasks can be recovered")
    if task.checkpoint.get("recovery_abandoned"):
        raise HTTPException(409, "An abandoned task cannot be resumed")
    idle(db, video.id)
    if data.action != "abandon":
        from app.modules.revisions.service import check_revision_task

        try:
            check_revision_task(db, task)
        except ValueError:
            raise HTTPException(409, "Task belongs to an earlier or cancelled version") from None
    revision = db.exec(
        select(VideoRevision).where(
            VideoRevision.video_id == video.id, VideoRevision.status == RevisionStatus.PRODUCING
        )
    ).first()
    if data.action != "abandon" and (
        revision
        and task.parameters.get("revision_id") != str(revision.id)
        and not task.parameters.get("quality_check_id")
    ):
        raise HTTPException(409, "Task belongs to an earlier version")
    allowed = {
        TaskKind.DIRECT: {VideoStatus.SCRIPT_READY},
        TaskKind.IMAGE: {
            VideoStatus.SCRIPT_READY,
            VideoStatus.GENERATING_ASSETS,
            VideoStatus.QUALITY_CHECK,
        },
        TaskKind.VIDEO: {
            VideoStatus.SCRIPT_READY,
            VideoStatus.GENERATING_ASSETS,
            VideoStatus.QUALITY_CHECK,
        },
        TaskKind.AUDIO: {
            VideoStatus.SCRIPT_READY,
            VideoStatus.GENERATING_AUDIO,
            VideoStatus.QUALITY_CHECK,
        },
        TaskKind.RENDER: {
            VideoStatus.READY_TO_RENDER,
            VideoStatus.RENDERING,
            VideoStatus.QUALITY_CHECK,
        },
        TaskKind.QUALITY: {VideoStatus.QUALITY_CHECK, VideoStatus.READY, VideoStatus.FAILED},
        TaskKind.STORY: {VideoStatus.SCRIPT_READY},
        TaskKind.TOP5: {VideoStatus.SCRIPT_READY},
        TaskKind.RESEARCH: {VideoStatus.RESEARCHED},
    }
    if data.action != "abandon" and video.status not in allowed.get(task.kind, set()):
        raise HTTPException(409, "Task no longer matches the video stage")
    count = db.exec(
        select(func.count()).select_from(TaskRecovery).where(TaskRecovery.task_id == task.id)
    ).one()
    if data.action != "abandon" and (count >= 3 or task.attempts >= 10):
        raise HTTPException(409, "Recovery limit reached")
    checkpoint = dict(task.checkpoint)
    if data.action == "abandon":
        checkpoint["recovery_abandoned"] = True
    elif data.action == "use_asset":
        asset = db.get(Asset, data.asset_id)
        expected = checkpoint.get("budget_visual_kind", task.kind.value)
        if (
            task.kind not in (TaskKind.IMAGE, TaskKind.VIDEO, TaskKind.AUDIO)
            or asset is None
            or (
                asset.video_id != task.video_id
                or asset.scene_id != task.scene_id
                or asset.type.value != expected
            )
        ):
            raise HTTPException(409, "Choose a matching asset of this video and scene")
        checkpoint["recovery_result"] = {"asset_id": str(asset.id), "manually_recovered": True}
    elif task.kind in (TaskKind.IMAGE, TaskKind.VIDEO):
        if data.provider_job_id:
            if checkpoint.get("provider_job") or not checkpoint.get("submit_started"):
                raise HTTPException(409, "A saved provider job cannot be replaced")
            endpoint = (
                settings.runpod_image_endpoint_id
                if checkpoint.get("budget_visual_kind", task.kind.value) == "image"
                else settings.runpod_video_endpoint_id
            )
            if settings.external_providers_mode != "live" or not endpoint:
                raise HTTPException(409, "Configure the original live Runpod endpoint first")
            checkpoint["provider_job"] = GenerationJobRef(
                provider="runpod", endpoint_id=endpoint, job_id=data.provider_job_id
            ).model_dump(mode="json")
        if not (checkpoint.get("provider_job") or checkpoint.get("completed_visual")):
            raise HTTPException(
                409, "Unknown submission: provide its provider job ID or an existing asset"
            )
        checkpoint["recovery_poll_started_at"] = datetime.now(UTC).isoformat()
    else:
        if data.provider_job_id:
            raise HTTPException(409, "Provider job ID is supported only for visual tasks")
        from app.workers.operations import recover_result

        result = recover_result(db, task)
        if result is not None:
            checkpoint["recovery_result"] = result
        elif task.kind not in (TaskKind.DIRECT, TaskKind.QUALITY) and not (
            task.kind == TaskKind.RENDER and checkpoint.get("render_manifest")
        ):
            raise HTTPException(409, "No saved result to resume; attach an existing matching asset")
    now = datetime.now(UTC)
    db.add(
        TaskRecovery(
            task_id=task.id,
            actor_id=owner_id,
            number=count + 1,
            action=data.action,
            note=data.note,
            evidence={
                "previous_status": task.status.value,
                "asset_id": str(data.asset_id) if data.asset_id else None,
                "provider_job": checkpoint.get("provider_job"),
            },
        )
    )
    task.checkpoint = checkpoint
    task.status, task.error, task.error_category = TaskStatus.QUEUED, None, None
    task.completed_at, task.run_token = None, None
    if data.action == "abandon":
        task.status, task.error, task.completed_at = TaskStatus.FAILED, "manually_abandoned", now
        task.error_category = "review_required"
    task.available_at = task.delivery_after = now
    task.max_attempts = min(10, max(task.max_attempts, task.attempts + 1))
    db.add(task)
    db.commit()
    return TaskRead.model_validate(task)
