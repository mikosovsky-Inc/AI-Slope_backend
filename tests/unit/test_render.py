import wave

import pytest

from app.modules.render.captions import write_captions
from app.modules.render.engine import FFmpegRenderer, RenderError, RenderScene


@pytest.fixture
def media(tmp_path, settings):
    renderer = FFmpegRenderer(settings)
    image = tmp_path / "image.png"
    renderer.run(["-f", "lavfi", "-i", "color=c=blue:s=180x320", "-frames:v", "1", str(image)])
    audio = tmp_path / "voice.wav"
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * 16000)
    return renderer, image, audio


@pytest.mark.parametrize("motion", ["zoom_in", "zoom_out", "pan_left", "pan_right", "static"])
def test_real_render_dimensions_codecs_captions_and_music(media, tmp_path, motion):
    renderer, image, audio = media
    write_captions(tmp_path / "captions.ass", [("Zażółć gęślą jaźń", 1, None)])
    scene = RenderScene(
        visual=image,
        visual_type="image/png",
        audio=audio,
        audio_type="audio/wav",
        duration=1,
        motion=motion,
        narration="Test",
    )
    result = renderer.render([scene], tmp_path, music=(audio, "audio/wav"))
    probe = renderer.probe(result, "video/mp4")
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    sound = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    assert (video["width"], video["height"], video["codec_name"], video["r_frame_rate"]) == (
        1080,
        1920,
        "h264",
        "30/1",
    )
    assert sound["codec_name"] == "aac"
    assert abs(float(probe["format"]["duration"]) - 1) < 0.1


def test_rejects_truncating_narration(media, tmp_path):
    renderer, image, audio = media
    scene = RenderScene(
        visual=image,
        visual_type="image/png",
        audio=audio,
        audio_type="audio/wav",
        duration=0.3,
        narration="Test",
    )
    with pytest.raises(RenderError, match="Narration exceeds"):
        renderer.render([scene], tmp_path)


def test_captions_escape_overrides_and_handle_empty_alignment(tmp_path):
    path = tmp_path / "captions.ass"
    write_captions(
        path,
        [
            (r"{\pos(0,0)} Tekst", 1, None),
            (
                "",
                1,
                {
                    "characters": [],
                    "character_start_times_seconds": [],
                    "character_end_times_seconds": [],
                },
            ),
        ],
    )
    content = path.read_text()
    assert r"\pos" not in content
    assert "｛" in content
    assert "0:00:01.00" in content


def test_invalid_media_is_safe(media, tmp_path):
    renderer, _, _ = media
    path = tmp_path / "bad"
    path.write_text("https://example.com/private")
    with pytest.raises(RenderError, match="Invalid media input"):
        renderer.probe(path, "video/mp4")


def prepare_video(client, db, settings, story_factory, tmp_path):
    from uuid import UUID

    from sqlmodel import select

    from app.modules.tasks.models import Task
    from app.modules.videos.models import Scene
    from app.workers.runner import run_task

    settings.storage_local_root = tmp_path / "assets"
    headers, path, video_id, _ = story_factory(client)
    client.post(path + "/script/generate", headers=headers).raise_for_status()
    scenes = db.exec(select(Scene).order_by(Scene.position)).all()
    # A short real render fixture, keeping genuine scene ownership and script associations.
    for scene in scenes:
        scene.duration = 1
        scene.narration = "Test"
        scene.camera_motion = "zoom_in"
        db.add(scene)
    db.commit()
    settings.tasks_eager = False
    for scene in scenes:
        for suffix in ("visuals/image", "audio"):
            response = client.post(
                path + "/" + suffix, headers=headers, json={"scene_id": str(scene.id)}
            )
            response.raise_for_status()
            run_task(db.get_bind(), response.json()["id"], settings=settings)
            db.expire_all()
            assert db.get(Task, UUID(response.json()["id"])).status == "succeeded"
    return headers, path, video_id


