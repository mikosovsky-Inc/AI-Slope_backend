from uuid import UUID

from fastapi import APIRouter

from app.api.dependencies import AppSettings, CurrentUser, DbSession
from app.api.task_submission import TaskKey, accepted
from app.modules.director.dependencies import CurrentDirector
from app.modules.director.schemas import DirectorRead
from app.modules.tasks.models import TaskKind
from app.modules.tasks.schemas import TaskRead
from app.modules.tasks.service import enqueue

router = APIRouter(prefix="/videos", tags=["director"])


@router.post(
    "/{video_id}/direct", response_model=DirectorRead, responses={202: {"model": TaskRead}}
)
def direct(
    video_id: UUID,
    user: CurrentUser,
    db: DbSession,
    director: CurrentDirector,
    settings: AppSettings,
    task_key: TaskKey = None,
) -> DirectorRead:
    if not settings.tasks_eager:
        return accepted(enqueue(db, user.id, TaskKind.DIRECT, video_id=video_id, key=task_key))
    return director.direct(db, user.id, video_id)


@router.get("/{video_id}/direction", response_model=DirectorRead)
def get(
    video_id: UUID, user: CurrentUser, db: DbSession, director: CurrentDirector
) -> DirectorRead:
    return director.get(db, user.id, video_id)
