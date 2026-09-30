from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlmodel import select

from app.demo import seed_demo
from app.modules.assets.models import Asset, AssetType, StorageBackend
from app.modules.quality.models import QualityCheck, QualityStatus
from app.modules.revisions.models import TaskRecovery
from app.modules.tasks.models import Task, TaskKind, TaskStatus
from app.modules.videos.models import Scene, Video, VideoStatus
from app.workers.runner import run_task


def drain(db, settings):
    for _ in range(100):
        db.expire_all()
        tasks = db.exec(select(Task).where(Task.status == TaskStatus.QUEUED)).all()
        if not tasks:
            return
        ids = [t.id for t in tasks]
        for task in tasks:
            task.available_at = datetime.now(UTC)
            db.add(task)
        db.commit()
        for identifier in ids:
            run_task(db.get_bind(), str(identifier), settings=settings)
    raise AssertionError("Pipeline did not settle")


def test_full_revision_preserves_original_and_reuses_healthy_assets(client, db, settings, tmp_path):
    credentials = {"email": "revisions@example.com", "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": "Bearer " + token}
    settings.storage_local_root = tmp_path
    demo = seed_demo(db, settings, credentials["email"], generate=True)
    drain(db, settings)
    path = f"/api/v1/videos/{demo.items[1].video_id}/revisions"
    draft = client.post(path, headers=headers).json()
    assert draft["number"] == 2
    assert client.post(path, headers=headers).json()["id"] == draft["id"]
    baseline = client.get(path, headers=headers).json()[0]
    old_asset = db.get(Asset, UUID(baseline["final_asset_id"]))
    old_bytes = (tmp_path / old_asset.object_key).read_bytes()
    scene = draft["scenes"][0]
    edit = {"narration": "Changed.", "visual_prompt": "A different dark forest"}
    rp = path + "/" + draft["id"]
    client.patch(rp + "/scenes/" + scene["id"], headers=headers, json=edit).raise_for_status()
    assert db.get(Scene, UUID(scene["id"])).narration == scene["narration"]
    assert client.post(rp + "/produce", headers=headers).status_code == 202
    before = len(db.exec(select(Task)).all())
    assert client.post(rp + "/produce", headers=headers).status_code == 202
    assert len(db.exec(select(Task)).all()) == before
    assert (
        client.patch(rp + "/scenes/" + scene["id"], headers=headers, json=edit).status_code == 409
    )
    drain(db, settings)
    tasks = db.exec(select(Task)).all()
    assert all(t.status == TaskStatus.SUCCEEDED for t in tasks), [
        (t.kind, t.error) for t in tasks if t.status != TaskStatus.SUCCEEDED
    ]
    versions = client.get(path, headers=headers).json()
    assert versions[0] == baseline
    assert versions[1]["status"] == "ready"
    assert versions[1]["final_asset_id"] != baseline["final_asset_id"]
    assert versions[1]["scenes"][0]["narration"] == "Changed."
    assert (tmp_path / old_asset.object_key).read_bytes() == old_bytes
    assert sum(bool(t.parameters.get("reuse_asset_id")) for t in tasks) == 8
    # A subsequent draft uses the latest successful version as its baseline.
    next_draft = client.post(path, headers=headers).json()
    assert next_draft["number"] == 3
    assert next_draft["base_render_id"] == versions[1]["render_task_id"]


def ready_fixture(client, db, story_factory):
    headers, path, video_id, _ = story_factory(client)
    client.post(path + "/script/generate", headers=headers).raise_for_status()
    client.post(path + "/direct", headers=headers).raise_for_status()
    video = db.get(Video, UUID(video_id))
    from app.models.user import User

    owner = db.exec(select(User)).one()
    final = Asset(
        video_id=video.id,
        type=AssetType.FINAL_VIDEO,
        storage_backend=StorageBackend.LOCAL,
        object_key=f"test/{uuid4()}.mp4",
        content_type="video/mp4",
        size_bytes=1,
        sha256="0" * 64,
    )
    db.add(final)
    db.flush()
    render = Task(
        owner_id=owner.id,
        video_id=video.id,
        kind=TaskKind.RENDER,
        status=TaskStatus.SUCCEEDED,
        idempotency_key=str(uuid4()),
        result={"asset_id": str(final.id)},
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
            final_asset_id=final.id,
            status=QualityStatus.PASSED,
            attempt=1,
            created_at=now,
            completed_at=now,
        )
    )
    video.status = VideoStatus.READY
    db.add(video)
    db.commit()
    return headers, path, video


