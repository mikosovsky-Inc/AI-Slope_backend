from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import CurrentUser, DbSession
from app.models.roles import UserRole
from app.modules.observability.schemas import AdminJobPage
from app.modules.observability.service import list_jobs
from app.modules.tasks.models import TaskKind, TaskStatus


def require_admin(user: CurrentUser) -> None:
    if user.role != UserRole.ADMIN:
        raise HTTPException(403, "Administrator role required")


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/jobs", response_model=AdminJobPage)
def jobs(
    db: DbSession,
    status: TaskStatus | None = None,
    kind: TaskKind | None = None,
    video_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AdminJobPage:
    return list_jobs(db, status=status, kind=kind, video_id=video_id, limit=limit, offset=offset)
