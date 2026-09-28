import hashlib
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from uuid import UUID

from sqlmodel import Session, select

from app.core.config import Settings
from app.modules.assets.models import Asset, AssetType, GenerationJob, GenerationStatus
from app.modules.audio.pricing import ConfiguredTTSPricing
from app.modules.costs.models import CostEvent
from app.modules.ideas.models import ContentIdea
from app.modules.scripts.service import ScriptNotFound, owned_video
from app.modules.videos.models import Scene, VideoScript
from app.shared.storage import StorageProvider
from app.shared.tts import TTSProvider, TTSRequest, VoiceSettings

logger = logging.getLogger("app.audio")


class AudioConflict(Exception):
    pass


def generate_scene_audio(
    db: Session,
    owner_id: UUID,
    video_id: UUID,
    scene_id: UUID,
    provider: TTSProvider,
    storage: StorageProvider,
    settings: Settings,
    *,
    voice_id: str | None = None,
    voice_settings: VoiceSettings | None = None,
) -> Asset:
    """Internal synchronous service. Owns transactions; future workers call this outside HTTP."""
    job_id = None
    key = None
    try:
        video = owned_video(db, owner_id, video_id, lock=True)
        scene = db.exec(
            select(Scene)
            .join(VideoScript)
            .where(
                Scene.id == scene_id,
                VideoScript.video_id == video_id,
            )
        ).one_or_none()
        if scene is None:
            raise ScriptNotFound
        request = TTSRequest(
            text=scene.narration,
            voice_id=voice_id
            or settings.elevenlabs_voice_id
            or ("mock-voice" if provider.name == "mock_elevenlabs" else ""),
            language=video.language,
            settings=voice_settings or VoiceSettings(),
        )
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "request": request.model_dump(),
                    "provider": provider.name,
                    "model": provider.model,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        existing = db.exec(
            select(GenerationJob).where(
                GenerationJob.scene_id == scene_id,
                GenerationJob.idempotency_key == "tts-" + fingerprint,
            )
        ).one_or_none()
        if existing is not None:
            asset = db.exec(
                select(Asset).where(Asset.generation_job_id == existing.id)
            ).one_or_none()
            if existing.status != GenerationStatus.SUCCEEDED or asset is None:
                raise AudioConflict("Audio job already exists; requires explicit recovery")
            db.commit()
            return asset
        rate = (
            Decimal(0)
            if provider.name == "mock_elevenlabs"
            else settings.tts_usd_per_1000_characters
        )
        if rate is None:
            raise AudioConflict("Configure TTS estimate before live generation")
        estimate = ConfiguredTTSPricing(rate).estimate(len(request.text))
        idea = db.get(ContentIdea, video.idea_id)
        job = GenerationJob(
            scene_id=scene_id,
            provider=provider.name,
            type=AssetType.AUDIO,
            status=GenerationStatus.RUNNING,
            started_at=datetime.now(UTC),
            idempotency_key="tts-" + fingerprint,
            parameters={"request": request.model_dump(), "model": provider.model},
        )
        db.add(job)
        db.flush()
        cost = CostEvent(
            video_id=video_id,
            channel_id=idea.channel_id,
            generation_job_id=job.id,
            provider=provider.name,
            operation="text_to_speech",
            model=provider.model,
            estimated_cost_usd=estimate,
            actual_cost_usd=Decimal(0) if provider.name == "mock_elevenlabs" else None,
            metadata_json={
                "input_characters": len(request.text),
                "rate_usd_per_1000": str(rate),
                "outcome": "started",
            },
        )
        db.add(cost)
        db.flush()
        job_id, cost_id = job.id, cost.id
        db.commit()  # Claim before any external work; crashes never cause an automatic paid replay.

        result = provider.synthesize(request)
        cost = db.get(CostEvent, cost_id)
        if cost is None:
            raise AudioConflict("Audio job removed during generation")
        cost.metadata_json = cost.metadata_json | {
            "outcome": "provider_completed",
            "provider_request_id": result.request_id,
            "billed_characters": result.billed_characters,
        }
        db.add(cost)
        db.commit()  # Preserve reported usage even if subsequent storage fails.

        extension = "wav" if result.content_type == "audio/wav" else "mp3"
        key = f"videos/{video_id}/audio/{job_id}.{extension}"
        stored = storage.put(key, BytesIO(result.audio), content_type=result.content_type)
        owned_video(db, owner_id, video_id, lock=True)
        job = db.get(GenerationJob, job_id)
        if job is None or job.status != GenerationStatus.RUNNING:
            raise AudioConflict("Audio job changed during generation")
        asset = Asset(
            video_id=video_id,
            scene_id=scene_id,
            generation_job_id=job_id,
            type=AssetType.AUDIO,
            storage_backend=settings.storage_backend,
            bucket=settings.s3_bucket if settings.storage_backend == "s3" else "",
            object_key=key,
            content_type=stored.content_type,
            size_bytes=stored.size_bytes,
            sha256=stored.sha256,
            metadata_json={
                "alignment": result.alignment.model_dump() if result.alignment else None,
                "normalized_alignment": result.normalized_alignment.model_dump()
                if result.normalized_alignment
                else None,
            },
        )
        db.add(asset)
        job.status = GenerationStatus.SUCCEEDED
        job.completed_at = datetime.now(UTC)
        db.add(job)
        db.flush()
        db.commit()
        return asset
    except Exception:
        db.rollback()
        if key is not None:
            try:
                storage.delete(key)
            except Exception:
                logger.warning("Audio object cleanup failed")
        if job_id is not None:
            try:
                job = db.get(GenerationJob, job_id)
                if job is not None and job.status == GenerationStatus.RUNNING:
                    job.status = GenerationStatus.FAILED
                    job.completed_at = datetime.now(UTC)
                    job.error = "audio_generation_failed_requires_review"
                    db.add(job)
                    db.commit()
            except Exception:
                db.rollback()
                logger.warning("Audio job failure could not be recorded")
        raise
