"""Add roles and promote the earliest existing user to admin."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("role", sa.String(5), nullable=False, server_default="user"))
    op.create_check_constraint("user_role", "users", "role IN ('admin', 'user')")
    # UUID breaks ties deterministically when timestamps are equal.
    op.execute(
        "UPDATE users SET role = 'admin' "
        "WHERE id = (SELECT id FROM users ORDER BY created_at, id LIMIT 1)"
    )


def downgrade() -> None:
    op.drop_constraint("user_role", "users", type_="check")
    op.drop_column("users", "role")
