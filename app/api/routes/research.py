from uuid import UUID

from fastapi import APIRouter

from app.api.dependencies import CurrentUser, DbSession
from app.integrations.llm.factory import CurrentLLM
from app.modules.research.provider import CurrentResearch
from app.modules.research.schemas import ResearchRead, Top5ScriptRead
from app.modules.research.service import generate_top5, read_research, run_research

router = APIRouter(prefix="/videos", tags=["research"])


@router.post("/{video_id}/research", response_model=ResearchRead)
def research(
    video_id: UUID, user: CurrentUser, db: DbSession, llm: CurrentLLM, provider: CurrentResearch
) -> ResearchRead:
    return run_research(db, user.id, video_id, llm, provider)


@router.get("/{video_id}/research", response_model=ResearchRead)
def get(video_id: UUID, user: CurrentUser, db: DbSession) -> ResearchRead:
    return read_research(db, user.id, video_id)


@router.post("/{video_id}/top5-script/generate", response_model=Top5ScriptRead)
def script(video_id: UUID, user: CurrentUser, db: DbSession, llm: CurrentLLM) -> Top5ScriptRead:
    return generate_top5(db, user.id, video_id, llm)
