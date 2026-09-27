from uuid import UUID, uuid4

import pytest

from app.integrations.llm.factory import get_llm_provider
from app.integrations.llm.mock import MockLLMProvider
from app.modules.ideas.mock import mock_ideas
from app.modules.ideas.models import ContentIdea, IdeaStatus
from app.modules.ideas.schemas import GeneratedIdeas
from app.shared.llm import LLMUnavailable


def login(client, email="ideas@example.com"):
    credentials = {"email": email, "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def prepare(client, language="en"):
    headers = login(client)
    response = client.post(
        "/api/v1/channels",
        headers=headers,
        json={"idea": "Short historical stories and facts", "language": language},
    )
    response.raise_for_status()
    path = "/api/v1/channels/" + response.json()["id"]
    return headers, path


@pytest.mark.parametrize("language", ["pl", "en"])
def test_generation_and_decisions(client, language):
    headers, path = prepare(client, language)
    assert client.post(path + "/ideas/generate", headers=headers).status_code == 409
    client.post(path + "/analyze", headers=headers).raise_for_status()
    generated = client.post(path + "/ideas/generate?count=12", headers=headers)
    assert generated.status_code == 201, generated.text
    items = generated.json()["items"]
    assert len(items) == 12
    assert all(i["status"] == "candidate" and i["language"] == language for i in items)
    idea_path = "/api/v1/ideas/" + items[0]["id"]
    approved = client.post(idea_path + "/approve", headers=headers).json()
    assert approved["status"] == "approved"
    assert client.post(idea_path + "/approve", headers=headers).json() == approved
    assert client.get(path + "/ideas?status=approved", headers=headers).json()["total"] == 1
    assert client.post(idea_path + "/reject", headers=headers).json()["status"] == "rejected"
    assert client.post(path + "/ideas/generate", headers=headers).status_code == 201
    assert client.get(path + "/ideas?limit=1&offset=1", headers=headers).json()["total"] == 22
    client.post(path + "/analyze", headers=headers).raise_for_status()
    assert client.get(path + "/ideas", headers=headers).json()["total"] == 22


def test_security_and_used_status(client, db):
    headers, path = prepare(client)
    client.post(path + "/analyze", headers=headers).raise_for_status()
    item = client.post(path + "/ideas/generate", headers=headers).json()["items"][0]
    idea_path = "/api/v1/ideas/" + item["id"]
    other = login(client, "other@example.com")
    for method, url in [
        ("post", path + "/ideas/generate"),
        ("get", path + "/ideas"),
        ("post", idea_path + "/approve"),
        ("post", idea_path + "/reject"),
    ]:
        assert getattr(client, method)(url).status_code == 401
        assert getattr(client, method)(url, headers=other).status_code == 404
    assert (
        client.post("/api/v1/ideas/" + str(uuid4()) + "/approve", headers=headers).status_code
        == 404
    )
    row = db.get(ContentIdea, UUID(item["id"]))
    row.status = IdeaStatus.USED
    db.add(row)
    db.commit()
    for action in ["approve", "reject"]:
        assert client.post(idea_path + "/" + action, headers=headers).status_code == 409
    for query in ["count=9", "count=21"]:
        assert client.post(path + "/ideas/generate?" + query, headers=headers).status_code == 422
    assert client.get(path + "/ideas?status=bad", headers=headers).status_code == 422


@pytest.mark.parametrize(
    "mode", ["duplicate", "pillar", "count", "score", "format", "failure", "conflict"]
)
def test_invalid_generation_is_atomic(client, mode):
    headers, path = prepare(client)
    client.post(path + "/analyze", headers=headers).raise_for_status()
    client.patch(
        path,
        headers=headers,
        json={
            "blueprint": {
                "configuration": {"target_audience": "Adults", "formats": {"top5": 1, "story": 0}},
                "content_pillars": [{"name": "History"}],
            }
        },
    ).raise_for_status()

    def generate(request):
        output = mock_ideas(request)
        if mode == "duplicate":
            output["items"][1]["title"] = "  " + output["items"][0]["title"].upper() + "  "
        if mode == "pillar":
            output["items"][0]["content_pillar"] = "Unknown"
        if mode == "count":
            output["items"].pop()
        if mode == "score":
            output["items"][0]["novelty_heuristic"]["score"] = 2
        if mode == "format":
            output["items"][0]["format"] = "story"
        if mode == "failure":
            raise LLMUnavailable("private details")
        if mode == "conflict":
            client.patch(path, headers=headers, json={"name": "Concurrent edit"}).raise_for_status()
        return output

    client.app.dependency_overrides[get_llm_provider] = lambda: MockLLMProvider(
        {GeneratedIdeas: generate}
    )
    response = client.post(path + "/ideas/generate", headers=headers)
    expected = 503 if mode == "failure" else 409 if mode == "conflict" else 502
    assert response.status_code == expected, response.text
    assert "private" not in response.text
    assert client.get(path + "/ideas", headers=headers).json()["total"] == 0


def test_history_and_benchmarks_passed_and_duplicates_rejected(client):
    import json

    from app.modules.competitors.dependencies import get_competitor_research_provider
    from app.modules.intelligence.research import (
        CompetitorResearchResult,
        LocalCompetitorResearchProvider,
    )

    headers, path = prepare(client)
    client.post(path + "/analyze", headers=headers).raise_for_status()
    benchmark = CompetitorResearchResult(
        name="Fixture",
        platform="youtube",
        url="https://example.com/fixture",
        language="en",
        niche="facts",
        example_titles=["Known title"],
    )
    client.app.dependency_overrides[get_competitor_research_provider] = lambda: (
        LocalCompetitorResearchProvider([benchmark])
    )
    client.post(path + "/competitor-research", headers=headers).raise_for_status()
    saved = client.post(path + "/ideas/generate", headers=headers).json()["items"]

    def generate(request):
        data = json.loads(request.prompt)
        assert data["previous_count"] == 10
        assert len(data["previous_topics"]) == 10
        assert data["competitors"]["items"][0]["example_titles"] == ["Known title"]
        output = mock_ideas(request)
        output["items"][0]["title"] = saved[0]["title"]
        return output

    client.app.dependency_overrides[get_llm_provider] = lambda: MockLLMProvider(
        {GeneratedIdeas: generate}
    )
    assert client.post(path + "/ideas/generate", headers=headers).status_code == 502
    assert client.get(path + "/ideas", headers=headers).json()["total"] == 10
