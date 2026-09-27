from decimal import Decimal
from uuid import UUID

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import func, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db.base import Base
from app.modules.channels.models import Channel, ChannelBlueprint, ContentPillar

DATA = {
    "idea": "Polski kanał o dziwnych wydarzeniach historycznych",
    "language": "pl",
    "videos_per_day": 2,
    "budget_per_video_usd": "0.20",
}


def login(client, email):
    credentials = {"email": email, "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_channel_crud_blueprint_and_cascade(pg_client, postgres_engine):
    headers = login(pg_client, "owner@example.com")
    response = pg_client.post("/api/v1/channels", headers=headers, json=DATA)
    assert response.status_code == 201
    created = response.json()
    assert created["name"] == DATA["idea"]
    assert created["status"] == "draft"
    assert created["autopilot_mode"] == "manual"
    assert created["blueprint"]["content_pillars"] == []
    path = f"/api/v1/channels/{created['id']}"
    patch = {
        "name": "Historia",
        "autopilot_mode": "semi_auto",
        "budget_per_video_usd": "0.2501",
        "blueprint": {
            "configuration": {"tone": "mysterious"},
            "content_pillars": [
                {"name": "Zagadki", "description": "Tajemnice"},
                {"name": "Postacie"},
            ],
        },
    }
    response = pg_client.patch(path, headers=headers, json=patch)
    assert response.status_code == 200
    edited = response.json()
    assert edited["name"] == "Historia"
    assert Decimal(edited["budget_per_video_usd"]) == Decimal("0.2501")
    assert edited["blueprint"]["configuration"]["tone"] == "mysterious"
    assert [p["position"] for p in edited["blueprint"]["content_pillars"]] == [0, 1]
    assert pg_client.get(path, headers=headers).json() == edited
    for action, status in [("activate", "active"), ("pause", "paused"), ("activate", "active")]:
        first = pg_client.post(f"{path}/{action}", headers=headers)
        assert first.status_code == 200
        assert first.json()["status"] == status
        assert pg_client.post(f"{path}/{action}", headers=headers).json() == first.json()
    replacement = {"blueprint": {"content_pillars": [{"name": "Nowy filar"}]}}
    assert pg_client.patch(path, headers=headers, json=replacement).status_code == 200
    with Session(postgres_engine) as db:
        assert db.exec(select(func.count()).select_from(ContentPillar)).one() == 1
    page = pg_client.get("/api/v1/channels?limit=1&offset=0", headers=headers).json()
    assert page["total"] == 1 and page["items"][0]["id"] == created["id"]
    assert pg_client.get("/api/v1/channels?offset=1", headers=headers).json()["items"] == []
    assert pg_client.delete(path, headers=headers).status_code == 204
    assert pg_client.get(path, headers=headers).status_code == 404
    with Session(postgres_engine) as db:
        for model in [Channel, ChannelBlueprint, ContentPillar]:
            assert db.exec(select(func.count()).select_from(model)).one() == 0


def test_channel_ownership_even_for_admin(pg_client):
    admin = login(pg_client, "admin@example.com")
    owner = login(pg_client, "owner@example.com")
    created = pg_client.post("/api/v1/channels", headers=owner, json=DATA).json()
    path = f"/api/v1/channels/{created['id']}"
    assert pg_client.get("/api/v1/channels", headers=admin).json()["total"] == 0
    for method, suffix, payload in [
        ("GET", "", None),
        ("PATCH", "", {"name": "Hijacked"}),
        ("DELETE", "", None),
        ("POST", "/activate", None),
        ("POST", "/pause", None),
    ]:
        response = pg_client.request(method, path + suffix, headers=admin, json=payload)
        assert response.status_code == 404
    assert pg_client.get(path, headers=owner).json() == created


def test_channel_database_constraints_and_atomic_update(pg_client, postgres_engine):
    headers = login(pg_client, "owner@example.com")
    created = pg_client.post("/api/v1/channels", headers=headers, json=DATA).json()
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE content_pillars ADD CONSTRAINT test_reject CHECK (name <> 'RejectMe')"
            )
        )
    response = pg_client.patch(
        f"/api/v1/channels/{created['id']}",
        headers=headers,
        json={"name": "Must rollback", "blueprint": {"content_pillars": [{"name": "RejectMe"}]}},
    )
    assert response.status_code == 503
    assert pg_client.get(f"/api/v1/channels/{created['id']}", headers=headers).json() == created
    for column, value in [
        ("language", "zz"),
        ("status", "bad"),
        ("autopilot_mode", "full_auto"),
        ("videos_per_day", 0),
        ("budget_per_video_usd", -1),
    ]:
        with pytest.raises(IntegrityError), postgres_engine.begin() as connection:
            connection.execute(text(f"UPDATE channels SET {column} = :value"), {"value": value})
    with Session(postgres_engine) as db:
        user_channel = db.get(Channel, UUID(created["id"]))
        assert user_channel.status.value == "draft"


def test_channel_migration_matches_metadata_and_downgrades(postgres_engine):
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    with postgres_engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    indexes = {i["name"] for i in inspect(postgres_engine).get_indexes("channels")}
    assert {"ix_channels_owner_created", "ix_channels_status"} <= indexes
    command.downgrade(config, "0002")
    assert "channels" not in inspect(postgres_engine).get_table_names()
    assert "users" in inspect(postgres_engine).get_table_names()
    command.upgrade(config, "head")
    assert "content_pillars" in inspect(postgres_engine).get_table_names()
