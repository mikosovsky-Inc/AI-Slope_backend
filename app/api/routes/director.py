from uuid import UUID

from fastapi import APIRouter

from app.api.dependencies import CurrentUser, DbSession
from app.modules.director.dependencies import CurrentDirector
from app.modules.director.schemas import DirectorRead

router = APIRouter(prefix="/videos", tags=["director"])


@router.post("/{video_id}/direct", response_model=DirectorRead)
def direct(
    video_id: UUID, user: CurrentUser, db: DbSession, director: CurrentDirector
) -> DirectorRead:
    return director.direct(db, user.id, video_id)


@router.get("/{video_id}/direction", response_model=DirectorRead)
def get(
    video_id: UUID, user: CurrentUser, db: DbSession, director: CurrentDirector
) -> DirectorRead:
    return director.get(db, user.id, video_id)
