from sqlalchemy import event, func
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.modules.competitors.dependencies import get_competitor_research_provider
from app.modules.competitors.models import Competitor, CompetitorContent
from app.modules.intelligence.research import (
    CompetitorResearchResult,
    LocalCompetitorResearchProvider,
)


def test_competitor_storage_rollback_and_cascade(pg_client, postgres_engine):
    credentials = {"email": "competitors@example.com", "password": "long-test-password"}
    pg_client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = pg_client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    created = pg_client.post(
        "/api/v1/channels",
        headers=headers,
        json={"idea": "Short stories and interesting facts", "language": "en"},
    )
    created.raise_for_status()
    path = "/api/v1/channels/" + created.json()["id"]
    pg_client.post(path + "/analyze", headers=headers).raise_for_status()
    item = CompetitorResearchResult(
        name="Local sample",
        platform="youtube",
        url="https://example.com/sample",
        language="en",
        niche="facts",
        example_titles=["Title"],
    )
    pg_client.app.dependency_overrides[get_competitor_research_provider] = lambda: (
        LocalCompetitorResearchProvider([item])
    )
    for _ in range(2):
        result = pg_client.post(path + "/competitor-research", headers=headers)
        assert result.status_code == 200, result.text
    saved = pg_client.get(path + "/competitors", headers=headers).json()
    assert saved["total"] == 1
    assert saved["items"][0]["example_titles"] == ["Title"]

    def fail_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO competitor_contents"):
            raise SQLAlchemyError("Simulated write failure")

    event.listen(postgres_engine, "before_cursor_execute", fail_insert)
    try:
        item.name = "Should roll back"
        item.example_titles = ["Replacement"]
        assert pg_client.post(path + "/competitor-research", headers=headers).status_code == 503
    finally:
        event.remove(postgres_engine, "before_cursor_execute", fail_insert)
    assert pg_client.get(path + "/competitors", headers=headers).json() == saved
    assert pg_client.delete(path, headers=headers).status_code == 204
    with Session(postgres_engine) as db:
        for model in [Competitor, CompetitorContent]:
            assert db.exec(select(func.count()).select_from(model)).one() == 0
