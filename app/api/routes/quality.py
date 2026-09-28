from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.dependencies import CurrentUser, DbSession
from app.api.task_submission import accepted
from app.modules.quality.schemas import QualityCheckRead
from app.modules.quality.service import read_checks, submit_quality
from app.modules.tasks.schemas import TaskRead

router = APIRouter(tags=["quality"])


@router.post("/videos/{video_id}/quality-check", response_model=TaskRead, status_code=202)
def quality_check(video_id: UUID, user: CurrentUser, db: DbSession) -> JSONResponse:
    return accepted(submit_quality(db, user.id, video_id))


@router.get("/videos/{video_id}/quality-checks", response_model=list[QualityCheckRead])
def quality_checks(video_id: UUID, user: CurrentUser, db: DbSession) -> list[QualityCheckRead]:
    return read_checks(db, user.id, video_id)
