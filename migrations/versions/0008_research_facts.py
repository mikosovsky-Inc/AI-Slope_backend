"""Persist research evidence and scene attribution."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "research_documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "video_id", sa.Uuid(), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("source_url", sa.String(2000), nullable=False),
        sa.Column("source_title", sa.String(500), nullable=False),
        sa.Column("content", sa.String(8000), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("video_id", "source_url", name="uq_research_document_url"),
    )
    op.create_index("ix_research_documents_video_id", "research_documents", ["video_id"])
    op.create_table(
        "research_facts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("research_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("statement", sa.String(2000), nullable=False),
        sa.Column("source_url", sa.String(2000), nullable=False),
        sa.Column("source_title", sa.String(500), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_research_confidence"),
    )
    op.create_index("ix_research_facts_document_id", "research_facts", ["document_id"])
    op.create_table(
        "scene_research_facts",
        sa.Column(
            "scene_id", sa.Uuid(), sa.ForeignKey("scenes.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "fact_id",
            sa.Uuid(),
            sa.ForeignKey("research_facts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("scene_research_facts")
    op.drop_table("research_facts")
    op.drop_table("research_documents")
