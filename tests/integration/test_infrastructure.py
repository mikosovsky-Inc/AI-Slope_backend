import os
from uuid import uuid4

import pytest
from redis import Redis
from sqlmodel import Session, create_engine

from app.services.health import check_dependencies


def test_postgres_and_redis_connectivity() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    redis_url = os.getenv("TEST_REDIS_URL")
    if not database_url or not redis_url:
        pytest.skip("Set TEST_DATABASE_URL and TEST_REDIS_URL")
    engine = create_engine(database_url)
    redis = Redis.from_url(redis_url, socket_timeout=2, socket_connect_timeout=2)
    key = f"foundation-test:{uuid4()}"
    try:
        with Session(engine) as db:
            check_dependencies(db, redis)
        redis.set(key, "ready", ex=30)
        assert redis.get(key) == b"ready"
        redis.delete(key)
    finally:
        redis.close()
        engine.dispose()
