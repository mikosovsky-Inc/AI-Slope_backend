from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError

from app.integrations.llm.factory import get_llm_provider
from app.integrations.llm.mock import MockLLMProvider
from app.modules.intelligence.mock import mock_analysis
from app.modules.intelligence.schemas import ChannelAnalysis


def prepare(client):
    credentials = {"email": "intelligence@example.com", "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post(
        "/api/v1/channels",
        headers=headers,
        json={
            "idea": "Historical mysteries and unusual discoveries",
            "language": "en",
        },
    )
    response.raise_for_status()
    return headers, f"/api/v1/channels/{response.json()['id']}"


def test_postgres_analysis_persists_and_rolls_back(pg_client, postgres_engine):
    headers, path = prepare(pg_client)
    response = pg_client.post(path + "/analyze", headers=headers)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["blueprint"]["configuration"]["seed_keywords"]
    assert pg_client.get(path, headers=headers).json() == saved

    def fail_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO content_pillars"):
            raise SQLAlchemyError("Simulated storage failure")

    event.listen(postgres_engine, "before_cursor_execute", fail_insert)
    try:
        assert pg_client.post(path + "/analyze", headers=headers).status_code == 503
    finally:
        event.remove(postgres_engine, "before_cursor_execute", fail_insert)
    assert pg_client.get(path, headers=headers).json() == saved


def test_concurrent_analysis_does_not_overwrite(pg_client):
    headers, path = prepare(pg_client)
    barrier = Barrier(2)

    def generate(request):
        barrier.wait(timeout=10)
        return mock_analysis(request)

    pg_client.app.dependency_overrides[get_llm_provider] = lambda: MockLLMProvider(
        {ChannelAnalysis: generate}
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(pg_client.post, path + "/analyze", headers=headers) for _ in range(2)
        ]
        responses = [future.result(timeout=20) for future in futures]
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json() for response in responses if response.status_code == 200)
    assert pg_client.get(path, headers=headers).json() == winner
