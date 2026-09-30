from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlmodel import select

from app.demo import seed_demo
from app.modules.assets.models import Asset, AssetType
from app.modules.channels.models import Channel
from app.modules.production.service import advance_production
from app.modules.tasks.models import Task, TaskKind, TaskStatus
from app.modules.videos.models import Video, VideoStatus
from app.workers.runner import run_task


def register(client):
    credentials = {"email": "demo@example.com", "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    return credentials["email"]


def run_pending(db, settings, *, stop_before=None):
    for _ in range(100):
        db.expire_all()
        tasks = db.exec(select(Task).where(Task.status == TaskStatus.QUEUED)).all()
        if not tasks:
            return
        if stop_before and any(t.kind == stop_before for t in tasks):
            return
        ids = [t.id for t in tasks]
        for identifier in ids:
            task = db.get(Task, identifier)
            task.available_at = datetime.now(UTC)
            db.add(task)
        db.commit()
        for identifier in ids:
            run_task(db.get_bind(), str(identifier), settings=settings)
    pytest.fail("Pipeline did not settle")


def test_demo_idempotent_and_mock_only(client, db, settings):
    email = register(client)
    with pytest.raises(ValueError, match="mock"):
        seed_demo(db, settings.model_copy(update={"external_providers_mode": "live"}), email)
    assert not db.exec(select(Channel)).all()
    seeded = seed_demo(db, settings, email)
    generated = seed_demo(db, settings, email, generate=True)
    assert [i.channel_id for i in seeded.items] == [i.channel_id for i in generated.items]
    assert seed_demo(db, settings, email, generate=True) == generated
    assert len(db.exec(select(Channel)).all()) == 2
    assert len(db.exec(select(Task)).all()) == 2


def test_demo_both_formats_reach_ready(client, db, settings, tmp_path):
    settings.storage_local_root = tmp_path
    result = seed_demo(db, settings, register(client), generate=True)
    run_pending(db, settings)
    tasks = db.exec(select(Task)).all()
    assert all(t.status == TaskStatus.SUCCEEDED for t in tasks), [
        (t.kind, t.status, t.error) for t in tasks
    ]
    for item in result.items:
        assert db.get(Video, item.video_id).status == VideoStatus.READY
        final = db.exec(
            select(Asset).where(
                Asset.video_id == item.video_id, Asset.type == AssetType.FINAL_VIDEO
            )
        ).one()
        assert (tmp_path / final.object_key).stat().st_size > 0
        render = db.exec(
            select(Task).where(Task.video_id == item.video_id, Task.kind == TaskKind.RENDER)
        ).one()
        assert render.parameters["input_manifest"]["scenes"]
    count = len(tasks)
    for task in tasks:
        run_task(db.get_bind(), str(task.id), settings=settings)
    assert len(db.exec(select(Task)).all()) == count


def test_failed_visual_blocks_audio_and_replay_is_idempotent(client, db, settings):
    seed_demo(db, settings, register(client), generate=True)
    run_pending(db, settings, stop_before=TaskKind.IMAGE)
    direct = db.exec(select(Task).where(Task.kind == TaskKind.DIRECT)).first()
    assert direct.status == TaskStatus.SUCCEEDED
    before = len(db.exec(select(Task)).all())
    advance_production(db, direct)
    db.commit()
    assert len(db.exec(select(Task)).all()) == before
    visual = db.exec(select(Task).where(Task.kind == TaskKind.IMAGE)).first()
    visual.status = TaskStatus.FAILED
    db.add(visual)
    advance_production(db, visual)
    db.commit()
    assert not db.exec(select(Task).where(Task.kind == TaskKind.AUDIO)).all()


def test_outbox_failure_rolls_back_entire_fanout(client, db, settings, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    from app.modules.production import service

    seed_demo(db, settings, register(client), generate=True)
    run_pending(db, settings, stop_before=TaskKind.DIRECT)
    direct = db.exec(select(Task).where(Task.kind == TaskKind.DIRECT)).first()
    identifier, video_id = direct.id, direct.video_id
    original = service.enqueue
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SQLAlchemyError("Simulated transactional outbox failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "enqueue", fail_second)
    run_task(db.get_bind(), str(identifier), settings=settings)
    db.expire_all()
    assert db.get(Task, identifier).status == TaskStatus.NEEDS_REVIEW
    assert db.get(Video, video_id).status == VideoStatus.SCRIPT_READY
    assert not db.exec(select(Task).where(Task.kind.in_([TaskKind.IMAGE, TaskKind.VIDEO]))).all()


def test_produce_requires_owned_completed_script(client, db, settings, story_factory):
    headers, path, identifier, _ = story_factory(client)
    assert client.post(path + "/produce").status_code == 401
    assert client.post(path + "/produce", headers=headers).status_code == 409
    client.post(path + "/script/generate", headers=headers).raise_for_status()
    first = client.post(path + "/produce", headers=headers)
    assert first.status_code == 202
    assert client.post(path + "/produce", headers=headers).json()["id"] == first.json()["id"]
    run_task(db.get_bind(), first.json()["id"], settings=settings)
    db.expire_all()
    assert db.get(Video, UUID(identifier)).status == VideoStatus.GENERATING_ASSETS
    email = register(client)
    token = client.post(
        "/api/v1/auth/login", json={"email": email, "password": "long-test-password"}
    ).json()["access_token"]
    assert (
        client.post(path + "/produce", headers={"Authorization": "Bearer " + token}).status_code
        == 404
    )
