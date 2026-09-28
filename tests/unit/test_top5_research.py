from uuid import UUID

import pytest
from sqlmodel import select

from app.integrations.llm.factory import get_llm_provider
from app.integrations.llm.mock import MockLLMProvider
from app.modules.research.mock import mock_facts, mock_queries, mock_top5
from app.modules.research.provider import LocalResearchProvider, get_research_provider
from app.modules.research.schemas import ExtractedFacts, ResearchQueries, Top5Plan
from app.modules.videos.models import Video, VideoScript, VideoStatus


def test_research_and_grounded_script(client, db, top5_factory, source_documents):
    headers, path, video_id, _ = top5_factory(client)
    client.app.dependency_overrides[get_research_provider] = lambda: LocalResearchProvider(
        source_documents
    )
    assert client.post(path + "/top5-script/generate", headers=headers).status_code == 409
    response = client.post(path + "/research", headers=headers)
    assert response.status_code == 200, response.text
    research = response.json()
    assert len(research["documents"]) == 2
    assert len(research["facts"]) == 6
    assert client.post(path + "/research", headers=headers).json() == research
    assert db.get(Video, UUID(video_id)).status == VideoStatus.RESEARCHED
    response = client.post(path + "/top5-script/generate", headers=headers)
    assert response.status_code == 200, response.text
    script = response.json()
    assert len(script["scenes"]) == 6 and len(script["citations"]) == 5
    for citation in script["citations"]:
        scene = script["scenes"][citation["position"] - 1]
        assert scene["narration"] == citation["fact"]["statement"]
        assert citation["fact"]["source_url"].startswith("https://example.com/fixture/")
    assert client.get(path + "/script", headers=headers).json() == script
    assert client.post(path + "/top5-script/generate", headers=headers).json() == script
    assert db.get(Video, UUID(video_id)).status == VideoStatus.SCRIPT_READY


@pytest.mark.parametrize(
    "mode", ["empty", "one_source", "low_confidence", "invented", "wrong_document"]
)
def test_insufficient_or_invalid_evidence_stops(client, db, top5_factory, source_documents, mode):
    headers, path, video_id, _ = top5_factory(client)
    docs = (
        []
        if mode == "empty"
        else source_documents[:1]
        if mode == "one_source"
        else source_documents
    )
    client.app.dependency_overrides[get_research_provider] = lambda: LocalResearchProvider(docs)

    def extract(request):
        data = mock_facts(request)
        if mode == "low_confidence":
            for fact in data["facts"]:
                fact["confidence"] = 0.1
        if mode == "invented":
            data["facts"][0]["quote"] = "Unsupported statement"
        if mode == "wrong_document":
            data["facts"][0]["document_id"] = "unknown"
        return data

    client.app.dependency_overrides[get_llm_provider] = lambda: MockLLMProvider(
        {ResearchQueries: mock_queries, ExtractedFacts: extract}
    )
    response = client.post(path + "/research", headers=headers)
    assert response.status_code == (502 if mode in ["invented", "wrong_document"] else 422), (
        response.text
    )
    assert db.get(Video, UUID(video_id)).status == VideoStatus.FAILED
    assert not db.exec(select(VideoScript)).all()
    assert client.post(path + "/top5-script/generate", headers=headers).status_code == 409
    if mode == "one_source":
        assert len(client.get(path + "/research", headers=headers).json()["documents"]) == 1


@pytest.mark.parametrize("bad", ["unknown", "duplicate", "duration"])
def test_top5_rejects_invalid_plan(client, db, top5_factory, source_documents, bad):
    headers, path, video_id, _ = top5_factory(client)
    client.app.dependency_overrides[get_research_provider] = lambda: LocalResearchProvider(
        source_documents
    )
    client.post(path + "/research", headers=headers).raise_for_status()

    def plan(request):
        data = mock_top5(request)
        if bad == "unknown":
            data["items"][0]["fact_id"] = "unknown"
        if bad == "duplicate":
            data["items"][1]["fact_id"] = data["items"][0]["fact_id"]
        if bad == "duration":
            data["items"][0]["duration"] = 100
        return data

    client.app.dependency_overrides[get_llm_provider] = lambda: MockLLMProvider({Top5Plan: plan})
    assert client.post(path + "/top5-script/generate", headers=headers).status_code == 502
    assert db.get(Video, UUID(video_id)).status == VideoStatus.FAILED
    assert not db.exec(select(VideoScript)).all()


def test_research_ownership(client, top5_factory):
    headers, path, _, _ = top5_factory(client)
    credentials = {"email": "other-research@example.com", "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    for method, suffix in [
        ("post", "/research"),
        ("get", "/research"),
        ("post", "/top5-script/generate"),
    ]:
        assert getattr(client, method)(path + suffix).status_code == 401
        assert getattr(client, method)(path + suffix, headers=other).status_code == 404
