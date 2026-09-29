from uuid import UUID

from fastapi import APIRouter

from app.api.dependencies import AppSettings, CurrentUser, DbSession
from app.api.task_submission import TaskKey, accepted
from app.integrations.llm.factory import CurrentLLM
from app.modules.costs.llm import BudgetedLLM
from app.modules.research.provider import CurrentResearch
from app.modules.research.schemas import ResearchRead, Top5ScriptRead
from app.modules.research.service import generate_top5, read_research, run_research
from app.modules.tasks.models import TaskKind
from app.modules.tasks.schemas import TaskRead
from app.modules.tasks.service import enqueue

router = APIRouter(prefix="/videos", tags=["research"])


@router.post(
    "/{video_id}/research", response_model=ResearchRead, responses={202: {"model": TaskRead}}
)
def research(
    video_id: UUID,
    user: CurrentUser,
    db: DbSession,
    llm: CurrentLLM,
    provider: CurrentResearch,
    settings: AppSettings,
    task_key: TaskKey = None,
) -> ResearchRead:
    if not settings.tasks_eager:
        return accepted(enqueue(db, user.id, TaskKind.RESEARCH, video_id=video_id, key=task_key))
    return run_research(db, user.id, video_id, BudgetedLLM(llm, db, video_id, settings), provider)


@router.get("/{video_id}/research", response_model=ResearchRead)
def get(video_id: UUID, user: CurrentUser, db: DbSession) -> ResearchRead:
    return read_research(db, user.id, video_id)


@router.post(
    "/{video_id}/top5-script/generate",
    response_model=Top5ScriptRead,
    responses={202: {"model": TaskRead}},
)
def script(
    video_id: UUID,
    user: CurrentUser,
    db: DbSession,
    llm: CurrentLLM,
    settings: AppSettings,
    task_key: TaskKey = None,
) -> Top5ScriptRead:
    if not settings.tasks_eager:
        return accepted(enqueue(db, user.id, TaskKind.TOP5, video_id=video_id, key=task_key))
    return generate_top5(db, user.id, video_id, BudgetedLLM(llm, db, video_id, settings))
