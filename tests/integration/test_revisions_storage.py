from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.models.user import User
from app.modules.assets.models import Asset, AssetType, StorageBackend
from app.modules.quality.models import QualityCheck, QualityStatus
from app.modules.revisions.models import RevisionStatus, TaskRecovery, VideoRevision
from app.modules.tasks.models import Task, TaskKind, TaskStatus
from app.modules.videos.models import Scene, Video, VideoStatus


def prepare(client, engine, story_factory):
    headers, path, video_id, _ = story_factory(client)
    client.post(path + "/script/generate", headers=headers).raise_for_status()
    client.post(path + "/direct", headers=headers).raise_for_status()
    with Session(engine) as db:
        owner = db.exec(select(User)).one()
        video = db.get(Video, UUID(video_id))
        asset = Asset(
            video_id=video.id,
            type=AssetType.FINAL_VIDEO,
            storage_backend=StorageBackend.LOCAL,
            object_key=f"test/{uuid4()}.mp4",
            content_type="video/mp4",
            size_bytes=1,
            sha256="0" * 64,
        )
        db.add(asset)
        db.flush()
        render = Task(
            owner_id=owner.id,
            video_id=video.id,
            kind=TaskKind.RENDER,
            status=TaskStatus.SUCCEEDED,
            idempotency_key=str(uuid4()),
        )
        qc = Task(
            owner_id=owner.id,
            video_id=video.id,
            kind=TaskKind.QUALITY,
            status=TaskStatus.SUCCEEDED,
            idempotency_key=str(uuid4()),
        )
        db.add(render)
        db.add(qc)
        db.flush()
        now = datetime.now(UTC)
        db.add(
            QualityCheck(
                video_id=video.id,
                render_task_id=render.id,
                task_id=qc.id,
                final_asset_id=asset.id,
                attempt=1,
                status=QualityStatus.PASSED,
                created_at=now,
                completed_at=now,
            )
        )
        video.status = VideoStatus.READY
        db.add(video)
        db.commit()
    return headers, path, UUID(video_id)


def test_concurrent_revision_creation_production_and_recovery(
    pg_client, postgres_engine, story_factory
):
    headers, path, video_id = prepare(pg_client, postgres_engine, story_factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda _: pg_client.post(path + "/revisions", headers=headers), range(2))
        )
    assert all(r.status_code == 201 for r in responses)
    identifier = responses[0].json()["id"]
    assert responses[1].json()["id"] == identifier
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: pg_client.post(
                    path + f"/revisions/{identifier}/produce", headers=headers
                ),
                range(2),
            )
        )
    assert all(r.status_code == 202 for r in responses)
    with Session(postgres_engine) as db:
        assert (
            len(db.exec(select(VideoRevision).where(VideoRevision.video_id == video_id)).all()) == 2
        )
        task = db.exec(
            select(Task).where(Task.video_id == video_id, Task.kind == TaskKind.DIRECT)
        ).one()
        task_id = task.id
        task.status = TaskStatus.NEEDS_REVIEW
        db.add(task)
        db.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: pg_client.post(
                    f"/api/v1/tasks/{task_id}/recover",
                    headers=headers,
                    json={"note": "Resume local director"},
                ),
                range(2),
            )
        )
    assert sorted(r.status_code for r in responses) == [202, 409]
    with Session(postgres_engine) as db:
        assert len(db.exec(select(TaskRecovery).where(TaskRecovery.task_id == task_id)).all()) == 1
        assert db.get(Task, task_id).status == TaskStatus.QUEUED


def test_revision_submit_rolls_back_scenes_and_status_on_outbox_failure(
    pg_client, postgres_engine, story_factory
):
    headers, path, video_id = prepare(pg_client, postgres_engine, story_factory)
    draft = pg_client.post(path + "/revisions", headers=headers).json()
    scene = draft["scenes"][0]
    rp = path + "/revisions/" + draft["id"]
    pg_client.patch(
        rp + "/scenes/" + scene["id"], headers=headers, json={"narration": "Changed."}
    ).raise_for_status()

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO tasks"):
            raise SQLAlchemyError("Simulated durable outbox failure")

    event.listen(postgres_engine, "before_cursor_execute", fail)
    try:
        assert pg_client.post(rp + "/produce", headers=headers).status_code == 503
    finally:
        event.remove(postgres_engine, "before_cursor_execute", fail)
    with Session(postgres_engine) as db:
        assert db.get(Scene, UUID(scene["id"])).narration == scene["narration"]
        assert db.get(Video, video_id).status == VideoStatus.READY
        assert db.get(VideoRevision, UUID(draft["id"])).status == RevisionStatus.DRAFT
        assert not db.exec(select(Task).where(Task.kind == TaskKind.DIRECT)).all()
