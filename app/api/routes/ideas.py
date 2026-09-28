from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.dependencies import AppSettings, CurrentUser, DbSession
from app.api.task_submission import TaskKey, accepted
from app.integrations.llm.factory import CurrentLLM
from app.modules.ideas.models import IdeaStatus
from app.modules.ideas.schemas import IdeaBatch, IdeaPage, IdeaRead
from app.modules.ideas.service import decide_idea, generate_ideas, list_ideas
from app.modules.tasks.models import TaskKind
from app.modules.tasks.schemas import TaskRead
from app.modules.tasks.service import enqueue
from app.modules.videos.schemas import VideoRead
from app.modules.videos.service import create_video

router = APIRouter(tags=["ideas"])


@router.post(
    "/channels/{channel_id}/ideas/generate",
    response_model=IdeaBatch,
    status_code=201,
    responses={202: {"model": TaskRead}},
)
def generate(
    channel_id: UUID,
    user: CurrentUser,
    db: DbSession,
    llm: CurrentLLM,
    settings: AppSettings,
    count: Annotated[int, Query(ge=10, le=20)] = 10,
    task_key: TaskKey = None,
) -> IdeaBatch:
    if not settings.tasks_eager:
        return accepted(
            enqueue(
                db,
                user.id,
                TaskKind.IDEAS,
                channel_id=channel_id,
                key=task_key,
                parameters={"count": count},
            )
        )
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


@router.post(
    "/ideas/{idea_id}/create-video",
    response_model=VideoRead,
    status_code=201,
    responses={
        200: {"model": VideoRead, "description": "Existing video"},
        202: {"model": VideoRead, "description": "Video and workflow queued"},
    },
)
def create(
    idea_id: UUID, user: CurrentUser, db: DbSession, response: Response, settings: AppSettings
) -> VideoRead:
    result, created = create_video(db, user.id, idea_id, enqueue_workflow=not settings.tasks_eager)
    response.status_code = (201 if settings.tasks_eager else 202) if created else 200
    return result
