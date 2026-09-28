from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlmodel import select

from app.integrations.storage.factory import create_storage_provider
from app.modules.assets.models import Asset, AssetType, GenerationJob
from app.modules.quality.models import QualityCheck, QualityStatus
from app.modules.quality.provider import VisualQualityResult
from app.modules.render.engine import FFmpegRenderer
from app.modules.tasks.models import Task, TaskStatus
from app.modules.videos.models import Scene, Video, VideoScript, VideoStatus, VideoStatusEvent
from app.shared.storage import StorageError
from app.workers.runner import run_task


def run(db, settings, identifier):
    run_task(db.get_bind(), str(identifier), settings=settings)
    db.expire_all()
    return db.get(Task, UUID(str(identifier)))


def due(db, task):
    task.available_at = datetime.now(UTC) - timedelta(seconds=1)
    db.add(task)
    db.commit()


@pytest.fixture
def rendered(client, db, settings, story_factory, tmp_path):
    settings.storage_local_root = tmp_path / "assets"
    headers, path, video_id, _ = story_factory(client)
    client.post(path + "/script/generate", headers=headers).raise_for_status()
    for scene in db.exec(select(Scene)).all():
        scene.duration, scene.narration, scene.camera_motion = 2, "Test", "static"
        db.add(scene)
    video = db.get(Video, UUID(video_id))
    video.duration_target = 10
    db.add(video)
    script = db.exec(select(VideoScript)).one()
    script.duration_target = 10
    db.add(script)
    db.commit()
    settings.tasks_eager = False
    for scene in db.exec(select(Scene).order_by(Scene.position)).all():
        for suffix in ("visuals/image", "audio"):
            response = client.post(
                path + "/" + suffix, headers=headers, json={"scene_id": str(scene.id)}
            )
            response.raise_for_status()
            assert run(db, settings, response.json()["id"]).status == TaskStatus.SUCCEEDED
    response = client.post(path + "/render", headers=headers, json={})
    render = run(db, settings, response.json()["id"])
    assert render.status == TaskStatus.SUCCEEDED
    quality = db.get(Task, UUID(render.result["quality_task_id"]))
    final = db.get(Asset, UUID(render.result["asset_id"]))
    return headers, path, video, render, quality, final


