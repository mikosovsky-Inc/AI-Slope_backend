from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.integrations.elevenlabs.mock import MockTTSProvider
from app.integrations.storage.local import LocalStorageProvider
from app.modules.assets.models import Asset, GenerationJob, GenerationStatus
from app.modules.audio.service import AudioConflict, generate_scene_audio
from app.modules.costs.models import CostEvent
from app.modules.videos.models import Scene


def test_audio_concurrency_and_cascade(
    pg_client, postgres_engine, settings, tmp_path, story_factory
):
    headers, path, video, channel = story_factory(pg_client)
    pg_client.post(path + "/script/generate", headers=headers).raise_for_status()
    owner = UUID(pg_client.get("/api/v1/auth/me", headers=headers).json()["id"])
    with Session(postgres_engine) as db:
        scene = db.exec(select(Scene)).first().id

    def generate(_):
        with Session(postgres_engine, expire_on_commit=False) as db:
            try:
                return generate_scene_audio(
                    db,
                    owner,
                    UUID(video),
                    scene,
                    MockTTSProvider(),
                    LocalStorageProvider(tmp_path),
                    settings,
                ).id
            except AudioConflict:
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(generate, range(2)))
    assert any(results)
    with Session(postgres_engine) as db:
        assert len(db.exec(select(CostEvent)).all()) == 1
        assert len(db.exec(select(Asset)).all()) == 1
        assert db.exec(select(GenerationJob)).one().status == GenerationStatus.SUCCEEDED
    pg_client.delete(channel, headers=headers).raise_for_status()
    with Session(postgres_engine) as db:
        assert db.exec(select(CostEvent)).all() == []


def test_failed_asset_commit_cleans_object_and_keeps_cost(
    pg_client, postgres_engine, settings, tmp_path, story_factory
):
    headers, path, video, _ = story_factory(pg_client)
    pg_client.post(path + "/script/generate", headers=headers).raise_for_status()
    owner = UUID(pg_client.get("/api/v1/auth/me", headers=headers).json()["id"])

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO assets"):
            raise SQLAlchemyError("simulated write failure")

    event.listen(postgres_engine, "before_cursor_execute", fail)
    try:
        import pytest

        with Session(postgres_engine) as db:
            scene = db.exec(select(Scene)).first().id
            with pytest.raises(SQLAlchemyError):
                generate_scene_audio(
                    db,
                    owner,
                    UUID(video),
                    scene,
                    MockTTSProvider(),
                    LocalStorageProvider(tmp_path),
                    settings,
                )
    finally:
        event.remove(postgres_engine, "before_cursor_execute", fail)
    with Session(postgres_engine) as db:
        assert db.exec(select(Asset)).all() == []
        assert db.exec(select(CostEvent)).one().metadata_json["outcome"] == "provider_completed"
        assert db.exec(select(GenerationJob)).one().status == GenerationStatus.FAILED
    assert list(tmp_path.rglob("*.wav")) == []
