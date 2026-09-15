"""Real PostgreSQL test; creates and removes only a dedicated random schema."""

import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlmodel import Session, create_engine

from app.core.config import get_settings
from app.db.session import get_db
from main import app


def test_migration_registration_and_duplicate_email(monkeypatch, settings):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to run against PostgreSQL")
    schema = "test_auth_" + uuid4().hex
    admin_engine = create_engine(url)
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        monkeypatch.setattr("app.db.session.get_engine", lambda: engine)
        monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
        monkeypatch.setattr("main.get_settings", lambda: settings)
        monkeypatch.setattr("main.get_engine", lambda: engine)
        config = Config("alembic.ini")
        command.upgrade(config, "head")
        assert "users" in inspect(engine).get_table_names()
        with Session(engine, expire_on_commit=False) as session:
            app.dependency_overrides[get_db] = lambda: session
            app.dependency_overrides[get_settings] = lambda: settings
            with TestClient(app) as client:
                data = {"email": "Creator@example.com", "password": "long-enough-password"}
                assert client.post("/api/v1/auth/register", json=data).status_code == 201
                data["email"] = "creator@example.com"
                assert client.post("/api/v1/auth/register", json=data).status_code == 409
                token = client.post("/api/v1/auth/login", json=data).json()["access_token"]
                assert (
                    client.get(
                        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
                    ).status_code
                    == 200
                )
                session.close()
        command.downgrade(config, "base")
        assert "users" not in inspect(engine).get_table_names()
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
