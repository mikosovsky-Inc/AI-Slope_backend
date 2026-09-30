"""Event-driven fan-out/fan-in using the existing transactional task outbox."""

from uuid import UUID

from fastapi import HTTPException
from sqlmodel import Session, select

from app.models.user import User
from app.modules.assets.models import Asset, AssetType
from app.modules.render.service import RenderManifest, SceneInput
from app.modules.scripts.service import owned_video
from app.modules.tasks.models import Task, TaskKind, TaskStatus
from app.modules.tasks.schemas import TaskRead
from app.modules.tasks.service import enqueue
from app.modules.videos.models import Scene, VideoScript, VideoStatus
from app.modules.videos.service import transition_video


def start_production(db: Session, owner_id: UUID, video_id: UUID) -> TaskRead:
    db.exec(select(User).where(User.id == owner_id).with_for_update()).one()
    video = owned_video(db, owner_id, video_id, lock=True)
    key = f"pipeline:{video_id}:direct"
    existing = db.exec(
        select(Task).where(Task.owner_id == owner_id, Task.idempotency_key == key)
    ).first()
    if existing:
        db.commit()
        return TaskRead.model_validate(existing)
    if video.status != VideoStatus.SCRIPT_READY:
        raise HTTPException(409, "Complete the script before starting production")
    return enqueue(
        db, owner_id, TaskKind.DIRECT, video_id=video_id, parameters={"workflow": True}, key=key
    )


def child_key(video_id: UUID, stage: str, scene_id: UUID) -> str:
    return f"pipeline:{video_id}:{stage}:{scene_id}"


def children(
    db: Session, video_id: UUID, stage: str, scenes: list[Scene], scope_id: UUID | None = None
) -> list[Task]:
    keys = [child_key(scope_id or video_id, stage, scene.id) for scene in scenes]
    return list(
        db.exec(select(Task).where(Task.video_id == video_id, Task.idempotency_key.in_(keys))).all()
    )


def completed(tasks: list[Task], count: int) -> bool:
    return len(tasks) == count and all(task.status == TaskStatus.SUCCEEDED for task in tasks)


def result_asset(
    db: Session, task: Task, expected: AssetType, *, identifier: str | None = None
) -> Asset:
    data = task.result or {}
    identifier = identifier or data.get("asset_id") or data.get("id")
    asset = db.get(Asset, UUID(identifier)) if identifier else None
    if (
        asset is None
        or asset.video_id != task.video_id
        or asset.scene_id != task.scene_id
        or asset.type != expected
    ):
        raise ValueError("Production task did not return the required scene asset")
    return asset


def advance_production(db: Session, task: Task) -> None:
    """Called after task success, under owner lock, before committing parent + children."""
    if not (
        task.parameters.get("pipeline")
        or (task.kind == TaskKind.DIRECT and task.parameters.get("workflow"))
    ):
        return
    video = owned_video(db, task.owner_id, task.video_id, lock=True)
    from app.modules.revisions.service import revision_parameters

    revision_id = task.parameters.get("revision_id")
    scope_id = UUID(revision_id) if revision_id else video.id
    scenes = list(
        db.exec(
            select(Scene)
            .join(VideoScript)
            .where(VideoScript.video_id == video.id)
            .order_by(Scene.position)
        ).all()
    )
    if not scenes:
        raise ValueError("Production requires scenes")
    if task.kind == TaskKind.DIRECT and video.status == VideoStatus.SCRIPT_READY:
        transition_video(
            db,
            video.id,
            VideoStatus.GENERATING_ASSETS,
            reason="Automatic visual generation started",
        )
        for scene in scenes:
            if scene.visual_type.value not in ("image", "video"):
                raise ValueError("Production requires image or video scenes")
            enqueue(
                db,
                task.owner_id,
                TaskKind(scene.visual_type.value),
                video_id=video.id,
                scene_id=scene.id,
                parameters=revision_parameters(db, task, scene, TaskKind(scene.visual_type.value)),
                key=child_key(scope_id, "visual", scene.id),
                commit=False,
            )
    visuals = children(db, video.id, "visual", scenes, scope_id)
    if video.status == VideoStatus.GENERATING_ASSETS and completed(visuals, len(scenes)):
        for child in visuals:
            scene = next(s for s in scenes if s.id == child.scene_id)
            result_asset(db, child, AssetType(scene.visual_type.value))
        transition_video(
            db, video.id, VideoStatus.ASSETS_READY, reason="All automatic visuals completed"
        )
        transition_video(
            db, video.id, VideoStatus.GENERATING_AUDIO, reason="Automatic narration started"
        )
        for scene in scenes:
            enqueue(
                db,
                task.owner_id,
                TaskKind.AUDIO,
                video_id=video.id,
                scene_id=scene.id,
                parameters=revision_parameters(db, task, scene, TaskKind.AUDIO),
                key=child_key(scope_id, "audio", scene.id),
                commit=False,
            )
    audio = children(db, video.id, "audio", scenes, scope_id)
    if video.status == VideoStatus.GENERATING_AUDIO and completed(audio, len(scenes)):
        if not completed(visuals, len(scenes)):
            raise ValueError("Production visual jobs changed")
        inputs = []
        for scene in scenes:
            visual = result_asset(
                db,
                next(t for t in visuals if t.scene_id == scene.id),
                AssetType(scene.visual_type.value),
            )
            narration = result_asset(
                db, next(t for t in audio if t.scene_id == scene.id), AssetType.AUDIO
            )
            inputs.append(
                SceneInput(
                    scene_id=scene.id,
                    visual_asset_id=visual.id,
                    audio_asset_id=narration.id,
                    duration=float(scene.duration),
                    narration=scene.narration,
                    motion=scene.camera_motion,
                )
            )
        manifest = RenderManifest(scenes=inputs)
        transition_video(
            db, video.id, VideoStatus.READY_TO_RENDER, reason="All automatic narration completed"
        )
        enqueue(
            db,
            task.owner_id,
            TaskKind.RENDER,
            video_id=video.id,
            parameters={"input_manifest": manifest.model_dump(mode="json")}
            | ({"revision_id": revision_id} if revision_id else {}),
            key=f"pipeline:{scope_id}:render",
            commit=False,
        )
