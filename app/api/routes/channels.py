from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.dependencies import AppSettings, CurrentUser, DbSession
from app.api.task_submission import TaskKey, accepted
from app.integrations.llm.factory import CurrentLLM
from app.modules.channels import service
from app.modules.channels.models import ChannelStatus
from app.modules.channels.schemas import ChannelCreate, ChannelDetail, ChannelPage, ChannelUpdate
from app.modules.competitors.dependencies import CurrentResearch
from app.modules.competitors.schemas import CompetitorPage, ResearchSummary
from app.modules.competitors.service import list_competitors, research_competitors
from app.modules.intelligence.service import analyze_channel
from app.modules.tasks.models import TaskKind
from app.modules.tasks.schemas import TaskRead
from app.modules.tasks.service import enqueue

router = APIRouter(prefix="/channels", tags=["channels"])


@router.post("", response_model=ChannelDetail, status_code=201)
def create(data: ChannelCreate, user: CurrentUser, db: DbSession) -> ChannelDetail:
    return service.create_channel(db, user.id, data)


@router.get("", response_model=ChannelPage)
def list_all(
    user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ChannelPage:
    return service.list_channels(db, user.id, limit, offset)


@router.get("/{channel_id}", response_model=ChannelDetail)
def get(channel_id: UUID, user: CurrentUser, db: DbSession) -> ChannelDetail:
    return service.detail(db, service.owned_channel(db, user.id, channel_id))


@router.patch("/{channel_id}", response_model=ChannelDetail)
def update(
    channel_id: UUID, data: ChannelUpdate, user: CurrentUser, db: DbSession
) -> ChannelDetail:
    return service.update_channel(db, user.id, channel_id, data)


@router.delete("/{channel_id}", status_code=204)
def delete(channel_id: UUID, user: CurrentUser, db: DbSession) -> Response:
    service.delete_channel(db, user.id, channel_id)
    return Response(status_code=204)


@router.post("/{channel_id}/activate", response_model=ChannelDetail)
def activate(channel_id: UUID, user: CurrentUser, db: DbSession) -> ChannelDetail:
    return service.change_status(db, user.id, channel_id, ChannelStatus.ACTIVE)


@router.post("/{channel_id}/pause", response_model=ChannelDetail)
def pause(channel_id: UUID, user: CurrentUser, db: DbSession) -> ChannelDetail:
    return service.change_status(db, user.id, channel_id, ChannelStatus.PAUSED)


@router.post(
    "/{channel_id}/analyze", response_model=ChannelDetail, responses={202: {"model": TaskRead}}
)
def analyze(
    channel_id: UUID,
    user: CurrentUser,
    db: DbSession,
    llm: CurrentLLM,
    settings: AppSettings,
    task_key: TaskKey = None,
) -> ChannelDetail:
    if not settings.tasks_eager:
        return accepted(enqueue(db, user.id, TaskKind.ANALYZE, channel_id=channel_id, key=task_key))
    return analyze_channel(db, user.id, channel_id, llm)


@router.post(
    "/{channel_id}/competitor-research",
    response_model=ResearchSummary,
    responses={202: {"model": TaskRead}},
)
def research(
    channel_id: UUID,
    user: CurrentUser,
    db: DbSession,
    provider: CurrentResearch,
    settings: AppSettings,
    task_key: TaskKey = None,
) -> ResearchSummary:
    if not settings.tasks_eager:
        return accepted(
            enqueue(db, user.id, TaskKind.COMPETITORS, channel_id=channel_id, key=task_key)
        )
    return research_competitors(db, user.id, channel_id, provider)


@router.get("/{channel_id}/competitors", response_model=CompetitorPage)
def competitors(
    channel_id: UUID,
    user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CompetitorPage:
    return list_competitors(db, user.id, channel_id, limit, offset)
