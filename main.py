import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp

from app.api.router import router
from app.api.routes.health import router as health_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.redis import create_redis_client
from app.db.session import get_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = logging.getLogger("app.lifecycle")
    app.state.redis = create_redis_client(settings)
    logger.info("API started")
    try:
        yield
    finally:
        app.state.redis.close()
        get_engine().dispose()
        logger.info("API stopped")


app = FastAPI(title="AI-Slop API", version="0.1.0", lifespan=lifespan)
app.include_router(router)
app.include_router(health_router)
register_error_handlers(app)


class APICORSMiddleware(CORSMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(
            app,
            allow_origins=get_settings().cors_allowed_origins,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )


app.add_middleware(APICORSMiddleware)
