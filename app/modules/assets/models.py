from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import JSON, BigInteger, CheckConstraint, DateTime, Index, UniqueConstraint
from sqlmodel import Field

from app.db.base import Base
from app.modules.videos.models import enum_type


class AssetType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLE = "subtitle"
    FINAL_VIDEO = "final_video"


class StorageBackend(StrEnum):
    LOCAL = "local"
    S3 = "s3"


class GenerationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class GenerationJob(Base, table=True):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        UniqueConstraint("scene_id", "idempotency_key", name="uq_generation_scene_key"),
        Index("ix_generation_status_created", "status", "created_at", "id"),
        CheckConstraint("retry_count >= 0", name="ck_generation_retries"),
        CheckConstraint("length(provider) > 0", name="ck_generation_provider"),
        CheckConstraint("length(idempotency_key) > 0", name="ck_generation_key"),
        CheckConstraint(
            "(status = 'pending' AND started_at IS NULL AND completed_at IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status IN ('succeeded', 'failed') AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND completed_at >= started_at)",
            name="ck_generation_timestamps",
        ),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    scene_id: UUID = Field(foreign_key="scenes.id", ondelete="CASCADE", index=True)
    provider: str = Field(max_length=100)
    type: AssetType = Field(sa_type=enum_type(AssetType, "generation_type"))
    status: GenerationStatus = Field(
        default=GenerationStatus.PENDING,
        sa_type=enum_type(GenerationStatus, "generation_status"),
    )
    idempotency_key: str = Field(max_length=200)
    parameters: dict = Field(default_factory=dict, sa_type=JSON)
    retry_count: int = Field(default=0)
    # Store a sanitized error code/message, never raw provider responses or credentials.
    error: str | None = Field(default=None, max_length=1000)
    started_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    completed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )


class Asset(Base, table=True):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("storage_backend", "bucket", "object_key", name="uq_asset_location"),
        UniqueConstraint("generation_job_id", name="uq_asset_generation_job"),
        CheckConstraint("size_bytes > 0", name="ck_asset_size"),
        CheckConstraint("length(object_key) > 0", name="ck_asset_key"),
        CheckConstraint(
            "(storage_backend = 'local' AND bucket = '') OR "
            "(storage_backend = 's3' AND length(bucket) > 0)",
            name="ck_asset_bucket",
        ),
    )
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    video_id: UUID = Field(foreign_key="videos.id", ondelete="CASCADE", index=True)
    scene_id: UUID | None = Field(
        default=None, foreign_key="scenes.id", ondelete="CASCADE", index=True
    )
    generation_job_id: UUID | None = Field(
        default=None, foreign_key="generation_jobs.id", ondelete="SET NULL"
    )
    type: AssetType = Field(sa_type=enum_type(AssetType, "asset_type"))
    storage_backend: StorageBackend = Field(sa_type=enum_type(StorageBackend, "storage_backend"))
    bucket: str = Field(default="", max_length=255)
    object_key: str = Field(max_length=512)
    content_type: str = Field(max_length=100)
    size_bytes: int = Field(sa_type=BigInteger)
    sha256: str = Field(min_length=64, max_length=64)
    metadata_json: dict = Field(default_factory=dict, sa_type=JSON)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), sa_type=DateTime(timezone=True)
    )
