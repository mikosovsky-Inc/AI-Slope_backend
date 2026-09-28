from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlmodel import select

from app.modules.ideas.models import ContentFormat, ContentIdea, IdeaStatus
from app.modules.videos.models import Video, VideoScript, VideoStatus, VideoStatusEvent
from app.modules.videos.schemas import ScriptInput
from app.modules.videos.service import transition_video
from app.modules.videos.state import InvalidVideoTransition, validate_transition


def login(client, email="videos@example.com"):
    credentials = {"email": email, "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def prepare(client):
    headers = login(client)
    channel = client.post(
        "/api/v1/channels",
        headers=headers,
        json={"idea": "Short fictional stories and facts", "language": "en"},
    ).json()
    path = "/api/v1/channels/" + channel["id"]
    client.post(path + "/analyze", headers=headers).raise_for_status()
    ideas = client.post(path + "/ideas/generate", headers=headers).json()["items"]
    return headers, path, ideas


@pytest.mark.parametrize("format", ["top5", "story"])
def test_create_video_idempotent_snapshot_and_history(client, db, format):
    headers, path, ideas = prepare(client)
    idea = next(i for i in ideas if i["format"] == format)
    idea_path = "/api/v1/ideas/" + idea["id"]
    assert client.post(idea_path + "/create-video", headers=headers).status_code == 409
    client.post(idea_path + "/approve", headers=headers).raise_for_status()
    response = client.post(idea_path + "/create-video", headers=headers)
    assert response.status_code == 201, response.text
    video = response.json()
    assert video["status"] == "IDEA_GENERATED"
    assert video["format"] == format
    assert video["duration_target"] == 45
    assert db.get(ContentIdea, UUID(idea["id"])).status == IdeaStatus.USED
    repeated = client.post(idea_path + "/create-video", headers=headers)
    assert repeated.status_code == 200
    assert repeated.json() == video
    assert client.post(idea_path + "/reject", headers=headers).status_code == 409
    client.patch(path, headers=headers, json={"budget_per_video_usd": "1.5"}).raise_for_status()
    client.post(path + "/analyze", headers=headers).raise_for_status()
    assert client.post(idea_path + "/create-video", headers=headers).json() == video
    history = db.exec(
        select(VideoStatusEvent)
        .where(VideoStatusEvent.video_id == UUID(video["id"]))
        .order_by(VideoStatusEvent.sequence)
    ).all()
    assert [e.to_status for e in history] == [VideoStatus.DRAFT, VideoStatus.IDEA_GENERATED]
    assert history[0].from_status is None
    assert history[1].from_status == VideoStatus.DRAFT
    assert not db.exec(select(VideoScript)).all()


def test_create_video_auth_and_rejected(client):
    headers, _, ideas = prepare(client)
    path = "/api/v1/ideas/" + ideas[0]["id"]
    assert client.post(path + "/create-video").status_code == 401
    other = login(client, "other@example.com")
    assert client.post(path + "/create-video", headers=other).status_code == 404
    assert (
        client.post("/api/v1/ideas/" + str(uuid4()) + "/create-video", headers=headers).status_code
        == 404
    )
    client.post(path + "/reject", headers=headers).raise_for_status()
    assert client.post(path + "/create-video", headers=headers).status_code == 409


@pytest.mark.parametrize("format", list(ContentFormat))
def test_state_machine_complete_paths(format):
    S = VideoStatus
    path = [S.DRAFT, S.IDEA_GENERATED]
    if format == ContentFormat.TOP5:
        path += [S.RESEARCHING, S.RESEARCHED]
    path += [
        S.SCRIPTING,
        S.SCRIPT_READY,
        S.GENERATING_ASSETS,
        S.ASSETS_READY,
        S.GENERATING_AUDIO,
        S.READY_TO_RENDER,
        S.RENDERING,
        S.QUALITY_CHECK,
        S.READY,
        S.PUBLISHED,
    ]
    for source, target in zip(path, path[1:]):
        validate_transition(source, target, format)
        validate_transition(source, source, format)
        validate_transition(source, S.FAILED, format)
    for source, target in [
        (S.DRAFT, S.READY),
        (S.SCRIPTING, S.RESEARCHED),
        (S.FAILED, S.SCRIPTING),
        (S.PUBLISHED, S.FAILED),
        (S.IDEA_GENERATED, S.SCRIPTING if format == ContentFormat.TOP5 else S.RESEARCHING),
    ]:
        with pytest.raises(InvalidVideoTransition):
            validate_transition(source, target, format)


def test_transition_records_event_and_is_atomic(client, db):
    headers, _, ideas = prepare(client)
    idea = next(i for i in ideas if i["format"] == "story")
    path = "/api/v1/ideas/" + idea["id"]
    client.post(path + "/approve", headers=headers).raise_for_status()
    video_id = UUID(client.post(path + "/create-video", headers=headers).json()["id"])
    transition_video(db, video_id, VideoStatus.SCRIPTING, reason="Script stage started")
    db.commit()
    transition_video(db, video_id, VideoStatus.SCRIPTING, reason="Repeated delivery")
    db.commit()
    assert len(db.exec(select(VideoStatusEvent)).all()) == 3
    with pytest.raises(InvalidVideoTransition):
        transition_video(db, video_id, VideoStatus.READY, reason="Invalid jump")
    db.rollback()
    transition_video(db, video_id, VideoStatus.FAILED, reason="script_provider_unavailable")
    db.rollback()
    assert db.get(Video, video_id).status == VideoStatus.SCRIPTING
    assert len(db.exec(select(VideoStatusEvent)).all()) == 3


def test_script_input_validation():
    scene = {
        "position": 1,
        "duration": "4.5",
        "narration": "Narration",
        "visual_prompt": "Image",
        "visual_type": "image",
        "camera_motion": "slow_push",
        "mood": "mysterious",
        "caption_emphasis": ["Narration"],
    }
    script = {
        "title": "Title",
        "hook": "Hook",
        "language": "en",
        "duration_target": 45,
        "scenes": [scene],
    }
    assert ScriptInput.model_validate(script).scenes[0].duration > 0
    for update in [{"position": 2}, {"duration": 0}, {"visual_type": "invalid"}, {"narration": ""}]:
        with pytest.raises(ValidationError):
            ScriptInput.model_validate(script | {"scenes": [scene | update]})
