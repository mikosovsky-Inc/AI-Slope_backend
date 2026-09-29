from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlmodel import Session, select

from app.core.observability import context
from app.models.user import User
from app.modules.channels.service import owned_channel
from app.modules.scripts.service import owned_video
from app.modules.tasks.models import Task, TaskKind
from app.modules.tasks.schemas import TaskRead
from app.modules.videos.models import Scene, VideoScript


def enqueue(
    db: Session,
    owner_id: UUID,
    kind: TaskKind,
    *,
    channel_id: UUID | None = None,
    video_id: UUID | None = None,
    scene_id: UUID | None = None,
    parameters: dict | None = None,
    key: str | None = None,
    commit: bool = True,
) -> TaskRead:
    """The task row is the durable outbox. No Redis operation in the API transaction."""
    try:
        # Serialize idempotency keys per owner; scope checked before returning any existing result.
        db.exec(select(User).where(User.id == owner_id).with_for_update()).one()
        if channel_id:
            owned_channel(db, owner_id, channel_id)
        if video_id:
            owned_video(db, owner_id, video_id)
        if (
            scene_id
            and not db.exec(
                select(Scene)
                .join(VideoScript)
                .where(
                    Scene.id == scene_id,
                    VideoScript.video_id == video_id,
                )
            ).first()
        ):
            raise HTTPException(404, "Scene not found")
        idempotency_key = key or str(uuid4())
        existing = db.exec(
            select(Task).where(Task.owner_id == owner_id, Task.idempotency_key == idempotency_key)
        ).one_or_none()
        if existing:
            if (
                existing.kind,
                existing.channel_id,
                existing.video_id,
                existing.scene_id,
                existing.parameters,
            ) != (
                kind,
                channel_id,
                video_id,
                scene_id,
                parameters or {},
            ):
                raise HTTPException(409, "Idempotency key already used for another request")
            result = TaskRead.model_validate(existing)
        else:
            task = Task(
                owner_id=owner_id,
                kind=kind,
                channel_id=channel_id,
                video_id=video_id,
                scene_id=scene_id,
                parameters=parameters or {},
                idempotency_key=idempotency_key,
                request_id=context.get().get("request_id"),
            )
            db.add(task)
            db.flush()
            result = TaskRead.model_validate(task)
        if commit:
            db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def read_task(db: Session, owner_id: UUID, task_id: UUID) -> TaskRead:
    task = db.exec(select(Task).where(Task.id == task_id, Task.owner_id == owner_id)).one_or_none()
    if task is None:
        raise HTTPException(404, "Task not found")
    return TaskRead.model_validate(task)
