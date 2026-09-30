from copy import deepcopy
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import Session, select

from app.modules.panel.service import idle, lock_owner, scenes
from app.modules.quality.models import QualityCheck, QualityStatus
from app.modules.revisions.models import RevisionStatus, VideoRevision
from app.modules.revisions.schemas import RevisionPatch, RevisionRead
from app.modules.scripts.service import owned_video
from app.modules.tasks.models import Task, TaskKind, TaskStatus
from app.modules.tasks.service import enqueue
from app.modules.videos.models import Scene, VideoStatus, VideoStatusEvent


def owned_revision(db: Session, owner_id: UUID, video_id: UUID, revision_id: UUID) -> VideoRevision:
    owned_video(db, owner_id, video_id)
    revision = db.get(VideoRevision, revision_id)
    if revision is None or revision.video_id != video_id:
        raise HTTPException(404, "Revision not found")
    return revision


def list_revisions(db: Session, owner_id: UUID, video_id: UUID) -> list[RevisionRead]:
    owned_video(db, owner_id, video_id)
    return [
        RevisionRead.model_validate(r)
        for r in db.exec(
            select(VideoRevision)
            .where(VideoRevision.video_id == video_id)
            .order_by(VideoRevision.number)
        ).all()
    ]


def create_revision(db: Session, owner_id: UUID, video_id: UUID) -> RevisionRead:
    lock_owner(db, owner_id)
    video = owned_video(db, owner_id, video_id, lock=True)
    idle(db, video_id)
    if video.status != VideoStatus.READY:
        raise HTTPException(409, "Create a revision of a READY video")
    if db.exec(
        select(VideoRevision.id).where(
            VideoRevision.video_id == video_id, VideoRevision.status == RevisionStatus.PRODUCING
        )
    ).first():
        raise HTTPException(409, "Complete or cancel the previous revision first")
    existing = db.exec(
        select(VideoRevision).where(
            VideoRevision.video_id == video_id, VideoRevision.status == RevisionStatus.DRAFT
        )
    ).first()
    if existing:
        db.commit()
        return RevisionRead.model_validate(existing)
    baseline = db.exec(
        select(VideoRevision)
        .where(VideoRevision.video_id == video_id, VideoRevision.status == RevisionStatus.READY)
        .order_by(VideoRevision.number.desc())
    ).first()
    query = select(QualityCheck).where(
        QualityCheck.video_id == video_id, QualityCheck.status == QualityStatus.PASSED
    )
    if baseline:
        query = query.where(QualityCheck.render_task_id == baseline.render_task_id)
    check = db.exec(query.order_by(QualityCheck.attempt.desc())).first()
    if check is None or check.final_asset_id is None:
        raise HTTPException(409, "Revision requires a completed render with passed QC")
    snapshot = [s.model_dump(mode="json") for s in scenes(db, owner_id, video_id)]
    number = (
        db.exec(
            select(func.max(VideoRevision.number)).where(VideoRevision.video_id == video_id)
        ).one()
        or 0
    )
    if number == 0:
        db.add(
            VideoRevision(
                video_id=video_id,
                number=1,
                status=RevisionStatus.READY,
                base_render_id=check.render_task_id,
                render_task_id=check.render_task_id,
                final_asset_id=check.final_asset_id,
                scenes=deepcopy(snapshot),
            )
        )
        number = 1
    revision = VideoRevision(
        video_id=video_id, number=number + 1, base_render_id=check.render_task_id, scenes=snapshot
    )
    db.add(revision)
    db.commit()
    return RevisionRead.model_validate(revision)


