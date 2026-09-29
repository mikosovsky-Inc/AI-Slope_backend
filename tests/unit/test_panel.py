from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlmodel import select

from app.modules.channels.models import Channel
from app.modules.costs.models import CostEvent
from app.modules.tasks.models import Task, TaskKind, TaskStatus
from app.modules.videos.models import Video, VideoStatus, VideoStatusEvent
from app.modules.videos.service import transition_video
from app.workers.runner import run_task


def prepared(client, story_factory):
    headers, path, vid, channel = story_factory(client)
    client.post(path + "/script/generate", headers=headers).raise_for_status()
    scenes = client.get(path + "/scenes", headers=headers)
    scenes.raise_for_status()
    return headers, path, UUID(vid), channel, scenes.json()


def test_panel_reads_costs_pagination_and_empty_dashboard(client, db, story_factory):
    h, path, vid, channel, scenes = prepared(client, story_factory)
    channel_id = UUID(channel.rsplit("/", 1)[1])
    for estimated, actual in [("0.12", None), ("0.20", "0.15"), ("0.30", "0")]:
        db.add(
            CostEvent(
                video_id=vid,
                channel_id=channel_id,
                provider="test",
                operation="test",
                model="test",
                estimated_cost_usd=Decimal(estimated),
                actual_cost_usd=Decimal(actual) if actual is not None else None,
            )
        )
    db.commit()
    response = client.get("/api/v1/dashboard", headers=h)
    response.raise_for_status()
    assert response.json()["videos"] == 1
    assert response.json()["videos_by_status"]["SCRIPT_READY"] == 1
    costs = client.get(channel + "/costs?limit=1&offset=1", headers=h).json()
    assert costs["total"] == 3 and len(costs["items"]) == 1
    assert Decimal(costs["summary"]["estimated_usd"]) == Decimal("0.62")
    assert Decimal(costs["summary"]["actual_usd"]) == Decimal("0.15")
    assert Decimal(costs["summary"]["effective_usd"]) == Decimal("0.27")
    assert costs["summary"]["pending_actual_events"] == 1
    assert client.get(channel + "/overview", headers=h).json()["latest_plan"] is None
    assert client.get(channel + "/videos?status=READY", headers=h).json()["total"] == 0
    assert client.get(channel + "/videos?offset=1", headers=h).json()["items"] == []
    assert client.get(channel + "/videos?limit=0", headers=h).status_code == 422
    assert client.get(path, headers=h).json()["id"] == str(vid)
    assert scenes[0]["id"] and scenes[0]["position"] == 1
    assert client.get(path + "/status", headers=h).json()["has_active_tasks"] is False


