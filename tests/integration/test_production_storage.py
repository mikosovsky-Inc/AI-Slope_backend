from concurrent.futures import ThreadPoolExecutor

from sqlmodel import Session, select

from app.demo import seed_demo
from app.modules.assets.models import Asset, AssetType, StorageBackend
from app.modules.production.service import advance_production
from app.modules.tasks.models import Task, TaskKind, TaskStatus
from app.modules.videos.models import Video, VideoStatus
from app.workers.runner import run_task


def test_concurrent_seed_and_last_audio_completions(
    pg_client, postgres_engine, settings, monkeypatch
):
    email = "production@example.com"
    pg_client.post(
        "/api/v1/auth/register", json={"email": email, "password": "long-test-password"}
    ).raise_for_status()

    def seed(_):
        with Session(postgres_engine) as db:
            return seed_demo(db, settings, email, generate=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(seed, range(2)))
    assert results[0] == results[1]
    video_id = results[0].items[1].video_id
    with Session(postgres_engine) as db:
        root = db.exec(select(Task.id).where(Task.video_id == video_id)).one()
    run_task(postgres_engine, str(root), settings=settings)
    with Session(postgres_engine) as db:
        director = db.exec(
            select(Task.id).where(Task.video_id == video_id, Task.kind == TaskKind.DIRECT)
        ).one()
    run_task(postgres_engine, str(director), settings=settings)
    # Fan-in only needs persisted, scoped assets; media bytes are checked in the full demo test.
    with Session(postgres_engine) as db:
        visuals = db.exec(
            select(Task).where(
                Task.video_id == video_id, Task.kind.in_([TaskKind.IMAGE, TaskKind.VIDEO])
            )
        ).all()
        for task in visuals:
            asset = Asset(
                video_id=video_id,
                scene_id=task.scene_id,
                type=AssetType(task.kind.value),
                storage_backend=StorageBackend.LOCAL,
                object_key=f"test/{task.id}",
                content_type="image/png",
                size_bytes=1,
                sha256="0" * 64,
            )
            db.add(asset)
            db.flush()
            task.status, task.result = TaskStatus.SUCCEEDED, {"asset_id": str(asset.id)}
            db.add(task)
        db.flush()
        advance_production(db, visuals[-1])
        db.commit()
        audio = db.exec(
            select(Task).where(Task.video_id == video_id, Task.kind == TaskKind.AUDIO)
        ).all()
        results_by_id = {}
        for index, task in enumerate(audio):
            asset = Asset(
                video_id=video_id,
                scene_id=task.scene_id,
                type=AssetType.AUDIO,
                storage_backend=StorageBackend.LOCAL,
                object_key=f"test/{task.id}",
                content_type="audio/wav",
                size_bytes=1,
                sha256="0" * 64,
            )
            db.add(asset)
            db.flush()
            results_by_id[task.id] = {"id": str(asset.id)}
            if index < len(audio) - 2:
                task.status, task.result = TaskStatus.SUCCEEDED, results_by_id[task.id]
                db.add(task)
        ids = [str(t.id) for t in audio[-2:]]
        db.commit()
    monkeypatch.setattr(
        "app.workers.runner.execute_operation", lambda db, task, config: results_by_id[task.id]
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda identifier: run_task(postgres_engine, identifier, settings=settings), ids
            )
        )
    with Session(postgres_engine) as db:
        assert db.get(Video, video_id).status == VideoStatus.READY_TO_RENDER
        render = db.exec(
            select(Task).where(Task.video_id == video_id, Task.kind == TaskKind.RENDER)
        ).one()
        assert len(render.parameters["input_manifest"]["scenes"]) == len(audio)
        assert all(
            db.get(Task, task_id).status == TaskStatus.SUCCEEDED for task_id in results_by_id
        )