def test_revision_auth_validation_and_cancel(client, db, story_factory):
    headers, path, video = ready_fixture(client, db, story_factory)
    rp = path + "/revisions"
    assert client.post(rp).status_code == 401
    draft = client.post(rp, headers=headers).json()
    sp = rp + "/" + draft["id"] + "/scenes/" + draft["scenes"][0]["id"]
    assert client.patch(sp, headers=headers, json={"camera_motion": "invalid"}).status_code == 422
    assert client.patch(sp, headers=headers, json={}).status_code == 422
    assert client.patch(sp, headers=headers, json={"narration": None}).status_code == 422
    credentials = {"email": "other@example.com", "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    other = {
        "Authorization": "Bearer "
        + client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    }
    assert client.get(rp, headers=other).status_code == 404
    assert client.patch(sp, headers=other, json={"narration": "Changed."}).status_code == 404
    video.format = "top5"
    db.add(video)
    db.commit()
    assert client.patch(sp, headers=headers, json={"narration": "Invented fact"}).status_code == 409
    assert (
        client.post(rp + "/" + draft["id"] + "/cancel", headers=headers).json()["status"]
        == "cancelled"
    )
    assert client.post(rp + "/" + draft["id"] + "/produce", headers=headers).status_code == 409


def test_recover_committed_script_is_audited_without_provider_call(
    client, db, settings, story_factory, monkeypatch
):
    headers, path, _, _ = story_factory(client)
    settings.tasks_eager = False
    response = client.post(path + "/script/generate", headers=headers)
    identifier = response.json()["id"]
    run_task(db.get_bind(), identifier, settings=settings)
    db.expire_all()
    task = db.get(Task, UUID(identifier))
    task.status = TaskStatus.NEEDS_REVIEW
    db.add(task)
    db.commit()
    rp = "/api/v1/tasks/" + identifier
    assert client.post(rp + "/recover", json={"note": "Saved script"}).status_code == 401
    response = client.post(rp + "/recover", headers=headers, json={"note": "Verified saved script"})
    assert response.status_code == 202
    monkeypatch.setattr(
        "app.workers.operations.create_llm_provider",
        lambda _: (_ for _ in ()).throw(AssertionError("Must not call provider")),
    )
    run_task(db.get_bind(), identifier, settings=settings)
    db.expire_all()
    assert db.get(Task, task.id).status == TaskStatus.SUCCEEDED
    history = client.get(rp + "/recoveries", headers=headers).json()
    assert len(history) == 1
    assert history[0]["note"] == "Verified saved script"


