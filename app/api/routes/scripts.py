from uuid import UUID

from fastapi import APIRouter

from app.api.dependencies import CurrentUser, DbSession
from app.integrations.llm.factory import CurrentLLM
from app.modules.research.schemas import Top5ScriptRead
from app.modules.scripts.schemas import ScriptRead
from app.modules.scripts.service import generate_script, get_script

router = APIRouter(prefix="/videos", tags=["scripts"])


@router.post("/{video_id}/script/generate", response_model=ScriptRead)
def generate(video_id: UUID, user: CurrentUser, db: DbSession, llm: CurrentLLM) -> ScriptRead:
    return generate_script(db, user.id, video_id, llm)


@router.get("/{video_id}/script", response_model=ScriptRead | Top5ScriptRead)
def get(video_id: UUID, user: CurrentUser, db: DbSession) -> ScriptRead | Top5ScriptRead:
    return get_script(db, user.id, video_id)
