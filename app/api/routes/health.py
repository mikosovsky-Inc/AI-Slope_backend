from typing import Annotated

from fastapi import APIRouter, Depends, Response
from redis import Redis

from app.api.dependencies import DbSession
from app.core.redis import get_redis
from app.schemas.health import HealthResponse
from app.services.health import check_dependencies

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(response: Response) -> HealthResponse:
    response.headers["Cache-Control"] = "no-store"
    return HealthResponse()


@router.get("/ready", response_model=HealthResponse)
def readiness(
    db: DbSession, redis: Annotated[Redis, Depends(get_redis)], response: Response
) -> HealthResponse:
    check_dependencies(db, redis)
    response.headers["Cache-Control"] = "no-store"
    return HealthResponse()
