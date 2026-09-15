import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from app.core.config import Settings, get_settings
from app.db.base import Base
from app.db.session import get_db
from app.models.user import User  # noqa: F401
from main import app


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://test:test@localhost/test",
        jwt_secret_key="test-secret-" + "x" * 48,
    )


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


@pytest.fixture
def client(db, settings, monkeypatch):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_settings] = lambda: settings
    monkeypatch.setattr("main.get_settings", lambda: settings)
    monkeypatch.setattr("main.get_engine", lambda: db.get_bind())
    try:
        with TestClient(app) as client:
            try:
                yield client
            finally:
                db.close()
    finally:
        app.dependency_overrides.clear()
