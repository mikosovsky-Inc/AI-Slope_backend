import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session, create_engine

from app.core.config import get_settings
from app.db.session import get_db
from main import app


@pytest.fixture
def postgres_engine(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run against PostgreSQL")
    schema = "test_roles_" + uuid4().hex
    admin_engine = create_engine(url)
    engine = create_engine(
        url, connect_args={"options": f"-csearch_path={schema} -clock_timeout=10000"}
    )
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        monkeypatch.setattr("app.db.session.get_engine", lambda: engine)
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture
def pg_client(postgres_engine, settings, monkeypatch):
    command.upgrade(Config("alembic.ini"), "head")

    def get_session():
        with Session(postgres_engine, expire_on_commit=False) as session:
            yield session

    monkeypatch.setattr("main.get_settings", lambda: settings)
    monkeypatch.setattr("main.get_engine", lambda: postgres_engine)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = get_session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
