import pytest

from app.modules.competitors.dependencies import get_competitor_research_provider
from app.modules.competitors.service import ResearchUnavailable
from app.modules.intelligence.research import (
    CompetitorResearchResult,
    LocalCompetitorResearchProvider,
)


def setup(client):
    credentials = {"email": "research@example.com", "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    channel = client.post(
        "/api/v1/channels",
        headers=headers,
        json={"idea": "History stories for adults", "language": "en"},
    ).json()
    return headers, "/api/v1/channels/" + channel["id"]


def record():
    return CompetitorResearchResult(
        name="Example benchmark",
        platform="youtube",
        url="https://example.com/history",
        language="en",
        niche="facts stories",
        example_titles=["Example title"],
        observed_formats=["top5"],
        typical_length_seconds=45,
        publishing_frequency="Two per week",
        notes="Local fixture only",
    )


def test_research_upsert_and_empty_preserves(client):
    headers, path = setup(client)
    assert client.post(path + "/competitor-research", headers=headers).status_code == 409
    client.post(path + "/analyze", headers=headers).raise_for_status()
    fixture = record()
    client.app.dependency_overrides[get_competitor_research_provider] = lambda: (
        LocalCompetitorResearchProvider([fixture])
    )
    first = client.post(path + "/competitor-research", headers=headers)
    assert first.status_code == 200, first.text
    fixture.example_titles = ["New title", "New title"]
    second = client.post(path + "/competitor-research", headers=headers).json()
    assert first.json()["items"][0]["id"] == second["items"][0]["id"]
    assert second["items"][0]["example_titles"] == ["New title"]
    assert second["processed"] == 1
    client.app.dependency_overrides[get_competitor_research_provider] = (
        LocalCompetitorResearchProvider
    )
    assert client.post(path + "/competitor-research", headers=headers).json()["processed"] == 0
    listed = client.get(path + "/competitors", headers=headers).json()
    assert listed["total"] == 1
    assert listed["items"] == second["items"]
    assert client.get(path + "/competitors?offset=1", headers=headers).json()["items"] == []


def test_research_auth_ownership(client):
    headers, path = setup(client)
    for suffix, method in [("/competitors", "get"), ("/competitor-research", "post")]:
        assert getattr(client, method)(path + suffix).status_code == 401
    other = {"email": "other@example.com", "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=other).raise_for_status()
    token = client.post("/api/v1/auth/login", json=other).json()["access_token"]
    other_headers = {"Authorization": f"Bearer {token}"}
    for suffix, method in [("/competitors", "get"), ("/competitor-research", "post")]:
        assert getattr(client, method)(path + suffix, headers=other_headers).status_code == 404
    assert client.get(path + "/competitors?limit=101", headers=headers).status_code == 422


@pytest.mark.parametrize("mode", ["duplicate", "language", "invalid", "failure", "conflict"])
def test_invalid_research_preserves_data(client, mode):
    headers, path = setup(client)
    client.post(path + "/analyze", headers=headers).raise_for_status()

    class Provider:
        def research(self, query):
            if mode == "failure":
                raise ResearchUnavailable("private provider detail")
            if mode == "conflict":
                client.patch(
                    path, headers=headers, json={"name": "Concurrent edit"}
                ).raise_for_status()
            item = record()
            if mode == "duplicate":
                return [item, item]
            if mode == "language":
                item.language = "pl"
            if mode == "invalid":
                return [{"name": "invalid"}]
            return [item]

    client.app.dependency_overrides[get_competitor_research_provider] = Provider
    response = client.post(path + "/competitor-research", headers=headers)
    assert response.status_code == (
        503 if mode == "failure" else 409 if mode == "conflict" else 502
    )
    assert "private" not in response.text
    assert client.get(path + "/competitors", headers=headers).json()["total"] == 0
