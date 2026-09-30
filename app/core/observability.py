"""Small logging hooks; no telemetry backend dependency or payload recording."""

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import StrEnum
from functools import wraps
from time import perf_counter
from typing import Any
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

context: ContextVar[dict[str, Any]] = ContextVar("log_context", default={})


class ErrorCategory(StrEnum):
    BUDGET = "budget"
    TIMEOUT = "timeout"
    DEPENDENCY = "dependency"
    PROVIDER = "provider"
    INVALID_OUTPUT = "invalid_output"
    VALIDATION = "validation"
    REVIEW = "review_required"
    PENDING = "pending"
    INTERNAL = "internal"
    RENDER = "render"


def categorize_error(exc: BaseException) -> ErrorCategory:
    import httpx
    from pydantic import ValidationError
    from redis.exceptions import RedisError
    from sqlalchemy.exc import SQLAlchemyError

    from app.modules.costs.service import BudgetExceeded
    from app.modules.render.engine import RenderError
    from app.shared.generation import (
        GenerationError,
        GenerationInvalidOutput,
        GenerationSubmissionUnknown,
    )
    from app.shared.llm import LLMError, LLMInvalidOutput
    from app.shared.storage import StorageError
    from app.shared.tts import TTSError, TTSOutcomeUnknown

    if isinstance(exc, BudgetExceeded):
        return ErrorCategory.BUDGET
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return ErrorCategory.TIMEOUT
    if isinstance(exc, (GenerationSubmissionUnknown, TTSOutcomeUnknown)):
        return ErrorCategory.REVIEW
    if isinstance(exc, RenderError):
        return ErrorCategory.RENDER
    if isinstance(exc, (SQLAlchemyError, RedisError, StorageError, OSError)):
        return ErrorCategory.DEPENDENCY
    if isinstance(exc, (LLMInvalidOutput, GenerationInvalidOutput)):
        return ErrorCategory.INVALID_OUTPUT
    if isinstance(exc, (LLMError, GenerationError, TTSError, httpx.HTTPError)):
        return ErrorCategory.PROVIDER
    if isinstance(exc, (ValueError, ValidationError)):
        return ErrorCategory.VALIDATION
    return ErrorCategory.INTERNAL


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    token = context.set(context.get() | {k: v for k, v in fields.items() if v is not None})
    try:
        yield
    finally:
        context.reset(token)


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        supplied = next((v for k, v in scope["headers"] if k.lower() == b"x-request-id"), b"")
        request_id = (
            supplied.decode("ascii")
            if re.fullmatch(rb"[A-Za-z0-9_-]{1,64}", supplied)
            else uuid4().hex
        )
        scope.setdefault("state", {})["request_id"] = request_id
        start, status = perf_counter(), 500
        error_type = None

        async def send_response(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"
                ]
                message["headers"] = [*headers, (b"x-request-id", request_id.encode())]
            await send(message)

        with log_context(request_id=request_id):
            try:
                await self.app(scope, receive, send_response)
            except Exception as exc:
                error_type = type(exc).__name__
                raise
            finally:
                route = scope.get("route")
                metrics = getattr(getattr(scope.get("app"), "state", None), "http_metrics", None)
                if metrics is not None and scope.get("path") != "/metrics":
                    metrics.observe(
                        scope["method"],
                        getattr(route, "path", "unmatched"),
                        status,
                        perf_counter() - start,
                    )
                logging.getLogger("app.http").info(
                    "HTTP request completed",
                    extra={
                        "event": "http_request",
                        "method": scope["method"],
                        "route": getattr(route, "path", "unmatched"),
                        "status_code": status,
                        "duration_ms": round((perf_counter() - start) * 1000, 3),
                        "error_type": error_type,
                        "error_category": (
                            "internal"
                            if status >= 500
                            else "authorization"
                            if status in (401, 403)
                            else "not_found"
                            if status == 404
                            else "validation"
                            if status == 422
                            else "conflict"
                            if status == 409
                            else "http_error"
                            if status >= 400
                            else None
                        ),
                    },
                )


class ObservedProvider:
    METHODS = {
        "generate",
        "synthesize",
        "generate_image",
        "generate_video",
        "get_status",
        "cancel",
        "search",
        "put",
        "download",
        "delete",
        "get_url",
    }

    def __init__(self, provider: Any, label: str) -> None:
        self._provider, self._label = provider, label

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._provider, name)
        if name not in self.METHODS or not callable(attribute):
            return attribute

        @wraps(attribute)
        def measured(*args: Any, **kwargs: Any) -> Any:
            start, category, error_type = perf_counter(), None, None
            try:
                return attribute(*args, **kwargs)
            except Exception as exc:
                category, error_type = categorize_error(exc), type(exc).__name__
                raise
            finally:
                logging.getLogger("app.provider").info(
                    "Provider operation completed",
                    extra={
                        "event": "provider_call",
                        "provider": self._label,
                        "provider_type": type(self._provider).__name__,
                        "operation": name,
                        "duration_ms": round((perf_counter() - start) * 1000, 3),
                        "error_category": category,
                        "error_type": error_type,
                        "outcome": "failed" if error_type else "succeeded",
                    },
                )

        return measured
