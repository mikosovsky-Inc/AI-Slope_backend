from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp

from app.api.router import router
from app.core.config import get_settings
from app.db.session import get_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings()
    yield
    get_engine().dispose()


app = FastAPI(title="AI-Slop API", version="0.1.0", lifespan=lifespan)
app.include_router(router)


class APICORSMiddleware(CORSMiddleware):
    def __init__(self, app: ASGIApp):
        super().__init__(
            app,
            allow_origins=get_settings().cors_allowed_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )


app.add_middleware(APICORSMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI's default response can echo plaintext passwords from invalid input.
    errors = [
        {"loc": error["loc"], "msg": error["msg"], "type": error["type"]} for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": errors})
