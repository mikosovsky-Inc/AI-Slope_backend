import os
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import dramatiq
import pytest
from dramatiq.brokers.redis import RedisBroker
from sqlmodel import Session, select

from app.modules.tasks.models import Task, TaskStatus
from app.modules.videos.models import VideoScript
from app.workers.dispatcher import dispatch_once
from app.workers.runner import run_task


def test_real_redis_worker_and_duplicate_delivery(
    pg_client, postgres_engine, settings, story_factory
):
    redis_url = os.getenv("TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("Set TEST_REDIS_URL")
    headers, path, video, _ = story_factory(pg_client)
    settings.tasks_eager = False
    response = pg_client.post(path + "/script/generate", headers=headers)
    assert response.status_code == 202
    task_id = response.json()["id"]
    broker = RedisBroker(url=redis_url, namespace="test_tasks_" + uuid4().hex)

    def execute(identifier):
        run_task(postgres_engine, identifier, settings=settings)

    actor = dramatiq.actor(
        execute, actor_name="test_content", queue_name="content", broker=broker, max_retries=0
    )
    worker = dramatiq.Worker(broker, worker_threads=2)
    worker.start()
    try:
        dispatch_once(postgres_engine, lambda queue, identifier: actor.send(identifier))
        actor.send(task_id)
        broker.join("content", timeout=15000)
        with Session(postgres_engine) as db:
            task = db.get(Task, UUID(task_id))
            assert task.status == TaskStatus.SUCCEEDED
            assert task.attempts == 1
            assert len(db.exec(select(VideoScript)).all()) == 1
    finally:
        worker.stop(timeout=15000)
        broker.flush_all()
        broker.close()


def test_concurrent_api_idempotency(pg_client, postgres_engine, settings, story_factory):
    headers, path, _, _ = story_factory(pg_client)
    settings.tasks_eager = False

    def submit(_):
        return pg_client.post(
            path + "/script/generate", headers=headers | {"Idempotency-Key": "same-key"}
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    assert all(r.status_code == 202 for r in responses)
    assert responses[0].json()["id"] == responses[1].json()["id"]
    with Session(postgres_engine) as db:
        assert len(db.exec(select(Task)).all()) == 1


def test_video_and_task_creation_rollback(pg_client, postgres_engine, settings, story_factory):
    from sqlalchemy import event
    from sqlalchemy.exc import SQLAlchemyError

    from app.modules.ideas.models import ContentIdea, IdeaStatus
    from app.modules.videos.models import Video

    headers, _, _, channel = story_factory(pg_client)
    ideas = pg_client.get(channel + "/ideas", headers=headers).json()["items"]
    idea = next(i for i in ideas if i["status"] == "candidate")
    path = "/api/v1/ideas/" + idea["id"]
    pg_client.post(path + "/approve", headers=headers).raise_for_status()
    settings.tasks_eager = False

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO tasks"):
            raise SQLAlchemyError("simulated outbox failure")

    event.listen(postgres_engine, "before_cursor_execute", fail)
    try:
        assert pg_client.post(path + "/create-video", headers=headers).status_code == 503
    finally:
        event.remove(postgres_engine, "before_cursor_execute", fail)
    with Session(postgres_engine) as db:
        assert db.exec(select(Video).where(Video.idea_id == UUID(idea["id"]))).first() is None
        assert db.get(ContentIdea, UUID(idea["id"])).status == IdeaStatus.APPROVED
        assert db.exec(select(Task)).all() == []
