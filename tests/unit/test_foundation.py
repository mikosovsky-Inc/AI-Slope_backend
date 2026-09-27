import json
import logging
from io import StringIO
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError

from app.core.config import Settings
from app.core.errors import register_error_handlers
from app.core.logging import JSONFormatter
from app.core.redis import get_redis
from main import app


def test_health_is_independent_of_dependencies(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["cache-control"] == "no-store"


def test_ready_checks_database_and_redis(client):
    redis = Mock(spec=Redis)
    redis.ping.return_value = True
    app.dependency_overrides[get_redis] = lambda: redis
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    redis.ping.assert_called_once_with()


def test_readiness_recovers_when_redis_returns(client):
    redis = Mock(spec=Redis)
    redis.ping.side_effect = [RedisConnectionError("redis://user:secret@host"), True]
    app.dependency_overrides[get_redis] = lambda: redis
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": "Service temporarily unavailable"}
    assert "secret" not in response.text
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200


@pytest.mark.parametrize(
    "exception, status, detail",
    [
        (
            OperationalError("SELECT secret", {}, Exception("password=secret")),
            503,
            "Service temporarily unavailable",
        ),
        (RedisConnectionError("password=secret"), 503, "Service temporarily unavailable"),
        (RuntimeError("password=secret"), 500, "Internal server error"),
    ],
)
def test_errors_do_not_leak_secrets(exception, status, detail):
    isolated_app = FastAPI()
    register_error_handlers(isolated_app)

    @isolated_app.get("/failure")
    def fail() -> None:
        raise exception

    logger = logging.getLogger("app.errors")
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    try:
        with TestClient(isolated_app, raise_server_exceptions=False) as client:
            response = client.get("/failure")
        assert response.status_code == status
        assert response.json() == {"detail": detail}
        assert "secret" not in stream.getvalue()
        assert json.loads(stream.getvalue())["error_type"] == type(exception).__name__
    finally:
        logger.removeHandler(handler)


def test_json_logging():
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "API started", (), None)
    entry = json.loads(JSONFormatter().format(record))
    assert entry["message"] == "API started"
    assert entry["level"] == "INFO"
    assert entry["timestamp"].endswith("+00:00")


def test_foundation_settings_validate_modes_and_timeouts(settings):
    values = settings.model_dump()
    for override in [
        {"external_providers_mode": "unknown"},
        {"redis_timeout_seconds": 0},
        {"log_level": "UNKNOWN"},
        {"database_connect_timeout_seconds": 0},
    ]:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, **(values | override))
