from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.dependencies import CurrentUser, DbSession
from app.api.task_submission import accepted
from app.core.config import Settings, get_settings
from app.modules.revisions import recovery, service
from app.modules.revisions.schemas import RecoveryInput, RecoveryRead, RevisionPatch, RevisionRead
from app.modules.tasks.schemas import TaskRead

router = APIRouter(tags=["revisions"])


@router.get("/videos/{video_id}/revisions", response_model=list[RevisionRead])
def list_revisions(video_id: UUID, user: CurrentUser, db: DbSession):
    return service.list_revisions(db, user.id, video_id)


@router.post("/videos/{video_id}/revisions", response_model=RevisionRead, status_code=201)
def create_revision(video_id: UUID, user: CurrentUser, db: DbSession):
    return service.create_revision(db, user.id, video_id)


@router.patch(
    "/videos/{video_id}/revisions/{revision_id}/scenes/{scene_id}", response_model=RevisionRead
)
def edit_revision(
    video_id: UUID,
    revision_id: UUID,
    scene_id: UUID,
    data: RevisionPatch,
    user: CurrentUser,
    db: DbSession,
):
    return service.edit_revision(db, user.id, video_id, revision_id, scene_id, data)


@router.post(
    "/videos/{video_id}/revisions/{revision_id}/produce",
    response_model=RevisionRead,
    status_code=202,
)
def produce_revision(video_id: UUID, revision_id: UUID, user: CurrentUser, db: DbSession):
    return service.submit_revision(db, user.id, video_id, revision_id)


@router.get("/tasks/{task_id}/recoveries", response_model=list[RecoveryRead])
def recoveries(task_id: UUID, user: CurrentUser, db: DbSession):
    return recovery.history(db, user.id, task_id)


@router.post("/videos/{video_id}/revisions/{revision_id}/cancel", response_model=RevisionRead)
def cancel_revision(video_id: UUID, revision_id: UUID, user: CurrentUser, db: DbSession):
    return service.cancel_revision(db, user.id, video_id, revision_id)


@router.post("/tasks/{task_id}/recover", response_model=TaskRead, status_code=202)
def recover(
    task_id: UUID,
    data: RecoveryInput,
    user: CurrentUser,
    db: DbSession,
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    return accepted(recovery.recover(db, user.id, task_id, data, settings))
