from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import Engine
from sqlmodel import Session, create_engine

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(str(get_settings().database_url), pool_pre_ping=True)


def get_db() -> Generator[Session, None, None]:
    with Session(get_engine(), expire_on_commit=False) as session:
        yield session