def edit_revision(
    db: Session,
    owner_id: UUID,
    video_id: UUID,
    revision_id: UUID,
    scene_id: UUID,
    data: RevisionPatch,
) -> RevisionRead:
    lock_owner(db, owner_id)
    video = owned_video(db, owner_id, video_id, lock=True)
    revision = owned_revision(db, owner_id, video_id, revision_id)
    if revision.status != RevisionStatus.DRAFT:
        raise HTTPException(409, "Only draft revisions can be edited")
    if video.format.value == "top5" and "narration" in data.model_fields_set:
        raise HTTPException(409, "TOP5 narration must retain its verified facts")
    snapshot = deepcopy(revision.scenes)
    scene = next((s for s in snapshot if s["id"] == str(scene_id)), None)
    if scene is None:
        raise HTTPException(404, "Scene not found in revision")
    scene.update(data.model_dump(mode="json", exclude_unset=True))
    revision.scenes = snapshot
    db.add(revision)
    db.commit()
    return RevisionRead.model_validate(revision)


def submit_revision(db: Session, owner_id: UUID, video_id: UUID, revision_id: UUID) -> RevisionRead:
    lock_owner(db, owner_id)
    video = owned_video(db, owner_id, video_id, lock=True)
    revision = owned_revision(db, owner_id, video_id, revision_id)
    if revision.status in (RevisionStatus.PRODUCING, RevisionStatus.READY):
        db.commit()
        return RevisionRead.model_validate(revision)
    if revision.status != RevisionStatus.DRAFT or video.status != VideoStatus.READY:
        raise HTTPException(409, "Submit a draft while the video is READY")
    idle(db, video_id)
    if db.exec(
        select(Task.id).where(Task.video_id == video_id, Task.status == TaskStatus.NEEDS_REVIEW)
    ).first():
        raise HTTPException(409, "Resolve uncertain tasks before producing another version")
    # Freeze the snapshot; retain old versions and assets when changing working scenes.
    for saved in revision.scenes:
        scene = db.get(Scene, UUID(saved["id"]))
        for field in ("narration", "visual_prompt", "mood", "caption_emphasis", "camera_motion"):
            setattr(scene, field, saved[field])
        db.add(scene)
    sequence = (
        db.exec(
            select(func.max(VideoStatusEvent.sequence)).where(VideoStatusEvent.video_id == video_id)
        ).one()
        or 0
    )
    db.add(
        VideoStatusEvent(
            video_id=video_id,
            sequence=sequence + 1,
            from_status=video.status,
            to_status=VideoStatus.SCRIPT_READY,
            reason=f"Produce revision {revision.number}",
        )
    )
    video.status, video.updated_at = VideoStatus.SCRIPT_READY, datetime.now(UTC)
    revision.status = RevisionStatus.PRODUCING
    db.add(video)
    db.add(revision)
    enqueue(
        db,
        owner_id,
        TaskKind.DIRECT,
        video_id=video_id,
        parameters={"workflow": True, "revision_id": str(revision.id)},
        key=f"revision:{revision.id}:direct",
        commit=False,
    )
    db.commit()
    return RevisionRead.model_validate(revision)


def revision_parameters(db: Session, task: Task, scene: Scene, kind: TaskKind) -> dict:
    identifier = task.parameters.get("revision_id")
    if not identifier:
        return {"pipeline": True}
    from app.modules.render.service import RenderManifest

    revision = db.get(VideoRevision, UUID(identifier))
    if (
        revision is None
        or revision.video_id != task.video_id
        or revision.status != RevisionStatus.PRODUCING
    ):
        raise ValueError("Revision is no longer producing")
    base = db.get(Task, revision.base_render_id)
    manifest = RenderManifest.model_validate(base.checkpoint["render_manifest"])
    source = next(s for s in manifest.scenes if s.scene_id == scene.id)
    baseline = db.exec(
        select(VideoRevision).where(
            VideoRevision.video_id == task.video_id,
            VideoRevision.render_task_id == base.id,
            VideoRevision.status == RevisionStatus.READY,
        )
    ).one()
    previous = next(s for s in baseline.scenes if s["id"] == str(scene.id))
    unchanged = (
        scene.narration == previous["narration"]
        if kind == TaskKind.AUDIO
        else scene.visual_prompt == previous["visual_prompt"]
        and scene.visual_type.value == previous["visual_type"]
        and scene.mood == previous["mood"]
    )
    result = {"pipeline": True, "revision_id": identifier}
    if unchanged:
        result["reuse_asset_id"] = str(
            source.audio_asset_id if kind == TaskKind.AUDIO else source.visual_asset_id
        )
    return result