def test_render_task_download_authorization_and_recovery(
    client, db, settings, story_factory, tmp_path
):
    from uuid import UUID

    from sqlmodel import select

    from app.modules.assets.models import Asset, AssetType
    from app.modules.tasks.models import Task, TaskStatus
    from app.modules.videos.models import Video, VideoStatus
    from app.workers.runner import run_task

    headers, path, video_id = prepare_video(client, db, settings, story_factory, tmp_path)
    assert client.post(path + "/render", json={}).status_code == 401
    response = client.post(path + "/render", headers=headers, json={})
    assert response.status_code == 202
    identifier = UUID(response.json()["id"])
    run_task(db.get_bind(), str(identifier), settings=settings)
    db.expire_all()
    task = db.get(Task, identifier)
    assert task.status == TaskStatus.SUCCEEDED
    assert db.get(Video, UUID(video_id)).status == VideoStatus.QUALITY_CHECK
    final = db.get(Asset, UUID(task.result["asset_id"]))
    url = f"/api/v1/assets/{final.id}/download"
    assert client.get(url).status_code == 401
    response = client.get(url, headers=headers)
    assert response.status_code == 200
    assert len(response.content) == final.size_bytes
    assert response.headers["content-type"] == "video/mp4"
    assert len(client.get(path + "/assets", headers=headers).json()) >= 4
    # Simulate a crash after committing the film but before acknowledging the task.
    task.status = TaskStatus.RUNNING
    task.result = None
    db.add(task)
    db.commit()
    run_task(db.get_bind(), str(identifier), settings=settings)
    db.expire_all()
    assert db.get(Task, identifier).status == TaskStatus.SUCCEEDED
    assert len(db.exec(select(Asset).where(Asset.type == AssetType.FINAL_VIDEO)).all()) == 1
    credentials = {"email": "other-render@example.com", "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    assert client.get(url, headers=other).status_code == 404
    assert client.get(path + "/assets", headers=other).status_code == 404
    assert client.post(path + "/render", headers=other, json={}).status_code == 404


def test_render_missing_assets_does_not_advance_video(client, db, settings, story_factory):
    from uuid import UUID

    from app.modules.tasks.models import Task
    from app.modules.videos.models import Video
    from app.workers.runner import run_task

    headers, path, video_id, _ = story_factory(client)
    client.post(path + "/script/generate", headers=headers).raise_for_status()
    response = client.post(path + "/render", headers=headers, json={})
    run_task(db.get_bind(), response.json()["id"], settings=settings)
    db.expire_all()
    assert db.get(Task, UUID(response.json()["id"])).status == "failed"
    assert db.get(Video, UUID(video_id)).status == "SCRIPT_READY"


def test_visual_rejects_foreign_or_remote_output(client, db, settings, story_factory, tmp_path):
    from uuid import UUID

    from sqlmodel import select

    from app.integrations.storage.factory import create_storage_provider
    from app.modules.render.visuals import materialize_visual
    from app.modules.tasks.models import Task
    from app.modules.videos.models import Scene

    headers, path, _, _ = story_factory(client)
    client.post(path + "/script/generate", headers=headers).raise_for_status()
    scene = db.exec(select(Scene)).first()
    response = client.post(
        path + "/visuals/image", headers=headers, json={"scene_id": str(scene.id)}
    )
    task = db.get(Task, UUID(response.json()["id"]))
    settings.external_providers_mode = "live"
    settings.storage_local_root = tmp_path
    storage = create_storage_provider(settings)
    for output in (
        {"url": "http://169.254.169.254/"},
        {"object_key": "other-user/image.png", "content_type": "image/png"},
    ):
        with pytest.raises(RenderError, match="assigned storage"):
            materialize_visual(db, task, settings, storage, output, 1)
    storage.close()


def test_video_clip_input_loops_to_scene_duration(media, tmp_path):
    renderer, _, audio = media
    clip = tmp_path / "input.mp4"
    renderer.run(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x180:r=30",
            "-t",
            "0.3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(clip),
        ]
    )
    write_captions(tmp_path / "captions.ass", [("Video", 1, None)])
    result = renderer.render(
        [
            RenderScene(
                visual=clip,
                visual_type="video/mp4",
                audio=audio,
                audio_type="audio/wav",
                duration=1,
                narration="Video",
            )
        ],
        tmp_path,
    )
    probe = renderer.probe(result, "video/mp4")
    assert abs(float(probe["format"]["duration"]) - 1) < 0.1


def test_render_retry_retains_manifest_and_excludes_other_tasks(
    client, db, settings, story_factory, tmp_path, monkeypatch
):
    from datetime import UTC, datetime, timedelta
    from uuid import UUID

    from sqlmodel import select

    from app.modules.tasks.models import Task
    from app.modules.videos.models import Scene, Video
    from app.workers.runner import run_task

    headers, path, video_id = prepare_video(client, db, settings, story_factory, tmp_path)
    identifier = client.post(path + "/render", headers=headers, json={}).json()["id"]

    def unavailable(*args, **kwargs):
        raise RenderError("Transient failure")

    monkeypatch.setattr(FFmpegRenderer, "render", unavailable)
    run_task(db.get_bind(), identifier, settings=settings)
    db.expire_all()
    task = db.get(Task, UUID(identifier))
    assert task.status == "queued"
    manifest = task.checkpoint["render_manifest"]
    assert db.get(Video, UUID(video_id)).status == "RENDERING"
    second = client.post(path + "/render", headers=headers, json={}).json()["id"]
    run_task(db.get_bind(), second, settings=settings)
    db.expire_all()
    assert db.get(Task, UUID(second)).status == "failed"
    scene = db.exec(select(Scene)).first()
    scene.narration = "A new version must not affect this render"
    db.add(scene)
    db.commit()
    for _ in range(2):
        task.available_at = datetime.now(UTC) - timedelta(seconds=1)
        db.add(task)
        db.commit()
        run_task(db.get_bind(), identifier, settings=settings)
        db.expire_all()
    assert task.checkpoint["render_manifest"] == manifest
    assert task.status == "needs_review"
    assert task.attempts == 3