def test_all_panel_routes_require_auth_and_ownership(client, db, story_factory):
    h, path, vid, channel, scenes = prepared(client, story_factory)
    scene = "/api/v1/scenes/" + scenes[0]["id"]
    credentials = {"email": "other@example.com", "password": "another-long-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    other = {"Authorization": "Bearer " + token}
    requests = [
        ("GET", channel + suffix, None) for suffix in ("/overview", "/videos", "/costs", "/ideas")
    ]
    requests += [("GET", path + suffix, None) for suffix in ("", "/scenes", "/status")]
    requests += [
        ("POST", path + "/retry", {"task_id": str(uuid4())}),
        ("PATCH", scene, {"mood": "calm"}),
        ("POST", scene + "/regenerate", {"kind": "audio"}),
    ]
    for method, url, body in requests:
        assert client.request(method, url, json=body).status_code == 401, url
        assert client.request(method, url, headers=other, json=body).status_code == 404, url
    assert client.get("/api/v1/dashboard").status_code == 401
    result = client.get("/api/v1/dashboard", headers=other).json()
    assert result["channels"] == result["videos"] == result["costs"]["events"] == 0


def test_scene_patch_and_generation_guards(client, db, story_factory):
    h, path, vid, _, scenes = prepared(client, story_factory)
    scene_path = "/api/v1/scenes/" + scenes[0]["id"]
    assert client.patch(scene_path, headers=h, json={"mood": "calm"}).json()["mood"] == "calm"
    for data in ({}, {"narration": None}, {"duration": 12}, {"narration": ""}):
        assert client.patch(scene_path, headers=h, json=data).status_code == 422
    key_headers = h | {"Idempotency-Key": "scene-regenerate-test"}
    first = client.post(scene_path + "/regenerate", headers=key_headers, json={"kind": "audio"})
    assert first.status_code == 202
    replay = client.post(scene_path + "/regenerate", headers=key_headers, json={"kind": "audio"})
    assert replay.json()["id"] == first.json()["id"]
    assert client.patch(scene_path, headers=h, json={"mood": "happy"}).status_code == 409
    assert (
        client.post(scene_path + "/regenerate", headers=h, json={"kind": "audio"}).status_code
        == 409
    )
    assert client.get(path + "/status", headers=h).json()["has_active_tasks"] is True


def test_retry_failed_story_reuses_task_and_finishes_workflow(client, db, settings, story_factory):
    h, path, vid, channel_path = story_factory(client)
    video_id = UUID(vid)
    owner = db.get(Channel, UUID(channel_path.rsplit("/", 1)[1])).owner_id
    transition_video(db, video_id, VideoStatus.SCRIPTING, reason="test start")
    transition_video(db, video_id, VideoStatus.FAILED, reason="test failure")
    task = Task(
        owner_id=owner,
        video_id=video_id,
        kind=TaskKind.STORY,
        status=TaskStatus.FAILED,
        idempotency_key="failed-story",
        attempts=1,
        parameters={"workflow": True},
        completed_at=datetime.now(UTC),
    )
    db.add(task)
    db.commit()
    result = client.post(path + "/retry", headers=h, json={"task_id": str(task.id)})
    assert result.status_code == 202
    assert result.json()["id"] == str(task.id)
    assert db.get(Video, video_id).status == VideoStatus.IDEA_GENERATED
    replay = client.post(path + "/retry", headers=h, json={"task_id": str(task.id)})
    assert replay.status_code == 202
    assert db.get(Task, task.id).checkpoint["panel_retries"] == 1
    run_task(db.get_bind(), str(task.id), settings=settings)
    db.expire_all()
    assert db.get(Task, task.id).status == TaskStatus.SUCCEEDED
    assert db.get(Video, video_id).status == VideoStatus.SCRIPT_READY
    assert (
        db.exec(select(Task).where(Task.kind == TaskKind.DIRECT)).one().status == TaskStatus.QUEUED
    )
    events = db.exec(select(VideoStatusEvent).where(VideoStatusEvent.video_id == video_id)).all()
    assert sum(e.from_status == VideoStatus.FAILED for e in events) == 1


@pytest.mark.parametrize(
    "task_status,retries",
    [(TaskStatus.NEEDS_REVIEW, 0), (TaskStatus.FAILED, 3), (TaskStatus.SUCCEEDED, 0)],
)
def test_retry_rejects_uncertain_completed_and_exhausted_tasks(
    client, db, story_factory, task_status, retries
):
    h, path, vid, channel = story_factory(client)
    owner = db.get(Channel, UUID(channel.rsplit("/", 1)[1])).owner_id
    task = Task(
        owner_id=owner,
        video_id=UUID(vid),
        kind=TaskKind.STORY,
        status=task_status,
        idempotency_key="blocked",
        checkpoint={"panel_retries": retries},
    )
    db.add(task)
    db.commit()
    assert (
        client.post(path + "/retry", headers=h, json={"task_id": str(task.id)}).status_code == 409
    )


def test_panel_audio_regeneration_creates_new_asset_but_replay_does_not(
    client, db, settings, tmp_path, story_factory
):
    from app.modules.assets.models import Asset

    h, path, vid, _, scenes = prepared(client, story_factory)
    settings.storage_local_root = tmp_path
    scene_path = "/api/v1/scenes/" + scenes[0]["id"]
    identifiers = []
    for index in range(2):
        headers = h | {"Idempotency-Key": f"audio-regeneration-{index}"}
        result = client.post(scene_path + "/regenerate", headers=headers, json={"kind": "audio"})
        assert result.status_code == 202
        identifier = result.json()["id"]
        run_task(db.get_bind(), identifier, settings=settings)
        db.expire_all()
        task = db.get(Task, UUID(identifier))
        assert task.status == TaskStatus.SUCCEEDED
        identifiers.append(task.result["id"])
        replay = client.post(scene_path + "/regenerate", headers=headers, json={"kind": "audio"})
        assert replay.json()["id"] == identifier
        run_task(db.get_bind(), identifier, settings=settings)
    assert len(set(identifiers)) == 2
    assert len(db.exec(select(Asset).where(Asset.scene_id == UUID(scenes[0]["id"]))).all()) == 2
    assert client.patch(scene_path, headers=h, json={"narration": "New text"}).status_code == 409


def test_regeneration_guards_type_review_and_final_video(client, db, story_factory):
    h, path, vid, channel, scenes = prepared(client, story_factory)
    scene = scenes[0]
    url = "/api/v1/scenes/" + scene["id"] + "/regenerate"
    wrong = "video" if scene["visual_type"] == "image" else "image"
    assert client.post(url, headers=h, json={"kind": wrong}).status_code == 409
    owner = db.get(Channel, UUID(channel.rsplit("/", 1)[1])).owner_id
    task = Task(
        owner_id=owner,
        video_id=vid,
        scene_id=UUID(scene["id"]),
        kind=TaskKind.AUDIO,
        status=TaskStatus.NEEDS_REVIEW,
        idempotency_key="unknown",
    )
    db.add(task)
    db.commit()
    assert client.post(url, headers=h, json={"kind": "audio"}).status_code == 409
    video = db.get(Video, vid)
    video.status = VideoStatus.READY
    db.add(video)
    db.commit()
    assert client.post(url, headers=h, json={"kind": scene["visual_type"]}).status_code == 409
