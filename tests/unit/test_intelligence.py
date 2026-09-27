import json
from uuid import uuid4

import pytest

from app.integrations.llm.factory import get_llm_provider
from app.integrations.llm.mock import MockLLMProvider
from app.modules.intelligence.mock import mock_analysis
from app.modules.intelligence.schemas import ChannelAnalysis
from app.shared.llm import LLMIncomplete, LLMInvalidOutput, LLMRefusal, LLMRequest, LLMUnavailable


def login(client, email="owner@example.com"):
    credentials = {"email": email, "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def create(client, headers, language="pl"):
    response = client.post(
        "/api/v1/channels",
        headers=headers,
        json={
            "idea": "Short fictional horror stories with twists",
            "language": language,
            "videos_per_day": 3,
            "budget_per_video_usd": "0.40",
        },
    )
    response.raise_for_status()
    return response.json()


@pytest.mark.parametrize("language", ["pl", "en"])
def test_analyze_persists_and_replaces(client, language):
    headers = login(client)
    original = create(client, headers, language)
    path = f"/api/v1/channels/{original['id']}"
    for _ in range(2):
        response = client.post(path + "/analyze", headers=headers)
        assert response.status_code == 200, response.text
        result = response.json()
        config = result["blueprint"]["configuration"]
        assert config["niche_description"]
        assert config["hook_style"]
        assert config["seed_keywords"]
        assert config["suggested_posting_strategy"]["videos_per_day"] == 2
        assert len(result["blueprint"]["content_pillars"]) == 1
        for key in ["id", "idea", "language", "status", "videos_per_day", "budget_per_video_usd"]:
            assert result[key] == original[key]
        assert result["blueprint"]["id"] == original["blueprint"]["id"]
        assert client.get(path, headers=headers).json() == result


def test_auth_and_ownership_before_provider(client):
    owner = login(client)
    original = create(client, owner)
    other = login(client, "other@example.com")
    calls = []

    def generate(request):
        calls.append(request)
        return mock_analysis(request)

    client.app.dependency_overrides[get_llm_provider] = lambda: MockLLMProvider(
        {ChannelAnalysis: generate}
    )
    path = f"/api/v1/channels/{original['id']}/analyze"
    assert client.post(path).status_code == 401
    assert client.post(path, headers=other).status_code == 404
    assert client.post(f"/api/v1/channels/{uuid4()}/analyze", headers=owner).status_code == 404
    assert not calls


@pytest.mark.parametrize(
    ("error", "status"),
    [(LLMUnavailable, 503), (LLMRefusal, 422), (LLMInvalidOutput, 502), (LLMIncomplete, 502)],
)
def test_failure_preserves_blueprint(client, error, status):
    headers = login(client)
    original = create(client, headers)
    path = f"/api/v1/channels/{original['id']}"

    def generate(request):
        raise error("private provider detail")

    client.app.dependency_overrides[get_llm_provider] = lambda: MockLLMProvider(
        {ChannelAnalysis: generate}
    )
    response = client.post(path + "/analyze", headers=headers)
    assert response.status_code == status
    assert "private" not in response.text
    assert client.get(path, headers=headers).json() == original


@pytest.mark.parametrize("invalid", ["mix", "pillars", "keywords", "duration", "missing"])
def test_invalid_output_is_not_saved(client, invalid):
    headers = login(client)
    original = create(client, headers)

    def generate(request):
        output = mock_analysis(request)
        if invalid == "mix":
            output["configuration"]["formats"]["top5"] = 1
        if invalid == "pillars":
            output["content_pillars"] *= 2
        if invalid == "keywords":
            output["configuration"]["seed_keywords"] = []
        if invalid == "duration":
            output["configuration"]["video_style"]["duration_target"] = 0
        if invalid == "missing":
            del output["configuration"]["hook_style"]
        return output

    client.app.dependency_overrides[get_llm_provider] = lambda: MockLLMProvider(
        {ChannelAnalysis: generate}
    )
    path = f"/api/v1/channels/{original['id']}"
    assert client.post(path + "/analyze", headers=headers).status_code == 502
    assert client.get(path, headers=headers).json() == original


def test_prompt_and_concurrent_edit(client):
    headers = login(client)
    original = create(client, headers)
    path = f"/api/v1/channels/{original['id']}"

    def generate(request):
        assert json.loads(request.prompt) == {"idea": original["idea"], "language": "pl"}
        assert "no web search" in request.instructions
        response = client.patch(path, headers=headers, json={"name": "Concurrent edit"})
        assert response.status_code == 200
        return mock_analysis(request)

    client.app.dependency_overrides[get_llm_provider] = lambda: MockLLMProvider(
        {ChannelAnalysis: generate}
    )
    assert client.post(path + "/analyze", headers=headers).status_code == 409
    saved = client.get(path, headers=headers).json()
    assert saved["name"] == "Concurrent edit"
    assert saved["blueprint"] == original["blueprint"]


def test_local_research_filters_without_network():
    from app.modules.intelligence.research import (
        CompetitorResearchQuery,
        CompetitorResearchResult,
        LocalCompetitorResearchProvider,
    )

    query = CompetitorResearchQuery(language="en", seed_keywords=["history"])
    assert LocalCompetitorResearchProvider().research(query) == []
    record = CompetitorResearchResult(
        name="History example",
        platform="youtube",
        url="https://example.com/channel",
        language="en",
        niche="History",
    )
    provider = LocalCompetitorResearchProvider([record])
    results = provider.research(query)
    assert len(results) == 1
    results[0].name = "Mutated"
    assert provider.research(query)[0].name == "History example"
    assert provider.research(query.model_copy(update={"language": "pl"})) == []


def test_ai_schema_uses_required_fields():
    from openai.lib._pydantic import to_strict_json_schema

    schema = to_strict_json_schema(ChannelAnalysis)
    assert schema["additionalProperties"] is False
    for definition in schema["$defs"].values():
        if definition.get("type") == "object":
            assert set(definition["required"]) == set(definition["properties"])
            assert all("default" not in field for field in definition["properties"].values())
    output = mock_analysis(
        LLMRequest(instructions="test", prompt='{"idea":"test","language":"pl"}')
    )
    assert ChannelAnalysis.model_validate(output).configuration.seed_keywords
