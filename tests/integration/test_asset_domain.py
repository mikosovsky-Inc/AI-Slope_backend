from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.modules.assets.models import (
    Asset,
    AssetType,
    GenerationJob,
    GenerationStatus,
    StorageBackend,
)
from app.modules.videos.models import Scene


def test_jobs_assets_constraints_and_cascades(pg_client, postgres_engine, story_factory):
    headers, path, video_id, channel_path = story_factory(pg_client)
    pg_client.post(path + "/script/generate", headers=headers).raise_for_status()
    with Session(postgres_engine, expire_on_commit=False) as db:
        scene = db.exec(select(Scene)).first()
        job = GenerationJob(
            scene_id=scene.id,
            provider="mock",
            type=AssetType.IMAGE,
            idempotency_key="visual-v1",
            parameters={"prompt": "test"},
        )
        db.add(job)
        db.commit()
        assert job.status == GenerationStatus.PENDING
        assert job.retry_count == 0
        assert job.parameters == {"prompt": "test"}
        with pytest.raises(IntegrityError):
            db.add(
                GenerationJob(
                    scene_id=scene.id,
                    provider="mock",
                    type=AssetType.IMAGE,
                    idempotency_key="visual-v1",
                )
            )
            db.commit()
        db.rollback()
        job.status = GenerationStatus.RUNNING
        with pytest.raises(IntegrityError):
            db.commit()  # A running job must have started_at.
        db.rollback()
        job.status = GenerationStatus.RUNNING
        job.started_at = datetime.now(UTC)
        db.commit()
        job.status = GenerationStatus.SUCCEEDED
        job.completed_at = datetime.now(UTC)
        asset = Asset(
            video_id=UUID(video_id),
            scene_id=scene.id,
            generation_job_id=job.id,
            type=AssetType.IMAGE,
            storage_backend=StorageBackend.S3,
            bucket="test",
            object_key=f"{video_id}/{uuid4()}.png",
            content_type="image/png",
            size_bytes=10,
            sha256="a" * 64,
        )
        db.add(asset)
        db.commit()
        data = asset.model_dump(exclude={"id"})
        data["object_key"] = "other/key"
        with pytest.raises(IntegrityError):
            db.add(Asset(**data))  # One result per generation job, even under retries.
            db.commit()
        db.rollback()
        data["generation_job_id"] = None
        data["size_bytes"] = 0
        with pytest.raises(IntegrityError):
            db.add(Asset(**data))
            db.commit()
        db.rollback()
        # Removing a job preserves the already produced asset metadata.
        db.delete(job)
        db.commit()
        db.refresh(asset)
        assert asset.generation_job_id is None
    assert pg_client.delete(channel_path, headers=headers).status_code == 204
    with Session(postgres_engine) as db:
        assert db.exec(select(Asset)).all() == []
        assert db.exec(select(GenerationJob)).all() == []
