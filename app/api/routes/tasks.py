from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import CurrentUser, DbSession
from app.api.task_submission import TaskKey, accepted
from app.modules.tasks.models import TaskKind
from app.modules.tasks.schemas import TaskRead
from app.modules.tasks.service import enqueue, read_task

router = APIRouter(tags=["tasks"])


class SceneTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_id: UUID


@router.get("/tasks/{task_id}", response_model=TaskRead)
def get_task(task_id: UUID, user: CurrentUser, db: DbSession) -> TaskRead:
    return read_task(db, user.id, task_id)


@router.post("/videos/{video_id}/audio", response_model=TaskRead, status_code=202)
def audio(
    video_id: UUID,
    data: SceneTaskCreate,
    user: CurrentUser,
    db: DbSession,
    task_key: TaskKey = None,
) -> JSONResponse:
    return accepted(
        enqueue(
            db, user.id, TaskKind.AUDIO, video_id=video_id, scene_id=data.scene_id, key=task_key
        )
    )


@router.post("/videos/{video_id}/visuals/{kind}", response_model=TaskRead, status_code=202)
def visual(
    video_id: UUID,
    kind: str,
    data: SceneTaskCreate,
    user: CurrentUser,
    db: DbSession,
    task_key: TaskKey = None,
) -> JSONResponse:
    from fastapi import HTTPException

    if kind not in ("image", "video"):
        raise HTTPException(422, "Use image or video")
    return accepted(
        enqueue(
            db, user.id, TaskKind(kind), video_id=video_id, scene_id=data.scene_id, key=task_key
        )
    )


@router.get("/videos/{video_id}/tasks", response_model=list[TaskRead])
def video_tasks(video_id: UUID, user: CurrentUser, db: DbSession) -> list[TaskRead]:
    from sqlmodel import select

    from app.modules.scripts.service import owned_video
    from app.modules.tasks.models import Task

    owned_video(db, user.id, video_id)
    tasks = db.exec(
        select(Task)
        .where(Task.video_id == video_id, Task.owner_id == user.id)
        .order_by(Task.created_at.desc())
        .limit(100)
    ).all()
    return [TaskRead.model_validate(task) for task in tasks]
