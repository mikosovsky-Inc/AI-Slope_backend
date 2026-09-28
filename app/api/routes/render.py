from tempfile import SpooledTemporaryFile
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlmodel import select

from app.api.dependencies import CurrentUser, DbSession
from app.api.task_submission import TaskKey, accepted
from app.core.config import Settings, get_settings
from app.integrations.storage.factory import create_storage_provider
from app.modules.assets.models import Asset, AssetType
from app.modules.render.service import bucket
from app.modules.scripts.service import owned_video
from app.modules.tasks.models import TaskKind
from app.modules.tasks.schemas import TaskRead
from app.modules.tasks.service import enqueue

router = APIRouter(tags=["render"])


class RenderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    music_asset_id: UUID | None = None


class AssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    scene_id: UUID | None
    type: AssetType
    content_type: str
    size_bytes: int


@router.post("/videos/{video_id}/render", response_model=TaskRead, status_code=202)
def render(
    video_id: UUID, data: RenderCreate, user: CurrentUser, db: DbSession, task_key: TaskKey = None
) -> JSONResponse:
    owned_video(db, user.id, video_id)
    if data.music_asset_id:
        asset = db.get(Asset, data.music_asset_id)
        if asset is None or asset.video_id != video_id or asset.type != AssetType.AUDIO:
            raise HTTPException(404, "Music asset not found")
    return accepted(
        enqueue(
            db,
            user.id,
            TaskKind.RENDER,
            video_id=video_id,
            parameters=data.model_dump(mode="json"),
            key=task_key,
        )
    )


@router.get("/videos/{video_id}/assets", response_model=list[AssetRead])
def assets(video_id: UUID, user: CurrentUser, db: DbSession):
    owned_video(db, user.id, video_id)
    return db.exec(select(Asset).where(Asset.video_id == video_id).order_by(Asset.created_at)).all()


@router.get("/assets/{asset_id}/download")
def download(
    asset_id: UUID, user: CurrentUser, db: DbSession, settings: Settings = Depends(get_settings)
):
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(404, "Asset not found")
    owned_video(db, user.id, asset.video_id)
    if asset.storage_backend != settings.storage_backend or asset.bucket != bucket(settings):
        raise HTTPException(409, "Asset storage configuration changed")
    storage = create_storage_provider(settings)
    stream = SpooledTemporaryFile(max_size=1024 * 1024)
    try:
        stored = storage.download(asset.object_key, stream)
        if stored.sha256 != asset.sha256 or stored.size_bytes != asset.size_bytes:
            raise HTTPException(409, "Asset integrity mismatch")
        stream.seek(0)
    except Exception:
        stream.close()
        raise
    finally:
        storage.close()

    def chunks():
        try:
            while chunk := stream.read(64 * 1024):
                yield chunk
        finally:
            stream.close()

    return StreamingResponse(
        chunks(),
        media_type=asset.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{asset.object_key.rsplit("/", 1)[-1]}"',
            "Content-Length": str(asset.size_bytes),
            "X-Content-Type-Options": "nosniff",
        },
    )
