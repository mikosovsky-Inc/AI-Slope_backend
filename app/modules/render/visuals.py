"""Materialize provider output in private storage; never fetch arbitrary provider URLs."""

from pathlib import Path
from tempfile import TemporaryDirectory

from sqlmodel import select

from app.modules.assets.models import Asset, AssetType, StorageBackend
from app.modules.render.engine import FFmpegRenderer, RenderError
from app.modules.render.service import bucket
from app.modules.tasks.models import TaskKind


def visual_kind(task) -> TaskKind:
    return TaskKind(task.checkpoint.get("budget_visual_kind", task.kind.value))


def output_key(task) -> str:
    extension = "png" if visual_kind(task) == TaskKind.IMAGE else "mp4"
    return f"generated/{task.id}/visual.{extension}"


def materialize_visual(db, task, settings, storage, output: dict, duration: float) -> Asset:
    key = output_key(task)
    existing = db.exec(
        select(Asset).where(
            Asset.video_id == task.video_id,
            Asset.object_key == key,
            Asset.storage_backend == settings.storage_backend,
            Asset.bucket == bucket(settings),
        )
    ).first()
    if existing:
        return existing
    is_image = visual_kind(task) == TaskKind.IMAGE
    mime = "image/png" if is_image else "video/mp4"
    renderer = FFmpegRenderer(settings)
    with TemporaryDirectory(prefix="ai-slop-visual-") as directory:
        path = Path(directory) / ("visual.png" if is_image else "visual.mp4")
        if settings.external_providers_mode == "mock":
            args = ["-f", "lavfi", "-i", "color=c=0x6040a0:s=360x640:r=30"]
            args += (
                ["-frames:v", "1"]
                if is_image
                else ["-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p"]
            )
            renderer.run([*args, str(path)])
            with path.open("rb") as source:
                stored = storage.put(key, source, content_type=mime)
        else:
            # Runpod worker must upload into our bucket using its own scoped credentials.
            if output.get("object_key") != key or output.get("content_type") != mime:
                raise RenderError("Provider must return the assigned storage object key and MIME")
            with path.open("wb") as target:
                stored = storage.download(key, target)
        renderer.probe(path, mime)
        asset = Asset(
            video_id=task.video_id,
            scene_id=task.scene_id,
            type=AssetType.IMAGE if is_image else AssetType.VIDEO,
            storage_backend=StorageBackend(settings.storage_backend),
            bucket=bucket(settings),
            object_key=key,
            content_type=mime,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            metadata_json={
                "task_id": str(task.id),
                "mock": settings.external_providers_mode == "mock",
            },
        )
        db.add(asset)
        db.commit()
        return asset
