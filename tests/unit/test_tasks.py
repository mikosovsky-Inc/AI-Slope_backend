from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlmodel import select

from app.modules.tasks.models import Task, TaskKind, TaskStatus
from app.modules.videos.models import Scene, Video, VideoScript, VideoStatus
from app.workers.dispatcher import dispatch_once
from app.workers.runner import run_task


def test_async_story_enqueue_and_result(client, db, settings, story_factory):
    headers, path, video_id, _ = story_factory(client)
    settings.tasks_eager = False
    response = client.post(
        path + "/script/generate", headers=headers | {"Idempotency-Key": "script-1"}
    )
    assert response.status_code == 202
    task_id = response.json()["id"]
    assert db.exec(select(VideoScript)).all() == []
    assert (
        client.post(
            path + "/script/generate", headers=headers | {"Idempotency-Key": "script-1"}
        ).json()["id"]
        == task_id
    )
    run_task(db.get_bind(), task_id, settings=settings)
    db.expire_all()
    task = client.get(response.headers["location"], headers=headers).json()
    assert task["status"] == "succeeded"
    assert task["result"]["scenes"]
    run_task(db.get_bind(), task_id, settings=settings)
    db.expire_all()
    assert len(db.exec(select(VideoScript)).all()) == 1
    assert db.get(Task, UUID(task_id)).attempts == 1
    assert client.get(response.headers["location"]).status_code == 401
    assert client.get(path + "/tasks", headers=headers).json()[0]["id"] == task_id


