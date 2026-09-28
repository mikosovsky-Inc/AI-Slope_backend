import json
from uuid import UUID

import pytest
from sqlmodel import select

from app.integrations.llm.factory import get_llm_provider
from app.integrations.llm.mock import MockLLMProvider
from app.modules.scripts.mock import mock_beats, mock_scenes
from app.modules.scripts.schemas import StoryNarrative, StoryOutline, StoryScenes
from app.modules.videos.models import Scene, Video, VideoScript, VideoStatus, VideoStatusEvent
from app.shared.llm import LLMUnavailable


def prepare(client, language="en", format="story"):
    credentials = {"email": "scripts@example.com", "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    channel = client.post(
        "/api/v1/channels",
        headers=headers,
        json={"idea": "Strange fictional stories with a twist", "language": language},
    ).json()
    path = "/api/v1/channels/" + channel["id"]
    client.post(path + "/analyze", headers=headers).raise_for_status()
    ideas = client.post(path + "/ideas/generate", headers=headers).json()["items"]
    idea = next(i for i in ideas if i["format"] == format)
    idea_path = "/api/v1/ideas/" + idea["id"]
    client.post(idea_path + "/approve", headers=headers).raise_for_status()
    video = client.post(idea_path + "/create-video", headers=headers).json()
    return headers, "/api/v1/videos/" + video["id"], UUID(video["id"])


@pytest.mark.parametrize("language", ["pl", "en"])
def test_story_workflow_and_replay(client, db, language):
    headers, path, video_id = prepare(client, language)
    calls = []

    def outline(request):
        calls.append("outline")
        return mock_beats(request)

    def narrative(request):
        calls.append("narrative")
        assert "outline" in json.loads(request.prompt)
        return mock_beats(request)

    def scenes(request):
        calls.append("scenes")
        assert "story" in json.loads(request.prompt)
        return mock_scenes(request)

    client.app.dependency_overrides[get_llm_provider] = lambda: MockLLMProvider(
        {StoryOutline: outline, StoryNarrative: narrative, StoryScenes: scenes}
    )
    assert client.get(path + "/script", headers=headers).status_code == 404
    response = client.post(path + "/script/generate", headers=headers)
    assert response.status_code == 200, response.text
    script = response.json()
    assert script["language"] == language
    assert len(script["scenes"]) == 5
    assert abs(sum(float(s["duration"]) for s in script["scenes"]) - 45) < 0.001
    assert all(s["visual_prompt"] for s in script["scenes"])
    assert client.get(path + "/script", headers=headers).json() == script
    assert client.post(path + "/script/generate", headers=headers).json() == script
    assert calls == ["outline", "narrative", "scenes"]
    assert db.get(Video, video_id).status == VideoStatus.SCRIPT_READY
    events = db.exec(
        select(VideoStatusEvent)
        .where(VideoStatusEvent.video_id == video_id)
        .order_by(VideoStatusEvent.sequence)
    ).all()
    assert [e.to_status for e in events][-2:] == [VideoStatus.SCRIPTING, VideoStatus.SCRIPT_READY]


@pytest.mark.parametrize(
    "bad", ["duration", "narration", "language", "order", "hook", "provider", "unexpected"]
)
def test_story_failure_is_persisted_without_partial_script(client, db, bad):
    headers, path, video_id = prepare(client)

    def scenes(request):
        data = mock_scenes(request)
        if bad == "provider":
            raise LLMUnavailable("private-provider-body")
        if bad == "unexpected":
            raise RuntimeError("private-provider-body")
        if bad == "duration":
            data["scenes"][-1]["duration"] = 100
        if bad == "narration":
            data["scenes"][-1]["narration"] = "Invented replacement"
        if bad == "language":
            data["language"] = "pl"
        if bad == "order":
            data["scenes"][1]["position"] = 1
        if bad == "hook":
            data["scenes"][0]["duration"] = 4
        return data

    client.app.dependency_overrides[get_llm_provider] = lambda: MockLLMProvider(
        {StoryOutline: mock_beats, StoryNarrative: mock_beats, StoryScenes: scenes}
    )
    if bad == "unexpected":
        with pytest.raises(RuntimeError):
            client.post(path + "/script/generate", headers=headers)
    else:
        response = client.post(path + "/script/generate", headers=headers)
        assert response.status_code == (503 if bad == "provider" else 502), response.text
        assert "private-provider-body" not in response.text
    assert db.get(Video, video_id).status == VideoStatus.FAILED
    assert not db.exec(select(VideoScript)).all()
    assert not db.exec(select(Scene)).all()
    assert client.post(path + "/script/generate", headers=headers).status_code == 409
    assert (
        "private"
        not in db.exec(select(VideoStatusEvent).order_by(VideoStatusEvent.sequence.desc()))
        .first()
        .reason
    )


def test_story_auth_and_top5_guard(client):
    headers, path, _ = prepare(client, format="top5")
    for suffix, method in [("/script", "get"), ("/script/generate", "post")]:
        assert getattr(client, method)(path + suffix).status_code == 401
    assert client.post(path + "/script/generate", headers=headers).status_code == 409
    credentials = {"email": "other-script@example.com", "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    assert client.get(path + "/script", headers=other).status_code == 404
    assert client.post(path + "/script/generate", headers=other).status_code == 404
