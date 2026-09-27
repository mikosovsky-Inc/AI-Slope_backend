import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.modules.channels.service import ChannelNotFound

logger = logging.getLogger("app.errors")


async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})


async def unavailable_error(request: Request, exc: Exception) -> JSONResponse:
    logger.warning("Dependency unavailable", extra={"error_type": type(exc).__name__})
    return JSONResponse(
        status_code=503,
        content={"detail": "Service temporarily unavailable"},
        headers={"Retry-After": "5", "Cache-Control": "no-store"},
    )


async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled application error", extra={"error_type": type(exc).__name__})
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


async def channel_not_found(request: Request, exc: ChannelNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Channel not found"})


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ChannelNotFound, channel_not_found)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(SQLAlchemyError, unavailable_error)
    app.add_exception_handler(RedisError, unavailable_error)
    app.add_exception_handler(Exception, unexpected_error)
