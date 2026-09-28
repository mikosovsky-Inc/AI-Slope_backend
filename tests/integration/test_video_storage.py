from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from sqlalchemy import event, func, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from app.modules.ideas.models import ContentIdea, IdeaStatus
from app.modules.videos.models import Scene, Video, VideoScript, VideoStatusEvent


def prepare(client):
    credentials = {"email": "video-db@example.com", "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    channel = client.post(
        "/api/v1/channels",
        headers=headers,
        json={"idea": "Historical facts and fictional stories", "language": "en"},
    ).json()
    path = "/api/v1/channels/" + channel["id"]
    client.post(path + "/analyze", headers=headers).raise_for_status()
    idea = client.post(path + "/ideas/generate", headers=headers).json()["items"][0]
    idea_path = "/api/v1/ideas/" + idea["id"]
    client.post(idea_path + "/approve", headers=headers).raise_for_status()
    return headers, path, idea_path, UUID(idea["id"])


def test_concurrent_create_video_and_cascade(pg_client, postgres_engine):
    headers, channel_path, path, idea_id = prepare(pg_client)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(pg_client.post, path + "/create-video", headers=headers)
            for _ in range(2)
        ]
        responses = [f.result(timeout=20) for f in futures]
    assert sorted(r.status_code for r in responses) == [200, 201]
    assert responses[0].json() == responses[1].json()
    video_id = UUID(responses[0].json()["id"])
    with Session(postgres_engine) as db:
        assert db.exec(select(func.count()).select_from(Video)).one() == 1
        assert db.exec(select(func.count()).select_from(VideoStatusEvent)).one() == 2
        assert db.get(ContentIdea, idea_id).status == IdeaStatus.USED
        script = VideoScript(
            video_id=video_id, title="Example", hook="Hook", language="en", duration_target=45
        )
        db.add(script)
        db.flush()
        db.add(
            Scene(
                script_id=script.id,
                position=1,
                duration="4.5",
                narration="Example",
                visual_prompt="Illustration",
                visual_type="image",
                camera_motion="slow_push",
                mood="mysterious",
                caption_emphasis=["Example"],
            )
        )
        db.commit()
    for statement in [
        "UPDATE videos SET status = 'invalid'",
        "UPDATE scenes SET duration = 0",
        "UPDATE scenes SET visual_type = 'bad'",
    ]:
        with pytest.raises(IntegrityError), postgres_engine.begin() as conn:
            conn.execute(text(statement))
    assert pg_client.delete(channel_path, headers=headers).status_code == 204
    with Session(postgres_engine) as db:
        for model in [Video, VideoScript, Scene, VideoStatusEvent]:
            assert db.exec(select(func.count()).select_from(model)).one() == 0


def test_failed_history_write_rolls_back_video_and_idea(pg_client, postgres_engine):
    headers, _, path, idea_id = prepare(pg_client)

    def fail_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO video_status_events"):
            raise SQLAlchemyError("Simulated history storage failure")

    event.listen(postgres_engine, "before_cursor_execute", fail_insert)
    try:
        assert pg_client.post(path + "/create-video", headers=headers).status_code == 503
    finally:
        event.remove(postgres_engine, "before_cursor_execute", fail_insert)
    with Session(postgres_engine) as db:
        assert db.exec(select(func.count()).select_from(Video)).one() == 0
        assert db.exec(select(func.count()).select_from(VideoStatusEvent)).one() == 0
        assert db.get(ContentIdea, idea_id).status == IdeaStatus.APPROVED
    assert pg_client.post(path + "/create-video", headers=headers).status_code == 201