def test_async_job_owner_isolation_and_key_conflict(client, db, settings, story_factory):
    headers, path, _, _ = story_factory(client)
    settings.tasks_eager = False
    task = client.post(
        path + "/script/generate", headers=headers | {"Idempotency-Key": "key"}
    ).json()
    assert (
        client.post(path + "/direct", headers=headers | {"Idempotency-Key": "key"}).status_code
        == 409
    )
    credentials = {"email": "other@example.com", "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    other = {"Authorization": "Bearer " + token}
    assert client.get("/api/v1/tasks/" + task["id"], headers=other).status_code == 404
    assert client.post(path + "/script/generate", headers=other).status_code == 404


def test_dispatch_failure_keeps_durable_task(client, db, settings, story_factory):
    headers, path, _, _ = story_factory(client)
    settings.tasks_eager = False
    response = client.post(path + "/script/generate", headers=headers)

    def failed_send(*args):
        raise ConnectionError("Redis unavailable")

    with pytest.raises(ConnectionError):
        dispatch_once(db.get_bind(), failed_send)
    sent = []
    assert dispatch_once(db.get_bind(), lambda queue, task: sent.append((queue, task))) == 1
    assert sent == [("content", response.json()["id"])]
    # Dispatch bookkeeping must not postpone actual execution.
    run_task(db.get_bind(), response.json()["id"], settings=settings)
    db.expire_all()
    assert db.get(Task, UUID(response.json()["id"])).status == TaskStatus.SUCCEEDED


def test_running_task_recovers_committed_script_without_call(client, db, settings, story_factory):
    headers, path, video, _ = story_factory(client)
    client.post(path + "/script/generate", headers=headers).raise_for_status()
    settings.tasks_eager = False
    task_id = client.post(path + "/script/generate", headers=headers).json()["id"]
    task = db.get(Task, UUID(task_id))
    task.status = TaskStatus.RUNNING
    task.started_at = datetime.now(UTC) - timedelta(minutes=10)
    db.add(task)
    db.commit()
    # No valid live credentials: success must come from saved script, not a provider.
    run_task(
        db.get_bind(),
        task_id,
        settings=settings.model_copy(update={"external_providers_mode": "live"}),
    )
    db.expire_all()
    assert db.get(Task, UUID(task_id)).status == TaskStatus.SUCCEEDED


def test_ambiguous_started_task_requires_review(client, db, settings, story_factory):
    headers, path, _, _ = story_factory(client)
    settings.tasks_eager = False
    task_id = client.post(path + "/script/generate", headers=headers).json()["id"]
    task = db.get(Task, UUID(task_id))
    task.status = TaskStatus.RUNNING
    db.add(task)
    db.commit()
    run_task(db.get_bind(), task_id, settings=settings)
    db.expire_all()
    assert db.get(Task, UUID(task_id)).status == TaskStatus.NEEDS_REVIEW
    assert db.exec(select(VideoScript)).all() == []


def test_async_audio_and_visual_mock(client, db, settings, story_factory, tmp_path):
    headers, path, video_id, _ = story_factory(client)
    client.post(path + "/script/generate", headers=headers).raise_for_status()
    scene = db.exec(select(Scene)).first()
    settings.tasks_eager = False
    settings.storage_local_root = tmp_path
    for suffix in ("audio", "visuals/image", "visuals/video"):
        response = client.post(
            path + "/" + suffix, headers=headers, json={"scene_id": str(scene.id)}
        )
        assert response.status_code == 202
        run_task(db.get_bind(), response.json()["id"], settings=settings)
        db.expire_all()
        task = client.get(response.headers["location"], headers=headers).json()
        assert task["status"] == "succeeded", task
    assert list(tmp_path.rglob("*.wav"))


def test_create_video_atomic_workflow(client, db, settings, story_factory):
    headers, _, _, channel = story_factory(client)
    ideas = client.get(channel + "/ideas", headers=headers).json()["items"]
    idea = next(i for i in ideas if i["format"] == "story" and i["status"] == "candidate")
    idea_path = "/api/v1/ideas/" + idea["id"]
    client.post(idea_path + "/approve", headers=headers).raise_for_status()
    settings.tasks_eager = False
    response = client.post(idea_path + "/create-video", headers=headers)
    assert response.status_code == 202
    video = UUID(response.json()["id"])
    task = db.exec(select(Task).where(Task.video_id == video)).one()
    assert task.kind == TaskKind.STORY
    run_task(db.get_bind(), str(task.id), settings=settings)
    db.expire_all()
    director = db.exec(
        select(Task).where(Task.video_id == video, Task.kind == TaskKind.DIRECT)
    ).one()
    run_task(db.get_bind(), str(director.id), settings=settings)
    db.expire_all()
    assert db.get(Video, video).status == VideoStatus.GENERATING_ASSETS
    assert db.get(Task, director.id).status == TaskStatus.SUCCEEDED
    visuals = db.exec(
        select(Task).where(Task.video_id == video, Task.kind.in_([TaskKind.IMAGE, TaskKind.VIDEO]))
    ).all()
    scenes = db.exec(select(Scene).join(VideoScript).where(VideoScript.video_id == video)).all()
    assert {t.scene_id for t in visuals} == {s.id for s in scenes}
    assert all(t.status == TaskStatus.QUEUED for t in visuals)
    assert client.post(idea_path + "/create-video", headers=headers).status_code == 200
    assert len(db.exec(select(Task).where(Task.video_id == video)).all()) == 2 + len(scenes)


def test_queued_task_rejects_foreign_scene(client, db, story_factory):
    headers, path, _, _ = story_factory(client)
    assert (
        client.post(path + "/audio", headers=headers, json={"scene_id": str(uuid4())}).status_code
        == 404
    )


def test_visual_poll_checkpoint_and_backoff(
    client, db, settings, story_factory, monkeypatch, tmp_path
):
    from app.shared.generation import GenerationJobRef, GenerationResult, GenerationUnavailable

    headers, path, _, _ = story_factory(client)
    client.post(path + "/script/generate", headers=headers).raise_for_status()
    scene = db.exec(select(Scene)).first()
    settings.tasks_eager = False
    settings.external_providers_mode = "live"
    settings.storage_local_root = tmp_path / "assets"
    from app.integrations.storage.factory import create_storage_provider
    from app.modules.render.engine import FFmpegRenderer

    image = tmp_path / "image.png"
    FFmpegRenderer(settings).run(
        ["-f", "lavfi", "-i", "color=s=32x32", "-frames:v", "1", str(image)]
    )
    calls = {"submit": 0, "poll": 0}
    output = {}
    reference = GenerationJobRef(provider="runpod", endpoint_id="ep", job_id="remote")

    class Provider:
        def generate_image(self, request):
            calls["submit"] += 1
            key = request.parameters["output_key"]
            storage = create_storage_provider(settings)
            with image.open("rb") as source:
                storage.put(key, source, content_type="image/png")
            storage.close()
            output.update(object_key=key, content_type="image/png")
            return GenerationResult(job=reference, status="queued")

        def get_status(self, job):
            assert job == reference
            calls["poll"] += 1
            if calls["poll"] == 1:
                raise GenerationUnavailable("temporary")
            return GenerationResult(job=reference, status="succeeded", output=output)

        def close(self):
            pass

    monkeypatch.setattr("app.workers.operations.create_generation_provider", lambda _: Provider())
    task_id = client.post(
        path + "/visuals/image", headers=headers, json={"scene_id": str(scene.id)}
    ).json()["id"]
    run_task(db.get_bind(), task_id, settings=settings)
    db.expire_all()
    task = db.get(Task, UUID(task_id))
    assert task.status == TaskStatus.QUEUED
    assert task.checkpoint["provider_job"]["job_id"] == "remote"
    for _ in range(2):
        task.available_at = datetime.now(UTC) - timedelta(seconds=1)
        db.add(task)
        db.commit()
        run_task(db.get_bind(), task_id, settings=settings)
        db.expire_all()
        task = db.get(Task, UUID(task_id))
    assert task.status == TaskStatus.SUCCEEDED
    assert calls == {"submit": 1, "poll": 2}


def test_disabled_owner_task_never_runs(client, db, settings, story_factory):
    from app.models.user import User

    headers, path, _, _ = story_factory(client)
    settings.tasks_eager = False
    task_id = client.post(path + "/script/generate", headers=headers).json()["id"]
    task = db.get(Task, UUID(task_id))
    user = db.get(User, task.owner_id)
    user.is_active = False
    db.add(user)
    db.commit()
    run_task(db.get_bind(), task_id, settings=settings)
    db.expire_all()
    assert db.get(Task, UUID(task_id)).error == "owner_unavailable"
    assert db.exec(select(VideoScript)).all() == []
