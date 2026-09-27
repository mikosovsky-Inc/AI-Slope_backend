from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.dependencies import CurrentUser, DbSession
from app.integrations.llm.factory import CurrentLLM
from app.modules.ideas.models import IdeaStatus
from app.modules.ideas.schemas import IdeaBatch, IdeaPage, IdeaRead
from app.modules.ideas.service import decide_idea, generate_ideas, list_ideas

router = APIRouter(tags=["ideas"])


@router.post("/channels/{channel_id}/ideas/generate", response_model=IdeaBatch, status_code=201)
def generate(
    channel_id: UUID,
    user: CurrentUser,
    db: DbSession,
    llm: CurrentLLM,
    count: Annotated[int, Query(ge=10, le=20)] = 10,
) -> IdeaBatch:
    return generate_ideas(db, user.id, channel_id, count, llm)


@router.get("/channels/{channel_id}/ideas", response_model=IdeaPage)
def list_all(
    channel_id: UUID,
    user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: IdeaStatus | None = None,
) -> IdeaPage:
    return list_ideas(db, user.id, channel_id, limit, offset, status)


@router.post("/ideas/{idea_id}/approve", response_model=IdeaRead)
def approve(idea_id: UUID, user: CurrentUser, db: DbSession) -> IdeaRead:
    return decide_idea(db, user.id, idea_id, IdeaStatus.APPROVED)


@router.post("/ideas/{idea_id}/reject", response_model=IdeaRead)
def reject(idea_id: UUID, user: CurrentUser, db: DbSession) -> IdeaRead:
    return decide_idea(db, user.id, idea_id, IdeaStatus.REJECTED)
