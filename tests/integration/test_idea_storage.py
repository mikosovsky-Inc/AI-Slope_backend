from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import event, func
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.integrations.llm.factory import get_llm_provider
from app.integrations.llm.mock import MockLLMProvider
from app.modules.ideas.mock import mock_ideas
from app.modules.ideas.models import ContentIdea
from app.modules.ideas.schemas import GeneratedIdeas


def prepare(client):
    credentials = {"email": "ideas-db@example.com", "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    channel = client.post(
        "/api/v1/channels",
        headers=headers,
        json={"idea": "History facts and stories", "language": "en"},
    ).json()
    path = "/api/v1/channels/" + channel["id"]
    client.post(path + "/analyze", headers=headers).raise_for_status()
    return headers, path


def test_idea_batch_rollback_and_cascade(pg_client, postgres_engine):
    headers, path = prepare(pg_client)
    result = pg_client.post(path + "/ideas/generate", headers=headers)
    assert result.status_code == 201, result.text
    item = result.json()["items"][0]
    assert (
        pg_client.post("/api/v1/ideas/" + item["id"] + "/approve", headers=headers).status_code
        == 200
    )
    saved = pg_client.get(path + "/ideas", headers=headers).json()

    def fail_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO content_ideas"):
            raise SQLAlchemyError("Simulated storage failure")

    event.listen(postgres_engine, "before_cursor_execute", fail_insert)
    try:
        assert pg_client.post(path + "/ideas/generate", headers=headers).status_code == 503
    finally:
        event.remove(postgres_engine, "before_cursor_execute", fail_insert)
    assert pg_client.get(path + "/ideas", headers=headers).json() == saved
    assert pg_client.delete(path, headers=headers).status_code == 204
    with Session(postgres_engine) as db:
        assert db.exec(select(func.count()).select_from(ContentIdea)).one() == 0


def test_concurrent_generations_have_one_winner(pg_client):
    headers, path = prepare(pg_client)
    barrier = Barrier(2)

    def generate(request):
        barrier.wait(timeout=10)
        return mock_ideas(request)

    pg_client.app.dependency_overrides[get_llm_provider] = lambda: MockLLMProvider(
        {GeneratedIdeas: generate}
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(pg_client.post, path + "/ideas/generate", headers=headers)
            for _ in range(2)
        ]
        responses = [future.result(timeout=20) for future in futures]
    assert sorted(r.status_code for r in responses) == [201, 409]
    assert pg_client.get(path + "/ideas", headers=headers).json()["total"] == 10