def finish_revision(db: Session, task: Task) -> None:
    if task.kind != TaskKind.QUALITY or not task.parameters.get("revision_id"):
        return
    revision = db.get(VideoRevision, UUID(task.parameters["revision_id"]))
    check = db.exec(select(QualityCheck).where(QualityCheck.task_id == task.id)).first()
    if (
        revision
        and revision.status == RevisionStatus.PRODUCING
        and check
        and check.status == QualityStatus.PASSED
    ):
        revision.status = RevisionStatus.READY
        revision.render_task_id = check.render_task_id
        revision.final_asset_id = check.final_asset_id
        revision.scenes = [
            s.model_dump(mode="json") for s in scenes(db, task.owner_id, task.video_id)
        ]
        db.add(revision)


def check_revision_task(db: Session, task: Task) -> None:
    """Reject old-version work before it can invoke a provider or change current scenes."""
    if task.video_id is None:
        return
    identifier = task.parameters.get("revision_id")
    if task.parameters.get("quality_check_id"):
        check = db.get(QualityCheck, UUID(task.parameters["quality_check_id"]))
        render = db.get(Task, check.render_task_id) if check else None
        identifier = render.parameters.get("revision_id") if render else None
    active = db.exec(
        select(VideoRevision).where(
            VideoRevision.video_id == task.video_id,
            VideoRevision.status == RevisionStatus.PRODUCING,
        )
    ).first()
    if active and identifier != str(active.id):
        raise ValueError("Task belongs to an earlier video version")
    if identifier:
        revision = db.get(VideoRevision, UUID(identifier))
        if (
            revision is None
            or revision.video_id != task.video_id
            or revision.status != RevisionStatus.PRODUCING
        ):
            raise ValueError("Revision is no longer executable")


def cancel_revision(db: Session, owner_id: UUID, video_id: UUID, revision_id: UUID) -> RevisionRead:
    lock_owner(db, owner_id)
    video = owned_video(db, owner_id, video_id, lock=True)
    revision = owned_revision(db, owner_id, video_id, revision_id)
    if revision.status == RevisionStatus.READY:
        raise HTTPException(409, "Completed versions cannot be cancelled")
    if revision.status == RevisionStatus.PRODUCING:
        idle(db, video_id)
        if db.exec(
            select(Task.id).where(Task.video_id == video_id, Task.status == TaskStatus.NEEDS_REVIEW)
        ).first():
            raise HTTPException(409, "Resolve or abandon uncertain tasks first")
        baseline = db.exec(
            select(VideoRevision).where(
                VideoRevision.video_id == video_id,
                VideoRevision.render_task_id == revision.base_render_id,
                VideoRevision.status == RevisionStatus.READY,
            )
        ).one()
        for saved in baseline.scenes:
            from app.modules.panel.schemas import SceneRead

            scene = db.get(Scene, UUID(saved["id"]))
            for field, value in SceneRead.model_validate(saved).model_dump().items():
                if field not in ("id", "script_id"):
                    setattr(scene, field, value)
            db.add(scene)
        sequence = (
            db.exec(
                select(func.max(VideoStatusEvent.sequence)).where(
                    VideoStatusEvent.video_id == video_id
                )
            ).one()
            or 0
        )
        db.add(
            VideoStatusEvent(
                video_id=video_id,
                sequence=sequence + 1,
                from_status=video.status,
                to_status=VideoStatus.READY,
                reason=f"Cancel revision {revision.number}; restore completed baseline",
            )
        )
        video.status, video.updated_at = VideoStatus.READY, datetime.now(UTC)
        db.add(video)
    revision.status = RevisionStatus.CANCELLED
    db.add(revision)
    db.commit()
    return RevisionRead.model_validate(revision)
