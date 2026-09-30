from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import Session, select
from sqlmodel.sql.expression import SelectOfScalar

from app.models.user import User
from app.modules.assets.models import Asset, GenerationJob
from app.modules.channels.models import Channel, ChannelStatus
from app.modules.channels.service import detail, owned_channel
from app.modules.costs.models import CostEvent
from app.modules.ideas.models import ContentIdea
from app.modules.panel.schemas import (
    CostPage,
    CostRead,
    CostSummary,
    Dashboard,
    Overview,
    PlanRead,
    ScenePatch,
    SceneRead,
    StatusRead,
    VideoPage,
)
from app.modules.scheduler.models import DailyPlan
from app.modules.scripts.service import owned_video
from app.modules.tasks.models import Task, TaskKind, TaskStatus
from app.modules.tasks.schemas import TaskRead
from app.modules.tasks.service import enqueue
from app.modules.videos.models import Scene, Video, VideoScript, VideoStatus, VideoStatusEvent
from app.modules.videos.schemas import VideoRead

ACTIVE = (TaskStatus.QUEUED, TaskStatus.RUNNING)


def cost_summary(db: Session, owner_id: UUID, channel_id: UUID | None = None) -> CostSummary:
    query = (
        select(
            func.count(CostEvent.id),
            func.coalesce(func.sum(CostEvent.estimated_cost_usd), 0),
            func.coalesce(func.sum(CostEvent.actual_cost_usd), 0),
            func.coalesce(
                func.sum(func.coalesce(CostEvent.actual_cost_usd, CostEvent.estimated_cost_usd)), 0
            ),
            func.count(CostEvent.id) - func.count(CostEvent.actual_cost_usd),
        )
        .join(Channel)
        .where(Channel.owner_id == owner_id)
    )
    if channel_id:
        query = query.where(CostEvent.channel_id == channel_id)
    count, estimated, actual, effective, pending = db.exec(query).one()
    return CostSummary(
        events=count,
        estimated_usd=estimated,
        actual_usd=actual,
        effective_usd=effective,
        pending_actual_events=pending,
    )


def videos_query(owner_id: UUID, channel_id: UUID | None = None) -> SelectOfScalar[Video]:
    query = select(Video).join(ContentIdea).join(Channel).where(Channel.owner_id == owner_id)
    return query.where(Channel.id == channel_id) if channel_id else query


def video_counts(
    db: Session, owner_id: UUID, channel_id: UUID | None = None
) -> dict[VideoStatus, int]:
    query = (
        select(Video.status, func.count(Video.id))
        .join(ContentIdea)
        .join(Channel)
        .where(Channel.owner_id == owner_id)
    )
    if channel_id:
        query = query.where(Channel.id == channel_id)
    counts = dict(db.exec(query.group_by(Video.status)).all())
    return {status: counts.get(status, 0) for status in VideoStatus}


def dashboard(db: Session, owner_id: UUID) -> Dashboard:
    channels = dict(
        db.exec(
            select(Channel.status, func.count(Channel.id))
            .where(Channel.owner_id == owner_id)
            .group_by(Channel.status)
        ).all()
    )
    counts = video_counts(db, owner_id)
    recent = db.exec(
        videos_query(owner_id).order_by(Video.created_at.desc(), Video.id.desc()).limit(10)
    ).all()
    return Dashboard(
        channels=sum(channels.values()),
        active_channels=channels.get(ChannelStatus.ACTIVE, 0),
        videos=sum(counts.values()),
        videos_by_status=counts,
        costs=cost_summary(db, owner_id),
        recent_videos=[VideoRead.model_validate(v) for v in recent],
    )


def overview(db: Session, owner_id: UUID, channel_id: UUID) -> Overview:
    channel = owned_channel(db, owner_id, channel_id)
    ideas = dict(
        db.exec(
            select(ContentIdea.status, func.count(ContentIdea.id))
            .where(ContentIdea.channel_id == channel_id)
            .group_by(ContentIdea.status)
        ).all()
    )
    plan = db.exec(
        select(DailyPlan)
        .where(DailyPlan.channel_id == channel_id)
        .order_by(DailyPlan.day.desc())
        .limit(1)
    ).first()
    return Overview(
        channel=detail(db, channel),
        videos_by_status=video_counts(db, owner_id, channel_id),
        ideas_by_status=ideas,
        costs=cost_summary(db, owner_id, channel_id),
        latest_plan=PlanRead(
            day=plan.day.isoformat(), status=plan.status, target=plan.target, reason=plan.reason
        )
        if plan
        else None,
    )


