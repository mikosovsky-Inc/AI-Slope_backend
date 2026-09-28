from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID

import pytest
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.integrations.llm.factory import get_llm_provider
from app.integrations.llm.mock import MockLLMProvider
from app.modules.scripts.mock import mock_beats, mock_scenes
from app.modules.scripts.schemas import StoryNarrative, StoryOutline, StoryScenes
from app.modules.videos.models import Scene, Video, VideoScript, VideoStatus


@pytest.fixture
def story_video(pg_client):
    credentials = {"email": "story-db@example.com", "password": "long-test-password"}
    pg_client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = pg_client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    channel = pg_client.post(
        "/api/v1/channels",
        headers=headers,
        json={"idea": "Short fictional mystery stories", "language": "en"},
    ).json()
    path = "/api/v1/channels/" + channel["id"]
    pg_client.post(path + "/analyze", headers=headers).raise_for_status()
    ideas = pg_client.post(path + "/ideas/generate", headers=headers).json()["items"]
    idea = next(i for i in ideas if i["format"] == "story")
    idea_path = "/api/v1/ideas/" + idea["id"]
    pg_client.post(idea_path + "/approve", headers=headers).raise_for_status()
    video = pg_client.post(idea_path + "/create-video", headers=headers).json()
    return headers, "/api/v1/videos/" + video["id"], UUID(video["id"])


def test_story_concurrent_generation_and_persistence(pg_client, story_video):
    headers, path, _ = story_video
    started, release = Event(), Event()
    calls = []

    def outline(request):
        calls.append("outline")
        started.set()
        assert release.wait(timeout=15)
        return mock_beats(request)

    pg_client.app.dependency_overrides[get_llm_provider] = lambda: MockLLMProvider(
        {StoryOutline: outline, StoryNarrative: mock_beats, StoryScenes: mock_scenes}
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(pg_client.post, path + "/script/generate", headers=headers)
        try:
            assert started.wait(timeout=10)
            assert pg_client.post(path + "/script/generate", headers=headers).status_code == 409
        finally:
            release.set()
        result = first.result(timeout=20)
    assert result.status_code == 200, result.text
    assert pg_client.get(path + "/script", headers=headers).json() == result.json()
    assert pg_client.post(path + "/script/generate", headers=headers).json() == result.json()
    assert calls == ["outline"]


def test_story_scene_write_failure_rolls_back_and_marks_failed(
    pg_client, postgres_engine, story_video
):
    headers, path, video_id = story_video

    def fail_scene(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO scenes"):
            raise SQLAlchemyError("Private database error")

    event.listen(postgres_engine, "before_cursor_execute", fail_scene)
    try:
        response = pg_client.post(path + "/script/generate", headers=headers)
        assert response.status_code == 503, response.text
        assert "Private" not in response.text
    finally:
        event.remove(postgres_engine, "before_cursor_execute", fail_scene)
    with Session(postgres_engine) as db:
        assert db.get(Video, video_id).status == VideoStatus.FAILED
        assert not db.exec(select(VideoScript)).all()
        assert not db.exec(select(Scene)).all()
