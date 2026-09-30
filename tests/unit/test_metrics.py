from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from prometheus_client.parser import text_string_to_metric_families
from pydantic import SecretStr, ValidationError
from sqlmodel import select

from app.core.config import Settings
from app.models.user import User
from app.modules.observability.metrics import HTTPMetrics, exposition
from app.modules.tasks.models import Task, TaskKind, TaskStatus

TOKEN = "metrics-test-" + "x" * 48


@pytest.fixture(autouse=True)
def enable_metrics(settings):
    settings.metrics_enabled = True
    settings.metrics_token = SecretStr(TOKEN)


def samples(response):
    response.raise_for_status()
    return [s for family in text_string_to_metric_families(response.text) for s in family.samples]


def test_metrics_disabled_and_protected(client, settings):
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": TOKEN}).status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer wrong"}).status_code == 401
    response = client.get("/metrics", headers={"Authorization": "Bearer " + TOKEN})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["cache-control"] == "no-store"
    assert TOKEN not in response.text
    settings.metrics_enabled = False
    assert client.get("/metrics", headers={"Authorization": "Bearer " + TOKEN}).status_code == 404


def test_routes_and_methods_are_bounded_without_private_values(client):
    for number in range(20):
        client.request("PRIVATE" + str(number), f"/unknown/private-{number}?token=secret")
        client.get(f"/api/v1/videos/00000000-0000-0000-0000-{number:012d}")
    result = samples(client.get("/metrics", headers={"Authorization": "Bearer " + TOKEN}))
    requests = [s for s in result if s.name == "ai_slop_http_requests_total"]
    assert any(
        s.labels["method"] == "OTHER" and s.labels["route"] == "unmatched" and s.value == 20
        for s in requests
    )
    assert all(
        "private" not in str(s.labels)
        and "secret" not in str(s.labels)
        and "00000000" not in str(s.labels)
        for s in result
    )
    assert all(s.labels.get("route") != "/metrics" for s in result)
    assert any(s.name == "ai_slop_http_request_duration_seconds_count" for s in result)


def test_durable_task_snapshot_and_overdue_leases(client, db, story_factory):
    _, _, identifier, _ = story_factory(client)
    owner = db.exec(select(User)).one()
    for index, status in enumerate(
        (TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.NEEDS_REVIEW)
    ):
        db.add(
            Task(
                owner_id=owner.id,
                video_id=UUID(identifier),
                kind=TaskKind.AUDIO,
                status=status,
                idempotency_key=f"metrics-{index}",
                available_at=datetime.now(UTC) - timedelta(seconds=120),
                error_category="private-provider-response"
                if status == TaskStatus.NEEDS_REVIEW
                else None,
                parameters={"secret": "never-export-this"},
            )
        )
    db.commit()
    result = samples(client.get("/metrics", headers={"Authorization": "Bearer " + TOKEN}))
    assert (
        next(
            s.value
            for s in result
            if s.name == "ai_slop_tasks"
            and s.labels == {"kind": "audio", "queue": "audio", "status": "needs_review"}
        )
        == 1
    )
    assert (
        next(
            s.value
            for s in result
            if s.name == "ai_slop_task_overdue_seconds"
            and s.labels == {"kind": "audio", "queue": "audio", "status": "running"}
        )
        >= 120
    )
    assert (
        next(
            s.value
            for s in result
            if s.name == "ai_slop_task_errors"
            and s.labels == {"kind": "audio", "category": "other"}
        )
        == 1
    )
    assert "private-provider-response" not in str(result)
    assert "never-export-this" not in str(result)
    # A fresh API registry still sees all workers' persisted task states.
    assert b'ai_slop_tasks{kind="audio",queue="audio",status="needs_review"} 1.0' in exposition(
        db, HTTPMetrics()
    )


def test_dependency_failure_returns_503_not_false_zeroes(client, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    def fail(*args):
        raise SQLAlchemyError("private-db-error")

    monkeypatch.setattr("app.modules.observability.metrics.database_metrics", fail)
    result = client.get("/metrics", headers={"Authorization": "Bearer " + TOKEN})
    assert result.status_code == 503
    assert "private-db-error" not in result.text


@pytest.mark.parametrize("token", [None, "short", "x" * 40 + "\n"])
def test_enabled_metrics_require_strong_header_safe_secret(settings, token):
    data = settings.model_dump() | {"metrics_token": token}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **data)


def test_metrics_config_secret_is_hidden(settings):
    validated = Settings(_env_file=None, **settings.model_dump())
    assert TOKEN not in repr(validated)


def test_unhandled_errors_are_counted_without_exception_payload():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.core.observability import RequestLoggingMiddleware

    app = FastAPI()
    app.state.http_metrics = HTTPMetrics()
    app.add_middleware(RequestLoggingMiddleware)

    @app.get("/fail/{identifier}")
    def fail(identifier: str):
        raise RuntimeError("private-provider-body")

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/fail/private-id").status_code == 500
    registry = app.state.http_metrics.registry
    assert (
        registry.get_sample_value(
            "ai_slop_http_requests_total",
            {"method": "GET", "route": "/fail/{identifier}", "status": "500"},
        )
        == 1
    )
    assert "private" not in str(list(registry.collect()))
