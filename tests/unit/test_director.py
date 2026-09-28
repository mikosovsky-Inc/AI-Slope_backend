from decimal import Decimal
from uuid import UUID

import pytest
from sqlmodel import select

from app.modules.director.models import DirectorPlan
from app.modules.videos.models import Video, VideoStatus


def test_director_plan_replay_and_preserves_script(client, db, story_factory):
    headers, path, video_id, channel_path = story_factory(client)
    assert client.get(path + "/direction", headers=headers).status_code == 404
    assert client.post(path + "/direct", headers=headers).status_code == 409
    script = client.post(path + "/script/generate", headers=headers).json()
    # Later channel changes must not alter the saved video blueprint.
    client.patch(
        channel_path,
        headers=headers,
        json={
            "blueprint": {
                "configuration": {
                    "visual_style": {"description": "Different style", "video_scene_ratio": 1}
                }
            }
        },
    ).raise_for_status()
    result = client.post(path + "/direct", headers=headers)
    assert result.status_code == 200, result.text
    plan = result.json()
    assert sum(s["visual_type"] == "video" for s in plan["scenes"]) == 1
    assert sum(s["visual_type"] == "image" for s in plan["scenes"]) == 4
    assert plan["requested_video_ratio"] == 0.25
    assert all(s["visual_style"] == "Thematic illustrations" for s in plan["scenes"])
    assert sorted(s["generation_priority"] for s in plan["scenes"]) == list(range(1, 6))
    assert plan["scenes"][0]["importance"] == 1
    assert Decimal(plan["estimated_cost_usd"]) <= Decimal(plan["visual_budget_usd"])
    assert client.post(path + "/direct", headers=headers).json() == plan
    assert client.get(path + "/direction", headers=headers).json() == plan
    updated = client.get(path + "/script", headers=headers).json()
    for before, after in zip(script["scenes"], updated["scenes"], strict=True):
        for key in ["position", "duration", "narration", "caption_emphasis"]:
            assert before[key] == after[key]
    assert db.get(Video, UUID(video_id)).status == VideoStatus.SCRIPT_READY


@pytest.mark.parametrize(
    ("ratio", "budget", "expected"), [(0, "1", 0), (1, "1", 5), (1, "0.05", 0), (1, "0.001", None)]
)
def test_director_ratio_and_budget(client, db, story_factory, ratio, budget, expected):
    headers, path, video_id, _ = story_factory(client)
    client.post(path + "/script/generate", headers=headers).raise_for_status()
    video = db.get(Video, UUID(video_id))
    snapshot = dict(video.blueprint_snapshot)
    snapshot["configuration"]["visual_style"]["video_scene_ratio"] = ratio
    video.blueprint_snapshot = snapshot
    video.budget_limit_usd = Decimal(budget)
    db.add(video)
    # Mark JSON dirty explicitly for this direct fixture edit.
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(video, "blueprint_snapshot")
    db.commit()
    response = client.post(path + "/direct", headers=headers)
    if expected is None:
        assert response.status_code == 409
        assert not db.exec(select(DirectorPlan)).all()
    else:
        assert response.status_code == 200, response.text
        assert sum(s["visual_type"] == "video" for s in response.json()["scenes"]) == expected


def test_director_auth(client, story_factory):
    headers, path, _, _ = story_factory(client)
    credentials = {"email": "other-director@example.com", "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    for method, suffix in [("post", "/direct"), ("get", "/direction")]:
        assert getattr(client, method)(path + suffix).status_code == 401
        assert getattr(client, method)(path + suffix, headers=other).status_code == 404


def test_director_top5_preserves_citations(client, top5_factory, source_documents):
    from app.modules.research.provider import LocalResearchProvider, get_research_provider

    headers, path, _, _ = top5_factory(client)
    client.app.dependency_overrides[get_research_provider] = lambda: LocalResearchProvider(
        source_documents
    )
    client.post(path + "/research", headers=headers).raise_for_status()
    before = client.post(path + "/top5-script/generate", headers=headers).json()
    result = client.post(path + "/direct", headers=headers)
    assert result.status_code == 200, result.text
    after = client.get(path + "/script", headers=headers).json()
    assert before["citations"] == after["citations"]
    assert [s["narration"] for s in before["scenes"]] == [s["narration"] for s in after["scenes"]]
