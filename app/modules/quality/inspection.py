import math
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from pydantic import ValidationError
from sqlmodel import Session, select

from app.core.config import Settings
from app.modules.assets.models import Asset, AssetType
from app.modules.quality.provider import VisualQualityProvider, VisualQualityRequest
from app.modules.quality.schemas import CheckOutcome, CheckResult, QualityPolicy, QualityReport
from app.modules.render.engine import (
    FFmpegRenderer,
    InvalidMedia,
    MediaUnavailable,
    RenderError,
    media_input,
)
from app.modules.render.service import AssetIntegrityError, RenderManifest, download_asset
from app.modules.tasks.models import Task
from app.modules.videos.models import Scene, Video, VideoScript
from app.shared.storage import StorageObjectMissing, StorageProvider


class QualityInspector:
    def __init__(
        self, settings: Settings, storage: StorageProvider, visual: VisualQualityProvider
    ) -> None:
        self.settings, self.storage, self.visual = settings, storage, visual
        self.media = FFmpegRenderer(settings)
        self.total_bytes = 0

    def inspect(
        self, db: Session, video: Video, render: Task, final: Asset | None, policy: QualityPolicy
    ) -> QualityReport:
        self.media.deadline = time.monotonic() + self.settings.quality_timeout_seconds
        self.total_bytes = 0
        report = QualityReport(
            target_seconds=video.duration_target, visual_provider=self.visual.name
        )
        try:
            manifest = RenderManifest.model_validate(render.checkpoint.get("render_manifest"))
        except ValidationError:
            report.checks.append(CheckResult(code="render_manifest", outcome=CheckOutcome.FAILED))
            return report
        scenes = list(
            db.exec(
                select(Scene)
                .join(VideoScript)
                .where(VideoScript.video_id == video.id)
                .order_by(Scene.position)
            ).all()
        )
        ids = [s.scene_id for s in manifest.scenes]
        complete = ids == [s.id for s in scenes] and bool(scenes) and len(ids) == len(set(ids))
        report.checks.append(
            CheckResult(
                code="required_scenes",
                outcome=(CheckOutcome.PASSED if complete else CheckOutcome.FAILED),
            )
        )
        # Copies of metadata stay available; release the read transaction during media inspection.
        scene_map = {s.id: s for s in scenes}
        assets = {a.id: a for a in db.exec(select(Asset).where(Asset.video_id == video.id)).all()}
        db.commit()
        with TemporaryDirectory(prefix="ai-slop-quality-") as directory:
            work = Path(directory)
            final_path = work / "final.mp4"
            final_ok = self.asset(report, final, video.id, final_path, AssetType.FINAL_VIDEO)
            if final_ok:
                try:
                    probe = self.media.probe(final_path, "video/mp4")
                    visual = next((s for s in probe["streams"] if s["codec_type"] == "video"), {})
                    audio = next((s for s in probe["streams"] if s["codec_type"] == "audio"), {})
                    duration = float(probe["format"].get("duration", 0))
                    valid_duration = math.isfinite(duration) and duration > 0
                    report.duration_seconds = duration if valid_duration else None
                    conditions = {
                        "resolution": (visual.get("width"), visual.get("height")) == (1080, 1920),
                        "audio_stream": bool(audio) and float(audio.get("duration", 0)) > 0,
                        "duration_target": valid_duration
                        and abs(duration - video.duration_target)
                        <= policy.duration_tolerance_seconds,
                        "scene_timeline": valid_duration
                        and abs(
                            duration - sum(round(s.duration * 30) / 30 for s in manifest.scenes)
                        )
                        <= 0.25,
                        "output_codecs": visual.get("codec_name") == "h264"
                        and audio.get("codec_name") == "aac",
                        "frame_rate": visual.get("r_frame_rate") == "30/1",
                    }
                    for code, passed in conditions.items():
                        report.checks.append(
                            CheckResult(
                                code=code,
                                outcome=(CheckOutcome.PASSED if passed else CheckOutcome.FAILED),
                            )
                        )
                    self.media.run(
                        [
                            "-xerror",
                            "-err_detect",
                            "explode",
                            *media_input(final_path, "video/mp4"),
                            "-map",
                            "0:v:0",
                            "-map",
                            "0:a:0?",
                            "-f",
                            "null",
                            "-",
                        ]
                    )
                    report.checks.append(
                        CheckResult(code="final_decode", outcome=CheckOutcome.PASSED)
                    )
                except MediaUnavailable:
                    raise
                except (RenderError, KeyError, ValueError):
                    final_ok = False
                    report.checks.append(
                        CheckResult(code="final_decode", outcome=CheckOutcome.FAILED)
                    )
            offset = 0.0
            for index, item in enumerate(manifest.scenes):
                scene = scene_map.get(item.scene_id)
                if scene is None:
                    continue
                for asset_id, expected in (
                    (item.visual_asset_id, None),
                    (item.audio_asset_id, AssetType.AUDIO),
                ):
                    asset = assets.get(asset_id)
                    kind = expected or (
                        AssetType.VIDEO if scene.visual_type.value == "video" else AssetType.IMAGE
                    )
                    path = work / f"{index}-{kind.value}"
                    self.asset(report, asset, video.id, path, kind, scene.id, item.duration)
                if final_ok and complete:
                    frames = []
                    if self.visual.requires_frames:
                        frame = work / f"frame{index}.png"
                        self.media.run(
                            [
                                "-ss",
                                str(offset + item.duration / 2),
                                *media_input(final_path, "video/mp4"),
                                "-frames:v",
                                "1",
                                "-vf",
                                "scale=360:-2",
                                str(frame),
                            ]
                        )
                        if not frame.is_file() or frame.stat().st_size == 0:
                            raise RenderError("Quality frame extraction failed")
                        frames.append(frame)
                    result = self.visual.check(
                        VisualQualityRequest(
                            scene_id=scene.id, frames=frames, visual_prompt=scene.visual_prompt
                        )
                    )
                    report.checks.append(
                        CheckResult(
                            code=result.code,
                            outcome=result.outcome,
                            scene_id=scene.id,
                            asset_id=item.visual_asset_id,
                            repair=scene.visual_type.value
                            if result.outcome == CheckOutcome.FAILED
                            else None,
                        )
                    )
                offset += round(item.duration * 30) / 30
            if manifest.music_asset_id:
                self.asset(
                    report,
                    assets.get(manifest.music_asset_id),
                    video.id,
                    work / "music",
                    AssetType.AUDIO,
                )
        return report

    def asset(
        self,
        report: QualityReport,
        asset: Asset | None,
        video_id: UUID,
        path: Path,
        kind: AssetType,
        scene_id: UUID | None = None,
        duration: float | None = None,
    ) -> bool:
        code = "asset_integrity"
        repair = kind.value if scene_id is not None else None
        try:
            if (
                asset is None
                or asset.video_id != video_id
                or asset.type != kind
                or (scene_id is not None and asset.scene_id != scene_id)
            ):
                raise ValueError("Missing or mismatched asset")
            if asset.size_bytes <= 0:
                raise ValueError("Empty asset")
            self.total_bytes += asset.size_bytes
            if self.total_bytes > self.settings.render_max_input_bytes:
                # Resource/configuration problems are never grounds for paid regeneration.
                raise RenderError("Quality input limit exceeded")
            download_asset(self.storage, self.settings, asset, path)
            if kind != AssetType.FINAL_VIDEO:
                expected = {
                    AssetType.IMAGE: ("image/png", "image/jpeg"),
                    AssetType.VIDEO: ("video/mp4",),
                    AssetType.AUDIO: ("audio/wav", "audio/mpeg"),
                }
                if asset.content_type not in expected[kind]:
                    raise ValueError("Invalid asset format")
                probe = self.media.probe(path, asset.content_type)
                codec = "audio" if kind == AssetType.AUDIO else "video"
                if not any(s.get("codec_type") == codec for s in probe["streams"]):
                    raise ValueError("Missing stream")
                if codec == "video" and not any(
                    s.get("width", 0) > 0 and s.get("height", 0) > 0
                    for s in probe["streams"]
                    if s.get("codec_type") == "video"
                ):
                    raise ValueError("Empty visual dimensions")
                if kind in (AssetType.AUDIO, AssetType.VIDEO):
                    seconds = float(probe["format"].get("duration", 0))
                    if not math.isfinite(seconds) or seconds <= 0:
                        raise ValueError("Zero duration asset")
                    if kind == AssetType.AUDIO and duration and seconds > duration + 0.15:
                        # A script/timing mismatch cannot safely be fixed by regenerating paid TTS.
                        repair = None
                        raise ValueError("Narration exceeds scene")
                decode_args = (
                    ["-frames:v", "1"]
                    if kind == AssetType.IMAGE
                    else ["-t", str(min(duration or 180, 180))]
                )
                try:
                    self.media.run(
                        [
                            "-xerror",
                            "-err_detect",
                            "explode",
                            *media_input(path, asset.content_type),
                            "-map",
                            "0:a:0" if kind == AssetType.AUDIO else "0:v:0",
                            *decode_args,
                            "-f",
                            "null",
                            "-",
                        ]
                    )
                except MediaUnavailable:
                    raise
                except RenderError:
                    raise InvalidMedia("Asset cannot be decoded") from None
            report.checks.append(
                CheckResult(
                    code=code, outcome=CheckOutcome.PASSED, scene_id=scene_id, asset_id=asset.id
                )
            )
            return True
        except (StorageObjectMissing, ValueError, AssetIntegrityError, InvalidMedia):
            pass
        report.checks.append(
            CheckResult(
                code=code,
                outcome=CheckOutcome.FAILED,
                scene_id=scene_id,
                asset_id=asset.id if asset else None,
                repair=repair,
            )
        )
        return False
