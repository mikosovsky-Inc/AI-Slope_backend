"""Asset metadata and generation jobs."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def types(name):
    return sa.Enum(
        "image",
        "video",
        "audio",
        "subtitle",
        "final_video",
        name=name,
        native_enum=False,
        create_constraint=True,
    )


def upgrade() -> None:
    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "scene_id", sa.Uuid(), sa.ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("provider", sa.String(100), nullable=False),
        sa.Column("type", types("generation_type"), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "succeeded",
                "failed",
                name="generation_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(1000), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scene_id", "idempotency_key", name="uq_generation_scene_key"),
        sa.CheckConstraint("retry_count >= 0", name="ck_generation_retries"),
        sa.CheckConstraint("length(provider) > 0", name="ck_generation_provider"),
        sa.CheckConstraint("length(idempotency_key) > 0", name="ck_generation_key"),
        sa.CheckConstraint(
            "(status = 'pending' AND started_at IS NULL AND completed_at IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL) OR "
            "(status IN ('succeeded', 'failed') AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND completed_at >= started_at)",
            name="ck_generation_timestamps",
        ),
    )
    op.create_index("ix_generation_jobs_scene_id", "generation_jobs", ["scene_id"])
    op.create_index(
        "ix_generation_status_created", "generation_jobs", ["status", "created_at", "id"]
    )
    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "video_id", sa.Uuid(), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "scene_id", sa.Uuid(), sa.ForeignKey("scenes.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column(
            "generation_job_id",
            sa.Uuid(),
            sa.ForeignKey("generation_jobs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("type", types("asset_type"), nullable=False),
        sa.Column(
            "storage_backend",
            sa.Enum(
                "local", "s3", name="storage_backend", native_enum=False, create_constraint=True
            ),
            nullable=False,
        ),
        sa.Column("bucket", sa.String(255), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("storage_backend", "bucket", "object_key", name="uq_asset_location"),
        sa.UniqueConstraint("generation_job_id", name="uq_asset_generation_job"),
        sa.CheckConstraint("size_bytes > 0", name="ck_asset_size"),
        sa.CheckConstraint("length(object_key) > 0", name="ck_asset_key"),
        sa.CheckConstraint(
            "(storage_backend = 'local' AND bucket = '') OR "
            "(storage_backend = 's3' AND length(bucket) > 0)",
            name="ck_asset_bucket",
        ),
    )
    op.create_index("ix_assets_video_id", "assets", ["video_id"])
    op.create_index("ix_assets_scene_id", "assets", ["scene_id"])


def downgrade() -> None:
    op.drop_table("assets")
    op.drop_table("generation_jobs")
