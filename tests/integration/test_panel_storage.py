from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

from sqlmodel import Session, select

from app.modules.channels.models import Channel
from app.modules.panel.service import regenerate, retry
from app.modules.tasks.models import Task, TaskKind, TaskStatus
from app.modules.videos.models import VideoStatus, VideoStatusEvent
from app.modules.videos.service import transition_video


def test_postgres_panel_reads_and_concurrent_regeneration(
    pg_client, postgres_engine, story_factory
):
    headers, path, vid, channel = story_factory(pg_client)
    pg_client.post(path + "/script/generate", headers=headers).raise_for_status()
    scene = pg_client.get(path + "/scenes", headers=headers).json()[0]
    with Session(postgres_engine) as db:
        owner_id = db.get(Channel, UUID(channel.rsplit("/", 1)[1])).owner_id
    barrier = Barrier(2)

    def submit(_):
        with Session(postgres_engine, expire_on_commit=False) as db:
            barrier.wait(timeout=10)
            return regenerate(db, owner_id, UUID(scene["id"]), "audio", "concurrent-panel")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert results[0].id == results[1].id
    assert pg_client.get("/api/v1/dashboard", headers=headers).json()["videos"] == 1
    assert pg_client.get(channel + "/costs", headers=headers).json()["summary"]["events"] == 0
    with Session(postgres_engine) as db:
        assert len(db.exec(select(Task).where(Task.scene_id == UUID(scene["id"]))).all()) == 1


def test_postgres_concurrent_retry_has_one_recovery_event(
    pg_client, postgres_engine, story_factory
):
    headers, path, vid, channel = story_factory(pg_client)
    with Session(postgres_engine, expire_on_commit=False) as db:
        owner_id = db.get(Channel, UUID(channel.rsplit("/", 1)[1])).owner_id
        transition_video(db, UUID(vid), VideoStatus.SCRIPTING, reason="test")
        transition_video(db, UUID(vid), VideoStatus.FAILED, reason="test")
        task = Task(
            owner_id=owner_id,
            video_id=UUID(vid),
            kind=TaskKind.STORY,
            status=TaskStatus.FAILED,
            idempotency_key="failed",
        )
        db.add(task)
        db.commit()
        task_id = task.id
    barrier = Barrier(2)

    def submit(_):
        with Session(postgres_engine, expire_on_commit=False) as db:
            barrier.wait(timeout=10)
            return retry(db, owner_id, UUID(vid), task_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert results[0].id == results[1].id == task_id
    with Session(postgres_engine) as db:
        assert db.get(Task, task_id).checkpoint["panel_retries"] == 1
        events = db.exec(
            select(VideoStatusEvent).where(VideoStatusEvent.video_id == UUID(vid))
        ).all()
        assert sum(e.from_status == VideoStatus.FAILED for e in events) == 1


def test_retry_transaction_rolls_back_history_when_outbox_update_fails(
    pg_client, postgres_engine, story_factory
):
    import pytest
    from sqlalchemy import event
    from sqlalchemy.exc import SQLAlchemyError

    from app.modules.videos.models import Video

    _, _, vid, channel = story_factory(pg_client)
    with Session(postgres_engine, expire_on_commit=False) as db:
        owner_id = db.get(Channel, UUID(channel.rsplit("/", 1)[1])).owner_id
        transition_video(db, UUID(vid), VideoStatus.SCRIPTING, reason="test")
        transition_video(db, UUID(vid), VideoStatus.FAILED, reason="test")
        task = Task(
            owner_id=owner_id,
            video_id=UUID(vid),
            kind=TaskKind.STORY,
            status=TaskStatus.FAILED,
            idempotency_key="rollback",
        )
        db.add(task)
        db.commit()
        task_id = task.id

    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE tasks"):
            raise SQLAlchemyError("Test outbox unavailable")

    event.listen(postgres_engine, "before_cursor_execute", fail)
    try:
        with Session(postgres_engine) as db, pytest.raises(SQLAlchemyError):
            retry(db, owner_id, UUID(vid), task_id)
    finally:
        event.remove(postgres_engine, "before_cursor_execute", fail)
    with Session(postgres_engine) as db:
        assert db.get(Video, UUID(vid)).status == VideoStatus.FAILED
        assert db.get(Task, task_id).status == TaskStatus.FAILED
        events = db.exec(
            select(VideoStatusEvent).where(VideoStatusEvent.video_id == UUID(vid))
        ).all()
        assert not any(e.from_status == VideoStatus.FAILED for e in events)
