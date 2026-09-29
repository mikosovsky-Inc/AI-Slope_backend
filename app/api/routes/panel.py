from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.api.dependencies import CurrentUser, DbSession
from app.api.task_submission import TaskKey, accepted
from app.modules.costs.schemas import VideoBudget
from app.modules.costs.service import read_budget
from app.modules.panel import service
from app.modules.panel.schemas import (
    CostPage,
    Dashboard,
    Overview,
    Regenerate,
    Retry,
    ScenePatch,
    SceneRead,
    StatusRead,
    VideoPage,
)
from app.modules.scripts.service import owned_video
from app.modules.tasks.schemas import TaskRead
from app.modules.videos.models import VideoStatus
from app.modules.videos.schemas import VideoRead

router = APIRouter(tags=["panel"])
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]


@router.get("/dashboard", response_model=Dashboard)
def dashboard(user: CurrentUser, db: DbSession) -> Dashboard:
    return service.dashboard(db, user.id)


@router.get("/channels/{channel_id}/overview", response_model=Overview)
def overview(channel_id: UUID, user: CurrentUser, db: DbSession) -> Overview:
    return service.overview(db, user.id, channel_id)


@router.get("/channels/{channel_id}/videos", response_model=VideoPage)
def videos(
    channel_id: UUID,
    user: CurrentUser,
    db: DbSession,
    limit: Limit = 20,
    offset: Offset = 0,
    status: VideoStatus | None = None,
) -> VideoPage:
    return service.list_videos(db, user.id, channel_id, limit, offset, status)


@router.get("/channels/{channel_id}/costs", response_model=CostPage)
def costs(
    channel_id: UUID,
    user: CurrentUser,
    db: DbSession,
    limit: Limit = 20,
    offset: Offset = 0,
) -> CostPage:
    return service.costs(db, user.id, channel_id, limit, offset)


@router.get("/videos/{video_id}", response_model=VideoRead)
def video(video_id: UUID, user: CurrentUser, db: DbSession) -> VideoRead:
    return VideoRead.model_validate(owned_video(db, user.id, video_id))


@router.get("/videos/{video_id}/scenes", response_model=list[SceneRead])
def scenes(video_id: UUID, user: CurrentUser, db: DbSession) -> list[SceneRead]:
    return service.scenes(db, user.id, video_id)


@router.get("/videos/{video_id}/status", response_model=StatusRead)
def status(video_id: UUID, user: CurrentUser, db: DbSession) -> StatusRead:
    return service.status(db, user.id, video_id)


@router.post("/videos/{video_id}/retry", response_model=TaskRead, status_code=202)
def retry(video_id: UUID, data: Retry, user: CurrentUser, db: DbSession) -> JSONResponse:
    return accepted(service.retry(db, user.id, video_id, data.task_id))


@router.patch("/scenes/{scene_id}", response_model=SceneRead)
def edit_scene(scene_id: UUID, data: ScenePatch, user: CurrentUser, db: DbSession) -> SceneRead:
    return service.edit_scene(db, user.id, scene_id, data)


@router.post("/scenes/{scene_id}/regenerate", response_model=TaskRead, status_code=202)
def regenerate(
    scene_id: UUID,
    data: Regenerate,
    user: CurrentUser,
    db: DbSession,
    task_key: TaskKey = None,
) -> JSONResponse:
    return accepted(service.regenerate(db, user.id, scene_id, data.kind, task_key))


@router.get("/videos/{video_id}/budget", response_model=VideoBudget)
def budget(video_id: UUID, user: CurrentUser, db: DbSession) -> VideoBudget:
    return read_budget(db, user.id, video_id)