def test_unknown_visual_requires_evidence_and_abandon_cannot_retry(
    client, db, settings, story_factory
):
    headers, path, _, _ = story_factory(client)
    client.post(path + "/script/generate", headers=headers).raise_for_status()
    scene = db.exec(select(Scene)).first()
    settings.tasks_eager = False
    task_id = client.post(
        path + "/visuals/image", headers=headers, json={"scene_id": str(scene.id)}
    ).json()["id"]
    task = db.get(Task, UUID(task_id))
    task.status, task.checkpoint = TaskStatus.NEEDS_REVIEW, {"submit_started": True}
    db.add(task)
    db.commit()
    rp = "/api/v1/tasks/" + task_id + "/recover"
    assert client.post(rp, headers=headers, json={"note": "Unknown outcome"}).status_code == 409
    assert not db.exec(select(TaskRecovery)).all()
    assert (
        client.post(
            rp,
            headers=headers,
            json={"action": "use_asset", "note": "Invalid", "asset_id": str(uuid4())},
        ).status_code
        == 409
    )
    response = client.post(
        rp,
        headers=headers,
        json={"action": "abandon", "note": "Stop tracking; keep cost reservation"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "failed"
    assert (
        client.post(path + "/retry", headers=headers, json={"task_id": task_id}).status_code == 409
    )
    assert client.post(rp, headers=headers, json={"note": "Try again"}).status_code == 409


def test_cancel_failed_revision_restores_scenes_and_blocks_old_work(client, db, story_factory):
    headers, path, video = ready_fixture(client, db, story_factory)
    rp = path + "/revisions"
    draft = client.post(rp, headers=headers).json()
    scene = draft["scenes"][0]
    dp = rp + "/" + draft["id"]
    client.patch(
        dp + "/scenes/" + scene["id"], headers=headers, json={"narration": "Changed."}
    ).raise_for_status()
    client.post(dp + "/produce", headers=headers).raise_for_status()
    assert client.post(dp + "/cancel", headers=headers).status_code == 409
    task = db.exec(select(Task).where(Task.kind == TaskKind.DIRECT)).one()
    task.status = TaskStatus.NEEDS_REVIEW
    db.add(task)
    db.commit()
    assert client.post(dp + "/cancel", headers=headers).status_code == 409
    client.post(
        f"/api/v1/tasks/{task.id}/recover",
        headers=headers,
        json={"action": "abandon", "note": "Cancel this production"},
    ).raise_for_status()
    assert client.post(dp + "/cancel", headers=headers).json()["status"] == "cancelled"
    db.expire_all()
    assert db.get(Video, video.id).status == VideoStatus.READY
    assert db.get(Scene, UUID(scene["id"])).narration == scene["narration"]
    assert (
        client.post(path + "/retry", headers=headers, json={"task_id": str(task.id)}).status_code
        == 409
    )
    next_draft = client.post(rp, headers=headers).json()
    assert next_draft["base_render_id"] == draft["base_render_id"]


@pytest.mark.parametrize("kind", [TaskKind.AUDIO, TaskKind.IMAGE])
def test_recover_asset_survives_worker_restart_without_provider_call(
    client, db, settings, story_factory, monkeypatch, kind
):
    headers, path, video_id, _ = story_factory(client)
    client.post(path + "/script/generate", headers=headers).raise_for_status()
    scene = db.exec(select(Scene)).first()
    settings.tasks_eager = False
    identifier = client.post(
        path + ("/audio" if kind == TaskKind.AUDIO else "/visuals/image"),
        headers=headers,
        json={"scene_id": str(scene.id)},
    ).json()["id"]
    task = db.get(Task, UUID(identifier))
    task.status = TaskStatus.NEEDS_REVIEW
    db.add(task)
    asset = Asset(
        video_id=UUID(video_id),
        scene_id=scene.id,
        type=AssetType(kind.value),
        storage_backend=StorageBackend.LOCAL,
        object_key=f"test/{uuid4()}",
        content_type="audio/wav" if kind == TaskKind.AUDIO else "image/png",
        size_bytes=1,
        sha256="0" * 64,
    )
    db.add(asset)
    db.commit()
    rp = f"/api/v1/tasks/{identifier}/recover"
    assert client.post(rp, headers=headers, json={"note": "Unknown TTS outcome"}).status_code == 409
    response = client.post(
        rp,
        headers=headers,
        json={"action": "use_asset", "note": "Verified recovered audio", "asset_id": str(asset.id)},
    )
    assert response.status_code == 202
    task.status = TaskStatus.RUNNING
    db.add(task)
    db.commit()
    monkeypatch.setattr(
        "app.workers.operations.create_tts_provider",
        lambda _: (_ for _ in ()).throw(AssertionError("No TTS call allowed")),
    )
    monkeypatch.setattr(
        "app.workers.operations.create_generation_provider",
        lambda _: (_ for _ in ()).throw(AssertionError("No generation call allowed")),
    )
    run_task(db.get_bind(), identifier, settings=settings)
    db.expire_all()
    assert db.get(Task, UUID(identifier)).result["asset_id"] == str(asset.id)
    assert db.get(Task, UUID(identifier)).status == TaskStatus.SUCCEEDED


def test_resume_known_provider_job_polls_without_submission(
    client, db, settings, story_factory, monkeypatch
):
    from app.shared.generation import GenerationJobRef, GenerationResult, ProviderJobStatus

    headers, path, video_id, _ = story_factory(client)
    client.post(path + "/script/generate", headers=headers).raise_for_status()
    scene = db.exec(select(Scene)).first()
    settings.tasks_eager = False
    identifier = client.post(
        path + "/visuals/image", headers=headers, json={"scene_id": str(scene.id)}
    ).json()["id"]
    task = db.get(Task, UUID(identifier))
    ref = GenerationJobRef(provider="runpod", endpoint_id="original-endpoint", job_id="known-job")
    task.status, task.checkpoint = (
        TaskStatus.NEEDS_REVIEW,
        {"submit_started": True, "provider_job": ref.model_dump(mode="json")},
    )
    db.add(task)
    asset = Asset(
        video_id=UUID(video_id),
        scene_id=scene.id,
        type=AssetType.IMAGE,
        storage_backend=StorageBackend.LOCAL,
        object_key=f"test/{uuid4()}.png",
        content_type="image/png",
        size_bytes=1,
        sha256="0" * 64,
    )
    db.add(asset)
    db.commit()
    response = client.post(
        f"/api/v1/tasks/{identifier}/recover", headers=headers, json={"note": "Poll existing job"}
    )
    assert response.status_code == 202
    calls = []

    class Provider:
        def get_status(self, job):
            assert job == ref
            calls.append(job)
            return GenerationResult(
                job=job, status=ProviderJobStatus.SUCCEEDED, output={"test": True}
            )

        def close(self):
            pass

    settings.external_providers_mode = "live"
    monkeypatch.setattr("app.workers.operations.create_generation_provider", lambda _: Provider())
    monkeypatch.setattr("app.modules.render.visuals.materialize_visual", lambda *args: asset)
    run_task(db.get_bind(), identifier, settings=settings)
    db.expire_all()
    assert db.get(Task, UUID(identifier)).status == TaskStatus.SUCCEEDED
    assert len(calls) == 1
