from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from app.core.config import Settings
from app.modules.assets.models import Asset, AssetType, StorageBackend
from app.modules.render.captions import write_captions
from app.modules.render.engine import FFmpegRenderer, RenderError, RenderScene
from app.modules.scripts.service import owned_video
from app.modules.tasks.models import Task
from app.modules.videos.models import Scene, VideoScript, VideoStatus
from app.modules.videos.service import transition_video
from app.shared.storage import StorageProvider


class AssetIntegrityError(RenderError):
    pass


class SceneInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    scene_id: UUID
    visual_asset_id: UUID
    audio_asset_id: UUID
    duration: float = Field(ge=1 / 30, le=180)
    narration: str
    motion: Literal["zoom_in", "zoom_out", "pan_left", "pan_right", "static"]


class RenderManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenes: list[SceneInput] = Field(min_length=1, max_length=100)
    music_asset_id: UUID | None = None


def bucket(settings: Settings) -> str:
    return settings.s3_bucket if settings.storage_backend == "s3" else ""


def asset_for(db: Session, video_id: UUID, asset_id: UUID) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None or asset.video_id != video_id:
        raise RenderError("Asset not found")
    return asset


def download_asset(storage: StorageProvider, settings: Settings, asset: Asset, path: Path) -> None:
    if asset.storage_backend != settings.storage_backend or asset.bucket != bucket(settings):
        raise RenderError("Asset storage does not match configuration")
    with path.open("wb") as target:
        stored = storage.download(asset.object_key, target)
    if stored.sha256 != asset.sha256 or stored.size_bytes != asset.size_bytes:
        raise AssetIntegrityError("Asset integrity mismatch")


def store_asset(db, storage, settings, task, path, kind, mime) -> Asset:
    key = f"videos/{task.video_id}/render/{task.id}/{path.name}"
    with path.open("rb") as source:
        stored = storage.put(key, source, content_type=mime)
    asset = Asset(
        video_id=task.video_id,
        type=kind,
        storage_backend=StorageBackend(settings.storage_backend),
        bucket=bucket(settings),
        object_key=key,
        content_type=mime,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        metadata_json={"task_id": str(task.id)},
    )
    db.add(asset)
    return asset


