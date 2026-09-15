from alembic import context

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_engine
from app.models import User  # noqa: F401

if context.is_offline_mode():
    context.configure(
        url=str(get_settings().database_url),
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    with get_engine().connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
