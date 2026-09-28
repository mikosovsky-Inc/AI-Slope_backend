from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.modules.quality.models import QualityCheck, QualityStatus
from app.modules.tasks.models import Task, TaskKind, TaskStatus
from app.modules.tasks.service import enqueue
from app.modules.videos.models import Video, VideoStatus, VideoStatusEvent
from app.modules.videos.service import transition_video
from app.workers.runner import run_task


def prepare(pg_client, postgres_engine, story_factory):
    headers, path, video_id, channel = story_factory(pg_client)
    pg_client.post(path + "/script/generate", headers=headers).raise_for_status()
    render_id = pg_client.post(path + "/render", headers=headers, json={}).json()["id"]
    with Session(postgres_engine, expire_on_commit=False) as db:
        render = db.get(Task, UUID(render_id))
        render.status = TaskStatus.SUCCEEDED
        db.add(render)
        for status in (
            VideoStatus.GENERATING_ASSETS,
            VideoStatus.ASSETS_READY,
            VideoStatus.GENERATING_AUDIO,
            VideoStatus.READY_TO_RENDER,
            VideoStatus.RENDERING,
            VideoStatus.QUALITY_CHECK,
        ):
            transition_video(db, UUID(video_id), status, reason="Quality integration fixture")
        db.commit()
    quality_id = pg_client.post(path + "/quality-check", headers=headers).json()["id"]
    return headers, path, UUID(video_id), UUID(render_id), UUID(quality_id), channel


def test_quality_constraints_and_cascade(pg_client, postgres_engine, story_factory):
    headers, _, video_id, render_id, quality_id, channel = prepare(
        pg_client, postgres_engine, story_factory
    )
    with Session(postgres_engine, expire_on_commit=False) as db:
        check = QualityCheck(
            video_id=video_id, render_task_id=render_id, task_id=quality_id, attempt=1
        )
        db.add(check)
        db.commit()
        check.status = QualityStatus.PASSED
        with pytest.raises(IntegrityError):
            db.commit()  # Terminal verdict requires a completion time.
        db.rollback()
        check.status = QualityStatus.PASSED
        check.completed_at = datetime.now(UTC)
        db.commit()
        check.attempt = 0
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        owner_id = db.get(Task, quality_id).owner_id
        other = enqueue(db, owner_id, TaskKind.QUALITY, video_id=video_id)
        with pytest.raises(IntegrityError):
            db.add(
                QualityCheck(
                    video_id=video_id, render_task_id=render_id, task_id=other.id, attempt=2
                )
            )
            db.commit()  # Only one report for the same rendered artifact.
        db.rollback()
    pg_client.delete(channel, headers=headers).raise_for_status()
    with Session(postgres_engine) as db:
        assert db.exec(select(QualityCheck)).all() == []


def test_concurrent_quality_delivery_and_submission(
    pg_client, postgres_engine, story_factory, settings
):
    headers, path, video_id, _, quality_id, _ = prepare(pg_client, postgres_engine, story_factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda _: pg_client.post(path + "/quality-check", headers=headers), range(2))
        )
    assert all(r.status_code == 202 and r.json()["id"] == str(quality_id) for r in responses)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda _: run_task(postgres_engine, str(quality_id), settings=settings), range(2)
            )
        )
    with Session(postgres_engine) as db:
        assert db.get(Task, quality_id).status == TaskStatus.SUCCEEDED
        check = db.exec(select(QualityCheck)).one()
        assert check.status == QualityStatus.FAILED  # No manifest/final file in this fixture.
        assert db.get(Video, video_id).status == VideoStatus.FAILED
        events = db.exec(
            select(VideoStatusEvent).where(
                VideoStatusEvent.video_id == video_id,
                VideoStatusEvent.to_status == VideoStatus.FAILED,
            )
        ).all()
        assert len(events) == 1
