import io
import json
import logging
from uuid import UUID

import pytest
from sqlmodel import select

from app.core.logging import JSONFormatter
from app.core.observability import ObservedProvider, context, log_context
from app.modules.tasks.models import Task, TaskKind
from app.workers.runner import run_task


def test_request_id_success_errors_and_cors(client):
    for path in ("/health", "/missing?secret=hidden", "/api/v1/auth/me"):
        response = client.get(path, headers={"X-Request-ID": "test-request-123"})
        assert response.headers["X-Request-ID"] == "test-request-123"
    response = client.get("/health", headers={"X-Request-ID": "bad request id"})
    assert len(response.headers["X-Request-ID"]) == 32
    cors = client.options(
        "/api/v1/dashboard",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Request-ID, Idempotency-Key, Authorization",
        },
    )
    assert cors.status_code == 200
    assert (
        "X-Request-ID"
        in client.get("/health", headers={"Origin": "http://localhost:3000"}).headers[
            "access-control-expose-headers"
        ]
    )
    assert context.get() == {}


def test_formatter_safe_context_and_provider_latency():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())
    logger = logging.getLogger("app.provider")
    logger.addHandler(handler)
    old_level = logger.level
    logger.setLevel(logging.INFO)

    class Broken:
        def generate(self, prompt):
            raise TimeoutError("SECRET provider response")

    try:
        with log_context(request_id="test", task_id="job", video_id="video"):
            with pytest.raises(TimeoutError):
                ObservedProvider(Broken(), "test").generate("SECRET prompt")
        entry = json.loads(stream.getvalue())
        assert entry["request_id"] == "test" and entry["task_id"] == "job"
        assert entry["duration_ms"] >= 0 and entry["error_category"] == "timeout"
        assert "SECRET" not in stream.getvalue()
        assert context.get() == {}
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)


def test_job_correlation_survives_worker_and_child(client, db, settings, story_factory):
    h, path, vid, _ = story_factory(client)
    settings.tasks_eager = False
    response = client.post(path + "/script/generate", headers=h | {"X-Request-ID": "origin-123"})
    response.raise_for_status()
    identifier = UUID(response.json()["id"])
    task = db.get(Task, identifier)
    task.parameters = {"workflow": True}
    db.add(task)
    db.commit()
    run_task(db.get_bind(), str(identifier), settings=settings)
    db.expire_all()
    assert db.get(Task, identifier).request_id == "origin-123"
    child = db.exec(select(Task).where(Task.kind == TaskKind.DIRECT)).one()
    assert child.request_id == "origin-123"
    assert context.get() == {}


def test_admin_jobs_filters_pagination_and_private_fields(client, db, settings, story_factory):
    h, path, vid, _ = story_factory(client)
    settings.tasks_eager = False
    task = client.post(path + "/script/generate", headers=h).json()
    row = db.get(Task, UUID(task["id"]))
    row.parameters = {"secret": "must-not-appear"}
    row.result = {"private": "must-not-appear"}
    db.add(row)
    db.commit()
    assert client.get("/api/v1/admin/jobs").status_code == 401
    response = client.get("/api/v1/admin/jobs?status=queued&limit=1", headers=h)
    response.raise_for_status()
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["queue"] == "content"
    assert "must-not-appear" not in response.text
    assert client.get("/api/v1/admin/jobs?status=failed", headers=h).json()["items"] == []
    assert client.get("/api/v1/admin/jobs?offset=1", headers=h).json()["items"] == []
    assert client.get("/api/v1/admin/jobs?limit=101", headers=h).status_code == 422
    credentials = {"email": "regular-observer@example.com", "password": "long-test-password"}
    client.post("/api/v1/auth/register", json=credentials).raise_for_status()
    token = client.post("/api/v1/auth/login", json=credentials).json()["access_token"]
    assert (
        client.get("/api/v1/admin/jobs", headers={"Authorization": "Bearer " + token}).status_code
        == 403
    )


def test_unhandled_500_keeps_request_id_and_hides_exception():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.errors import register_error_handlers
    from app.core.observability import RequestLoggingMiddleware

    app = FastAPI()
    register_error_handlers(app)
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/fail")
    def fail():
        raise RuntimeError("PRIVATE DATA")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/fail", headers={"X-Request-ID": "failure-123"})
    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "failure-123"
    assert "PRIVATE DATA" not in response.text
    assert context.get() == {}


def test_concurrent_requests_keep_separate_contexts():
    import asyncio

    import httpx
    from fastapi import FastAPI

    from app.core.observability import RequestLoggingMiddleware

    app = FastAPI()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/context")
    async def current_context():
        first = context.get()["request_id"]
        await asyncio.sleep(0.01)
        return {"first": first, "last": context.get()["request_id"]}

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            responses = await asyncio.gather(
                *[
                    client.get("/context", headers={"X-Request-ID": f"parallel-{i}"})
                    for i in range(8)
                ]
            )
        for i, response in enumerate(responses):
            assert response.json() == {"first": f"parallel-{i}", "last": f"parallel-{i}"}
        assert context.get() == {}

    asyncio.run(run())


def test_worker_failure_category_is_persisted_and_retry_clears_it(
    client, db, settings, story_factory, monkeypatch
):
    from app.modules.costs.service import BudgetExceeded

    h, path, _, _ = story_factory(client)
    settings.tasks_eager = False
    result = client.post(path + "/script/generate", headers=h).json()
    identifier = UUID(result["id"])

    class FailingProvider:
        def generate(self, request, response_model):
            raise BudgetExceeded("test")

        def close(self):
            pass

    monkeypatch.setattr("app.workers.operations.create_llm_provider", lambda _: FailingProvider())
    run_task(db.get_bind(), str(identifier), settings=settings)
    db.expire_all()
    task = db.get(Task, identifier)
    assert task.error_category == "budget"
    assert task.error == "budget_exceeded"
    response = client.post(path + "/retry", headers=h, json={"task_id": str(identifier)})
    assert response.status_code == 202
    assert response.json()["error_category"] is None
