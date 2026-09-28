from uuid import UUID

from fastapi import APIRouter

from app.api.dependencies import AppSettings, CurrentUser, DbSession
from app.api.task_submission import TaskKey, accepted
from app.integrations.llm.factory import CurrentLLM
from app.modules.research.schemas import Top5ScriptRead
from app.modules.scripts.schemas import ScriptRead
from app.modules.scripts.service import generate_script, get_script
from app.modules.tasks.models import TaskKind
from app.modules.tasks.schemas import TaskRead
from app.modules.tasks.service import enqueue

router = APIRouter(prefix="/videos", tags=["scripts"])


@router.post(
    "/{video_id}/script/generate", response_model=ScriptRead, responses={202: {"model": TaskRead}}
)
def generate(
    video_id: UUID,
    user: CurrentUser,
    db: DbSession,
    llm: CurrentLLM,
    settings: AppSettings,
    task_key: TaskKey = None,
) -> ScriptRead:
    if not settings.tasks_eager:
        return accepted(enqueue(db, user.id, TaskKind.STORY, video_id=video_id, key=task_key))
    return generate_script(db, user.id, video_id, llm)


@router.get("/{video_id}/script", response_model=ScriptRead | Top5ScriptRead)
def get(video_id: UUID, user: CurrentUser, db: DbSession) -> ScriptRead | Top5ScriptRead:
    return get_script(db, user.id, video_id)
