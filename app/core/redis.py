from fastapi import Request
from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.core.config import Settings


def create_redis_client(settings: Settings) -> Redis:
    return Redis.from_url(
        str(settings.redis_url),
        decode_responses=True,
        socket_connect_timeout=settings.redis_timeout_seconds,
        socket_timeout=settings.redis_timeout_seconds,
        retry=Retry(NoBackoff(), 0),
    )


def get_redis(request: Request) -> Redis:
    return request.app.state.redis