class RenderService:
    """Private files only. The task checkpoint is an immutable input manifest and render claim."""

    def __init__(self, settings: Settings, storage: StorageProvider):
        self.settings = settings
        self.storage = storage
        self.renderer = FFmpegRenderer(settings)

    def render(self, db: Session, task: Task) -> dict:
        video = owned_video(db, task.owner_id, task.video_id, lock=True)
        existing = db.exec(
            select(Asset).where(
                Asset.object_key == f"videos/{video.id}/render/{task.id}/final.mp4",
                Asset.video_id == video.id,
            )
        ).first()
        if existing:
            subtitle = db.exec(
                select(Asset).where(
                    Asset.object_key == f"videos/{video.id}/render/{task.id}/captions.ass",
                    Asset.video_id == video.id,
                )
            ).one()
            return {
                "asset_id": str(existing.id),
                "subtitle_asset_id": str(subtitle.id),
                "status": VideoStatus.QUALITY_CHECK.value,
            }
        raw = task.checkpoint.get("render_manifest")
        if raw:
            manifest = RenderManifest.model_validate(raw)
            if video.status != VideoStatus.RENDERING:
                raise RenderError("Video is no longer rendering")
        else:
            allowed = [
                VideoStatus.SCRIPT_READY,
                VideoStatus.GENERATING_ASSETS,
                VideoStatus.ASSETS_READY,
                VideoStatus.GENERATING_AUDIO,
                VideoStatus.READY_TO_RENDER,
            ]
            if video.status not in allowed:
                raise RenderError("Video is not ready for a new render")
            if task.parameters.get("input_manifest"):
                manifest = RenderManifest.model_validate(task.parameters["input_manifest"])
            else:
                scenes = db.exec(
                    select(Scene)
                    .join(VideoScript)
                    .where(VideoScript.video_id == video.id)
                    .order_by(Scene.position)
                ).all()
                inputs = []
                for scene in scenes:
                    assets = db.exec(
                        select(Asset)
                        .where(Asset.video_id == video.id, Asset.scene_id == scene.id)
                        .order_by(Asset.created_at.desc(), Asset.id.desc())
                    ).all()
                    if scene.visual_type.value not in ("image", "video"):
                        raise RenderError("Scene requires a supported visual type")
                    visual = next(
                        (a for a in assets if a.type.value == scene.visual_type.value), None
                    )
                    audio = next((a for a in assets if a.type == AssetType.AUDIO), None)
                    if visual is None or audio is None:
                        raise RenderError("Every scene requires visual and narration assets")
                    inputs.append(
                        SceneInput(
                            scene_id=scene.id,
                            visual_asset_id=visual.id,
                            audio_asset_id=audio.id,
                            duration=float(scene.duration),
                            narration=scene.narration,
                            motion=scene.camera_motion,
                        )
                    )
                manifest = RenderManifest(
                    scenes=inputs, music_asset_id=task.parameters.get("music_asset_id")
                )
            if sum(round(s.duration * 30) / 30 for s in manifest.scenes) > 180.1:
                raise RenderError("Render duration exceeds 180 seconds")
            if manifest.music_asset_id:
                music = asset_for(db, video.id, manifest.music_asset_id)
                if music.type != AssetType.AUDIO:
                    raise RenderError("Music must be an audio asset")
            # All required assets exist; persist readiness and claim in one transaction.
            for status in allowed[allowed.index(video.status) + 1 :] + [VideoStatus.RENDERING]:
                transition_video(db, video.id, status, reason="Render inputs collected")
            task.checkpoint = task.checkpoint | {
                "render_manifest": manifest.model_dump(mode="json")
            }
            db.add(task)
        db.commit()
        # No database locks or open transaction during FFmpeg work.
        try:
            with TemporaryDirectory(prefix="ai-slop-render-") as directory:
                work = Path(directory)
                scenes, captions, total = [], [], 0
                for index, item in enumerate(manifest.scenes):
                    visual = asset_for(db, task.video_id, item.visual_asset_id)
                    audio = asset_for(db, task.video_id, item.audio_asset_id)
                    if (
                        visual.scene_id != item.scene_id
                        or audio.scene_id != item.scene_id
                        or audio.type != AssetType.AUDIO
                        or visual.type not in (AssetType.IMAGE, AssetType.VIDEO)
                    ):
                        raise RenderError("Scene asset mismatch")
                    total += visual.size_bytes + audio.size_bytes
                    if total > self.settings.render_max_input_bytes:
                        raise RenderError("Render input limit exceeded")
                    visual_path, audio_path = work / f"visual{index}", work / f"audio{index}"
                    download_asset(self.storage, self.settings, visual, visual_path)
                    download_asset(self.storage, self.settings, audio, audio_path)
                    scenes.append(
                        RenderScene(
                            visual=visual_path,
                            visual_type=visual.content_type,
                            audio=audio_path,
                            audio_type=audio.content_type,
                            duration=item.duration,
                            motion=item.motion,
                            narration=item.narration,
                        )
                    )
                    captions.append(
                        (
                            item.narration,
                            round(item.duration * 30) / 30,
                            audio.metadata_json.get("normalized_alignment")
                            or audio.metadata_json.get("alignment"),
                        )
                    )
                music_input = None
                if manifest.music_asset_id:
                    music = asset_for(db, task.video_id, manifest.music_asset_id)
                    if music.content_type not in ("audio/wav", "audio/mpeg"):
                        raise RenderError("Unsupported music format")
                    if total + music.size_bytes > self.settings.render_max_input_bytes:
                        raise RenderError("Render input limit exceeded")
                    music_path = work / "music"
                    download_asset(self.storage, self.settings, music, music_path)
                    music_input = (music_path, music.content_type)
                db.commit()
                write_captions(work / "captions.ass", captions)
                output = self.renderer.render(scenes, work, music=music_input)
                owned_video(db, task.owner_id, task.video_id, lock=True)
                final = store_asset(
                    db,
                    self.storage,
                    self.settings,
                    task,
                    output,
                    AssetType.FINAL_VIDEO,
                    "video/mp4",
                )
                subtitle = store_asset(
                    db,
                    self.storage,
                    self.settings,
                    task,
                    work / "captions.ass",
                    AssetType.SUBTITLE,
                    "text/x-ssa",
                )
                transition_video(
                    db,
                    task.video_id,
                    VideoStatus.QUALITY_CHECK,
                    reason="FFmpeg render completed",
                    expected_status=VideoStatus.RENDERING,
                )
                db.commit()
                return {
                    "asset_id": str(final.id),
                    "subtitle_asset_id": str(subtitle.id),
                    "status": VideoStatus.QUALITY_CHECK.value,
                }
        except Exception:
            db.rollback()
            # Stable keys are overwritten on recovery; never delete a possibly committed result.
            raise
