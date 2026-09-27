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


def test_concurrent_first_registrations(postgres_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlmodel import select

    from app.models.roles import UserRole
    from app.models.user import User
    from app.schemas.user import UserCreate
    from app.services.auth import register_user

    command.upgrade(Config("alembic.ini"), "head")
    barrier = Barrier(4)

    def register(index):
        with Session(postgres_engine, expire_on_commit=False) as session:
            barrier.wait(timeout=10)
            user = register_user(
                session,
                UserCreate(email=f"creator{index}@example.com", password="long-test-password"),
            )
            return user.role

    with ThreadPoolExecutor(max_workers=4) as pool:
        roles = list(pool.map(register, range(4)))
    assert roles.count(UserRole.ADMIN) == 1
    assert roles.count(UserRole.USER) == 3
    with Session(postgres_engine) as session:
        users = session.exec(select(User)).all()
        assert len(users) == 4
        assert sum(user.role == UserRole.ADMIN for user in users) == 1


def test_roles_migration_existing_users(postgres_engine):
    from sqlalchemy.exc import IntegrityError

    config = Config("alembic.ini")
    command.upgrade(config, "0001")
    # Insert in reverse order to check that timestamps, not physical order, decide.
    with postgres_engine.begin() as connection:
        for email, created_at in [
            ("newer@example.com", "2026-02-01T00:00:00Z"),
            ("oldest@example.com", "2026-01-01T00:00:00Z"),
        ]:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, password_hash, created_at) "
                    "VALUES (:id, :email, 'existing-hash', :created_at)"
                ),
                {"id": uuid4(), "email": email, "created_at": created_at},
            )
    command.upgrade(config, "head")
    with postgres_engine.connect() as connection:
        roles = dict(connection.execute(text("SELECT email, role FROM users")).all())
    assert roles == {"oldest@example.com": "admin", "newer@example.com": "user"}
    with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
        connection.execute(text("UPDATE users SET role = 'other'"))
    command.downgrade(config, "0001")
    assert "role" not in {
        column["name"] for column in inspect(postgres_engine).get_columns("users")
    }
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM users")) == 2