def test_quality_happy_path_reports_auth_and_recovery(client, db, settings, rendered):
    headers, path, video, render, task, final = rendered
    assert client.post(path + "/quality-check").status_code == 401
    assert client.get(path + "/quality-checks").status_code == 401
    submitted = client.post(path + "/quality-check", headers=headers)
    assert submitted.status_code == 202
    assert submitted.json()["id"] == str(task.id)
    task = run(db, settings, task.id)
    assert task.status == TaskStatus.SUCCEEDED, task.error
    assert task.result["status"] == "passed"
    assert video.status == VideoStatus.READY
    report = client.get(path + "/quality-checks", headers=headers).json()[0]
    assert report["final_asset_id"] == str(final.id)
    checks = report["report_json"]["checks"]
    assert not [c for c in checks if c["outcome"] == "failed"]
    assert len([c for c in checks if c["outcome"] == "skipped"]) == 5
    task.status, task.result = TaskStatus.RUNNING, None
    db.add(task)
    db.commit()
    assert run(db, settings, task.id).result["status"] == "passed"
    assert len(db.exec(select(QualityCheck)).all()) == 1
    assert (
        len(
            db.exec(
                select(VideoStatusEvent).where(VideoStatusEvent.to_status == VideoStatus.READY)
            ).all()
        )
        == 1
    )
    credentials = {"email": "quality-other@example.com", "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    assert client.get(path + "/quality-checks", headers=other).status_code == 404
    assert client.post(path + "/quality-check", headers=other).status_code == 404


def replace_final(db, settings, asset, args, tmp_path):
    replacement = tmp_path / "replacement.mp4"
    FFmpegRenderer(settings).run([*args, str(replacement)])
    storage = create_storage_provider(settings)
    try:
        with replacement.open("rb") as source:
            result = storage.put(asset.object_key, source, content_type="video/mp4")
        asset.sha256, asset.size_bytes = result.sha256, result.size_bytes
        db.add(asset)
        db.commit()
    finally:
        storage.close()


@pytest.mark.parametrize(
    "fault,code",
    [
        ("missing_final", "asset_integrity"),
        ("empty_final", "asset_integrity"),
        ("resolution", "resolution"),
        ("duration", "duration_target"),
        ("no_audio", "audio_stream"),
        ("missing_scene", "required_scenes"),
        ("empty_audio", "asset_integrity"),
        ("invalid_scene_media", "asset_integrity"),
    ],
)
def test_technical_failures_never_ready(client, db, settings, rendered, tmp_path, fault, code):
    _, _, video, render, task, final = rendered
    settings.quality_max_scene_retries = 0
    path = settings.storage_local_root / final.object_key
    if fault == "missing_final":
        path.unlink()
    elif fault == "empty_final":
        path.write_bytes(b"")
    elif fault == "resolution":
        replace_final(
            db,
            settings,
            final,
            ["-i", str(path), "-vf", "scale=180:320", "-c:v", "libx264", "-c:a", "copy"],
            tmp_path,
        )
    elif fault == "no_audio":
        replace_final(db, settings, final, ["-i", str(path), "-an", "-c:v", "copy"], tmp_path)
    elif fault == "duration":
        video.duration_target = 15
        db.add(video)
    elif fault == "missing_scene":
        manifest = dict(render.checkpoint["render_manifest"])
        manifest["scenes"] = manifest["scenes"][:-1]
        render.checkpoint = {"render_manifest": manifest}
        db.add(render)
    else:
        field = "audio_asset_id" if fault == "empty_audio" else "visual_asset_id"
        asset_id = render.checkpoint["render_manifest"]["scenes"][0][field]
        asset = db.get(Asset, UUID(asset_id))
        source = settings.storage_local_root / asset.object_key
        source.write_bytes(b"" if fault == "empty_audio" else b"invalid image")
        # Keep the digest valid to exercise ffprobe rather than hashing.
        if fault == "invalid_scene_media":
            import hashlib

            asset.sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
            asset.size_bytes = source.stat().st_size
            db.add(asset)
    db.commit()
    task = run(db, settings, task.id)
    assert task.status == TaskStatus.SUCCEEDED, task.error
    check = db.exec(select(QualityCheck)).one()
    assert check.status == QualityStatus.FAILED
    assert video.status == VideoStatus.FAILED
    assert any(c["code"] == code and c["outcome"] == "failed" for c in check.report_json["checks"])
    assert not check.repair_tasks


@pytest.mark.parametrize("kind,field", [("image", "visual_asset_id"), ("audio", "audio_asset_id")])
def test_only_broken_scene_asset_regenerated_then_ready(db, settings, rendered, kind, field):
    _, _, video, render, task, _ = rendered
    original = render.checkpoint["render_manifest"]
    bad_id = UUID(original["scenes"][2][field])
    bad = db.get(Asset, bad_id)
    (settings.storage_local_root / bad.object_key).unlink()
    task = run(db, settings, task.id)
    assert task.status == TaskStatus.QUEUED
    check = db.exec(select(QualityCheck)).one()
    assert check.status == QualityStatus.REPAIRING
    assert len(check.repair_tasks) == 1
    child_id = UUID(check.repair_tasks[0]["task_id"])
    child = db.get(Task, child_id)
    assert child.kind.value == kind and child.scene_id == bad.scene_id
    assert video.status == VideoStatus.QUALITY_CHECK
    # A crash/poll must reuse the repair record and never submit more paid work.
    task.status = TaskStatus.RUNNING
    db.add(task)
    db.commit()
    run(db, settings, task.id)
    assert db.exec(select(QualityCheck)).one().repair_tasks == check.repair_tasks
    assert run(db, settings, child_id).status == TaskStatus.SUCCEEDED
    due(db, task)
    task = run(db, settings, task.id)
    assert task.status == TaskStatus.SUCCEEDED
    check = db.get(QualityCheck, check.id)
    rerender = db.get(Task, check.rerender_task_id)
    inputs = rerender.parameters["input_manifest"]
    for index, scene in enumerate(original["scenes"]):
        if index == 2:
            assert inputs["scenes"][index][field] != scene[field]
            assert {k: v for k, v in inputs["scenes"][index].items() if k != field} == {
                k: v for k, v in scene.items() if k != field
            }
        else:
            assert inputs["scenes"][index] == scene
    rerender = run(db, settings, rerender.id)
    assert rerender.status == TaskStatus.SUCCEEDED
    after = run(db, settings, rerender.result["quality_task_id"])
    assert after.result["status"] == "passed"
    assert video.status == VideoStatus.READY
    assert len(db.exec(select(Asset).where(Asset.type == AssetType.FINAL_VIDEO)).all()) == 2
    if kind == "audio":
        assert (
            len(db.exec(select(GenerationJob).where(GenerationJob.scene_id == bad.scene_id)).all())
            == 2
        )
        before = len(db.exec(select(GenerationJob)).all())
        run(db, settings, child_id)
        assert len(db.exec(select(GenerationJob)).all()) == before


def test_vision_extension_and_scene_retry_limit(db, settings, rendered, monkeypatch):
    _, _, video, render, task, _ = rendered
    settings.quality_max_scene_retries = 1
    scene_id = UUID(render.checkpoint["render_manifest"]["scenes"][0]["scene_id"])
    frames_seen = []

    class Vision:
        name, requires_frames = "test-vision", True

        def check(self, request):
            assert request.frames and request.frames[0].read_bytes().startswith(b"\x89PNG")
            frames_seen.append(request.scene_id)
            return VisualQualityResult(
                outcome="failed" if request.scene_id == scene_id else "passed"
            )

        def close(self):
            pass

    monkeypatch.setattr("app.modules.quality.provider.create_visual_quality_provider", Vision)
    run(db, settings, task.id)
    first = db.exec(select(QualityCheck)).one()
    assert len(first.repair_tasks) == 1
    run(db, settings, first.repair_tasks[0]["task_id"])
    due(db, task)
    run(db, settings, task.id)
    rerender = run(db, settings, first.rerender_task_id)
    result = run(db, settings, rerender.result["quality_task_id"])
    assert result.result["status"] == "failed"
    assert result.result["error"] == "scene_retry_limit"
    assert not result.result["repair_tasks"]
    assert video.status == VideoStatus.FAILED
    assert len(frames_seen) == 10


@pytest.mark.parametrize("fault", ["storage", "vision", "ffmpeg"])
def test_infrastructure_outage_does_not_regenerate_paid_assets(
    db, settings, rendered, monkeypatch, fault
):
    _, _, video, _, task, _ = rendered
    before = len(db.exec(select(Task)).all())

    class Offline:
        def download(self, key, target):
            raise StorageError("Temporary outage")

        def close(self):
            pass

    if fault == "storage":
        monkeypatch.setattr("app.workers.operations.create_storage_provider", lambda _: Offline())
    elif fault == "ffmpeg":
        settings.ffmpeg_binary = "/missing-quality-test/ffmpeg"
    else:
        from app.modules.quality.provider import VisualQualityUnavailable

        class OfflineVision:
            name, requires_frames = "offline", False

            def check(self, request):
                raise VisualQualityUnavailable

            def close(self):
                pass

        monkeypatch.setattr(
            "app.modules.quality.provider.create_visual_quality_provider", OfflineVision
        )
    for _ in range(3):
        due(db, task)
        task = run(db, settings, task.id)
    assert task.status == TaskStatus.NEEDS_REVIEW
    assert video.status == VideoStatus.QUALITY_CHECK
    assert len(db.exec(select(Task)).all()) == before
    assert not db.exec(select(QualityCheck)).one().repair_tasks


@pytest.mark.parametrize("fault", ["failed", "timeout"])
def test_failed_scene_repair_stops_without_rerender(db, settings, rendered, fault):
    _, _, video, render, task, _ = rendered
    asset = db.get(
        Asset, UUID(render.checkpoint["render_manifest"]["scenes"][0]["visual_asset_id"])
    )
    (settings.storage_local_root / asset.object_key).unlink()
    run(db, settings, task.id)
    check = db.exec(select(QualityCheck)).one()
    child = db.get(Task, UUID(check.repair_tasks[0]["task_id"]))
    if fault == "failed":
        child.status = TaskStatus.NEEDS_REVIEW
        db.add(child)
    else:
        check.created_at = datetime.now(UTC) - timedelta(hours=2)
        db.add(check)
    db.commit()
    due(db, task)
    run(db, settings, task.id)
    assert check.status == QualityStatus.FAILED
    assert check.error == ("scene_repair_failed" if fault == "failed" else "scene_repair_timeout")
    if fault == "timeout":
        # The stale queued child is rejected before opening any generation provider.
        assert run(db, settings, child.id).status == TaskStatus.FAILED
        assert not child.checkpoint
    assert check.rerender_task_id is None
    assert video.status == VideoStatus.FAILED


def test_requires_completed_render_and_ownership(client, story_factory):
    headers, path, _, _ = story_factory(client)
    assert client.post(path + "/quality-check", headers=headers).status_code == 409
    assert client.get(path + "/quality-checks", headers=headers).json() == []
    assert (
        client.get(f"/api/v1/videos/{uuid4()}/quality-checks", headers=headers).status_code == 404
    )
