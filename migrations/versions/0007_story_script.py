"""Persist story outline and full narrative beats."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "video_scripts",
        sa.Column("outline", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "video_scripts",
        sa.Column("story", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.alter_column("video_scripts", "outline", server_default=None)
    op.alter_column("video_scripts", "story", server_default=None)


def downgrade() -> None:
    op.drop_column("video_scripts", "story")
    op.drop_column("video_scripts", "outline")
