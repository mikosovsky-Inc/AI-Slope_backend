import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
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

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

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


@pytest.fixture
def top5_factory():
    def create(client):
        credentials = {"email": "top5@example.com", "password": "long-test-password"}
        client.post("/api/v1/auth/register", json=credentials).raise_for_status()
        token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        channel = client.post(
            "/api/v1/channels",
            headers=headers,
            json={"idea": "Historical discoveries and facts", "language": "en"},
        ).json()
        channel_path = "/api/v1/channels/" + channel["id"]
        client.post(channel_path + "/analyze", headers=headers).raise_for_status()
        ideas = client.post(channel_path + "/ideas/generate", headers=headers).json()["items"]
        idea = next(i for i in ideas if i["format"] == "top5")
        path = "/api/v1/ideas/" + idea["id"]
        client.post(path + "/approve", headers=headers).raise_for_status()
        video = client.post(path + "/create-video", headers=headers).json()
        return headers, "/api/v1/videos/" + video["id"], video["id"], channel_path

    return create


@pytest.fixture
def source_documents():
    from app.modules.research.schemas import SourceDocument

    return [
        SourceDocument(
            source_url=f"https://example.com/fixture/{i}",
            source_title=f"Discoveries test fixture {i}",
            language="en",
            content="\n".join(
                f"Test record {i}-{j} contains a sample observation." for j in range(3)
            ),
            metadata={"fixture": True},
        )
        for i in range(2)
    ]