def list_videos(
    db: Session,
    owner_id: UUID,
    channel_id: UUID,
    limit: int,
    offset: int,
    status: VideoStatus | None,
) -> VideoPage:
    owned_channel(db, owner_id, channel_id)
    query = videos_query(owner_id, channel_id)
    if status:
        query = query.where(Video.status == status)
    total = db.exec(select(func.count()).select_from(query.subquery())).one()
    rows = db.exec(
        query.order_by(Video.created_at.desc(), Video.id.desc()).offset(offset).limit(limit)
    ).all()
    return VideoPage(
        items=[VideoRead.model_validate(v) for v in rows], total=total, limit=limit, offset=offset
    )


def costs(db: Session, owner_id: UUID, channel_id: UUID, limit: int, offset: int) -> CostPage:
    owned_channel(db, owner_id, channel_id)
    summary = cost_summary(db, owner_id, channel_id)
    rows = db.exec(
        select(CostEvent)
        .where(CostEvent.channel_id == channel_id)
        .order_by(CostEvent.created_at.desc(), CostEvent.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return CostPage(
        summary=summary,
        items=[CostRead.model_validate(c) for c in rows],
        total=summary.events,
        limit=limit,
        offset=offset,
    )


def scenes(db: Session, owner_id: UUID, video_id: UUID) -> list[SceneRead]:
    owned_video(db, owner_id, video_id)
    rows = db.exec(
        select(Scene)
        .join(VideoScript)
        .where(VideoScript.video_id == video_id)
        .order_by(Scene.position)
    ).all()
    return [SceneRead.model_validate(s) for s in rows]


def status(db: Session, owner_id: UUID, video_id: UUID) -> StatusRead:
    video = owned_video(db, owner_id, video_id)
    tasks = db.exec(
        select(Task)
        .where(Task.video_id == video_id, Task.owner_id == owner_id)
        .order_by(Task.created_at.desc(), Task.id.desc())
        .limit(100)
    ).all()
    active = db.exec(
        select(Task.id).where(Task.video_id == video_id, Task.status.in_(ACTIVE))
    ).first()
    return StatusRead(
        video_id=video.id,
        status=video.status,
        updated_at=video.updated_at,
        tasks=[TaskRead.model_validate(t) for t in tasks],
        has_active_tasks=active is not None,
    )


def lock_owner(db: Session, owner_id: UUID) -> None:
    db.exec(select(User).where(User.id == owner_id).with_for_update()).one()


def idle(db: Session, video_id: UUID) -> None:
    if db.exec(select(Task.id).where(Task.video_id == video_id, Task.status.in_(ACTIVE))).first():
        raise HTTPException(409, "Wait for active video tasks to finish")


def owned_scene(db: Session, owner_id: UUID, scene_id: UUID) -> tuple[Scene, Video]:
    row = db.exec(
        select(Scene, VideoScript.video_id)
        .join(VideoScript)
        .join(Video)
        .join(ContentIdea)
        .join(Channel)
        .where(Scene.id == scene_id, Channel.owner_id == owner_id)
    ).first()
    if row is None:
        raise HTTPException(404, "Scene not found")
    scene, video_id = row
    video = owned_video(db, owner_id, video_id, lock=True)
    db.refresh(scene)
    return scene, video


def edit_scene(db: Session, owner_id: UUID, scene_id: UUID, data: ScenePatch) -> SceneRead:
    lock_owner(db, owner_id)
    scene, video = owned_scene(db, owner_id, scene_id)
    idle(db, video.id)
    if video.status != VideoStatus.SCRIPT_READY:
        raise HTTPException(409, "Edit scenes before rendering, at SCRIPT_READY")
    if (
        db.exec(select(Asset.id).where(Asset.scene_id == scene.id)).first()
        or db.exec(select(GenerationJob.id).where(GenerationJob.scene_id == scene.id)).first()
        or db.exec(select(Task.id).where(Task.scene_id == scene.id)).first()
    ):
        raise HTTPException(
            409, "Scene already has generation history; editing would invalidate assets"
        )
    if video.format.value == "top5" and "narration" in data.model_fields_set:
        raise HTTPException(409, "TOP5 narration must remain linked to verified research facts")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(scene, field, value)
    video.updated_at = datetime.now(UTC)
    db.add(scene)
    db.add(video)
    db.commit()
    return SceneRead.model_validate(scene)


def regenerate(db: Session, owner_id: UUID, scene_id: UUID, kind: str, key: str | None) -> TaskRead:
    lock_owner(db, owner_id)
    scene, video = owned_scene(db, owner_id, scene_id)
    parameters = {"panel_regeneration": True}
    if key:
        existing = db.exec(
            select(Task).where(Task.owner_id == owner_id, Task.idempotency_key == key)
        ).first()
        if existing:
            return enqueue(
                db,
                owner_id,
                TaskKind(kind),
                video_id=video.id,
                scene_id=scene.id,
                parameters=parameters,
                key=key,
            )
    idle(db, video.id)
    if video.status != VideoStatus.SCRIPT_READY:
        raise HTTPException(409, "Regenerate scenes at SCRIPT_READY, before rendering")
    if kind != "audio" and scene.visual_type.value != kind:
        raise HTTPException(409, "Generation kind must match the scene visual type")
    if db.exec(
        select(Task.id).where(
            Task.scene_id == scene.id,
            Task.kind == TaskKind(kind),
            Task.status == TaskStatus.NEEDS_REVIEW,
        )
    ).first():
        raise HTTPException(409, "Resolve the uncertain generation before creating another one")
    return enqueue(
        db,
        owner_id,
        TaskKind(kind),
        video_id=video.id,
        scene_id=scene.id,
        parameters=parameters,
        key=key,
    )


def retry(db: Session, owner_id: UUID, video_id: UUID, task_id: UUID) -> TaskRead:
    lock_owner(db, owner_id)
    video = owned_video(db, owner_id, video_id, lock=True)
    task = db.exec(
        select(Task)
        .where(Task.id == task_id, Task.video_id == video_id, Task.owner_id == owner_id)
        .with_for_update()
    ).first()
    if task is None:
        raise HTTPException(404, "Task not found")
    if task.status in ACTIVE:
        db.commit()
        return TaskRead.model_validate(task)
    idle(db, video_id)
    if task.status != TaskStatus.FAILED:
        raise HTTPException(
            409, "Only failed tasks can be retried; uncertain outcomes require review"
        )
    if task.checkpoint.get("recovery_abandoned"):
        raise HTTPException(409, "An abandoned task cannot be retried")
    from app.modules.revisions.service import check_revision_task

    try:
        check_revision_task(db, task)
    except ValueError:
        raise HTTPException(409, "Task belongs to an earlier or cancelled version") from None
    count = task.checkpoint.get("panel_retries", 0)
    if count >= 3 or task.attempts >= 10:
        raise HTTPException(409, "Manual retry limit reached")
    if task.parameters.get("quality_check_id") or task.kind in (TaskKind.QUALITY, TaskKind.AUDIO):
        raise HTTPException(409, "This operation requires explicit recovery or scene regeneration")
    if video.status == VideoStatus.FAILED:
        target = {
            TaskKind.STORY: VideoStatus.IDEA_GENERATED,
            TaskKind.RESEARCH: VideoStatus.IDEA_GENERATED,
            TaskKind.TOP5: VideoStatus.RESEARCHED,
        }.get(task.kind)
        event = db.exec(
            select(VideoStatusEvent)
            .where(VideoStatusEvent.video_id == video.id)
            .order_by(VideoStatusEvent.sequence.desc())
        ).first()
        expected = (
            VideoStatus.RESEARCHING if task.kind == TaskKind.RESEARCH else VideoStatus.SCRIPTING
        )
        if (
            target is None
            or event is None
            or event.to_status != VideoStatus.FAILED
            or event.from_status != expected
        ):
            raise HTTPException(409, "Video failure requires review")
        if db.exec(select(VideoScript.id).where(VideoScript.video_id == video.id)).first():
            raise HTTPException(409, "Existing script requires review")
        db.add(
            VideoStatusEvent(
                video_id=video.id,
                sequence=event.sequence + 1,
                from_status=VideoStatus.FAILED,
                to_status=target,
                reason=f"Explicit retry of task {task.id}",
            )
        )
        video.status, video.updated_at = target, datetime.now(UTC)
        db.add(video)
    else:
        allowed = {
            TaskKind.STORY: {VideoStatus.IDEA_GENERATED},
            TaskKind.RESEARCH: {VideoStatus.IDEA_GENERATED},
            TaskKind.TOP5: {VideoStatus.RESEARCHED},
            TaskKind.DIRECT: {VideoStatus.SCRIPT_READY},
            TaskKind.IMAGE: {VideoStatus.SCRIPT_READY},
            TaskKind.VIDEO: {VideoStatus.SCRIPT_READY},
            TaskKind.RENDER: {VideoStatus.SCRIPT_READY, VideoStatus.READY_TO_RENDER},
        }
        if video.status not in allowed.get(task.kind, set()):
            raise HTTPException(409, "Task no longer matches the video stage")
    if task.kind in (TaskKind.IMAGE, TaskKind.VIDEO) and task.checkpoint.get("submit_started"):
        raise HTTPException(409, "Submitted visual generation requires review")
    now = datetime.now(UTC)
    task.checkpoint = task.checkpoint | {"panel_retries": count + 1}
    task.status, task.error, task.completed_at = TaskStatus.QUEUED, None, None
    task.error_category = None
    task.available_at = task.delivery_after = now
    task.run_token = None
    task.max_attempts = min(10, max(task.max_attempts, task.attempts + 1))
    db.add(task)
    db.commit()
    return TaskRead.model_validate(task)
