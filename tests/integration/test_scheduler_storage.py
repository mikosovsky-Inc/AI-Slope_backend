from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID

import pytest
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.modules.channels.models import AutopilotMode, Channel, ChannelStatus
from app.modules.ideas.models import ContentIdea, IdeaStatus
from app.modules.scheduler.models import DailyPlan
from app.modules.scheduler.service import plan_channel
from app.modules.tasks.models import Task, TaskKind
from app.modules.videos.models import Video


def prepare(pg_client, postgres_engine, story_factory):
    headers, _, video_id, channel_path = story_factory(pg_client)
    with Session(postgres_engine, expire_on_commit=False) as db:
        channel = db.exec(select(Channel)).one()
        channel.status, channel.autopilot_mode = ChannelStatus.ACTIVE, AutopilotMode.SEMI_AUTO
        channel.videos_per_day = 3
        db.add(channel)
        db.commit()
        return channel.id, headers, channel_path, UUID(video_id)


def test_concurrent_schedulers_never_duplicate_daily_deficit(
    pg_client, postgres_engine, settings, story_factory
):
    channel_id, _, _, _ = prepare(pg_client, postgres_engine, story_factory)
    barrier = Barrier(3)
    now = datetime.now(UTC)

    def plan(_):
        with Session(postgres_engine, expire_on_commit=False) as db:
            barrier.wait(timeout=10)
            return plan_channel(db, channel_id, settings, now=now)

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(plan, range(3)))
    assert sum(len(r.created_video_ids) for r in results) == 2
    with Session(postgres_engine) as db:
        assert len(db.exec(select(Video)).all()) == 3
        assert len(db.exec(select(DailyPlan)).all()) == 1
        assert len(db.exec(select(Task).where(Task.video_id.is_not(None))).all()) == 2


def test_concurrent_schedulers_queue_one_idea_batch(
    pg_client, postgres_engine, settings, story_factory
):
    channel_id, _, _, _ = prepare(pg_client, postgres_engine, story_factory)
    with Session(postgres_engine) as db:
        for idea in db.exec(select(ContentIdea).where(ContentIdea.status != IdeaStatus.USED)).all():
            idea.status = IdeaStatus.REJECTED
            db.add(idea)
        db.commit()
    barrier = Barrier(2)

    def plan(_):
        with Session(postgres_engine, expire_on_commit=False) as db:
            barrier.wait(timeout=10)
            return plan_channel(db, channel_id, settings)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(plan, range(2)))
    assert sum(r.queued_idea_task_id is not None for r in results) == 1
    with Session(postgres_engine) as db:
        assert len(db.exec(select(Task).where(Task.kind == TaskKind.IDEAS)).all()) == 1
        assert len(db.exec(select(DailyPlan)).one().idea_task_ids) == 1


def test_scheduler_video_plan_outbox_rollback_and_cascade(
    pg_client, postgres_engine, settings, story_factory
):
    channel_id, headers, path, _ = prepare(pg_client, postgres_engine, story_factory)
    with Session(postgres_engine) as db:
        original_statuses = {i.id: i.status for i in db.exec(select(ContentIdea)).all()}

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO tasks"):
            raise SQLAlchemyError("Test outbox unavailable")

    event.listen(postgres_engine, "before_cursor_execute", fail)
    try:
        with Session(postgres_engine, expire_on_commit=False) as db, pytest.raises(SQLAlchemyError):
            plan_channel(db, channel_id, settings)
    finally:
        event.remove(postgres_engine, "before_cursor_execute", fail)
    with Session(postgres_engine, expire_on_commit=False) as db:
        assert len(db.exec(select(Video)).all()) == 1
        assert not db.exec(select(Task)).all()
        assert not db.exec(select(DailyPlan)).all()
        assert {i.id: i.status for i in db.exec(select(ContentIdea)).all()} == original_statuses
        assert len(plan_channel(db, channel_id, settings).created_video_ids) == 2
    pg_client.delete(path, headers=headers).raise_for_status()
    with Session(postgres_engine) as db:
        assert not db.exec(select(DailyPlan)).all()
