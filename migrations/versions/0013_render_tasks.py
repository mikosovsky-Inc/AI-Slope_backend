"""Allow local render tasks in the durable queue."""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

KINDS = "'analyze','ideas','competitors','story','research','top5','direct','image','video','audio'"


def upgrade() -> None:
    op.drop_constraint("task_kind", "tasks", type_="check")
    op.create_check_constraint("task_kind", "tasks", f"kind IN ({KINDS},'render')")


def downgrade() -> None:
    # Refuse to discard existing render history implicitly.
    op.drop_constraint("task_kind", "tasks", type_="check")
    op.create_check_constraint("task_kind", "tasks", f"kind IN ({KINDS})")
