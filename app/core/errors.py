import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.modules.channels.service import ChannelNotFound
from app.modules.competitors.service import (
    ResearchConflict,
    ResearchInvalidOutput,
    ResearchNotReady,
    ResearchUnavailable,
)
from app.modules.director.service import (
    DirectorBudgetExceeded,
    DirectorNotReady,
    DirectorPlanNotFound,
)
from app.modules.ideas.service import IdeaConflict, IdeaNotFound, IdeasNotReady
from app.modules.intelligence.service import AnalysisConflict
from app.modules.research.provider import ResearchUnavailable as FactResearchUnavailable
from app.modules.research.service import InsufficientResearch, ResearchStateConflict
from app.modules.scripts.service import ScriptConflict, ScriptNotFound
from app.modules.videos.service import VideoRequiresApprovedIdea
from app.shared.llm import LLMError, LLMRefusal, LLMUnavailable

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


async def analysis_conflict(request: Request, exc: AnalysisConflict) -> JSONResponse:
    return JSONResponse(
        status_code=409, content={"detail": "Channel changed during analysis; retry"}
    )


async def llm_error(request: Request, exc: LLMError) -> JSONResponse:
    logger.warning("LLM operation failed", extra={"error_type": type(exc).__name__})
    status = 503 if isinstance(exc, LLMUnavailable) else 422 if isinstance(exc, LLMRefusal) else 502
    return JSONResponse(
        status_code=status,
        content={"detail": "LLM operation could not complete"},
        headers={"Cache-Control": "no-store"},
    )


async def research_error(request: Request, exc: Exception) -> JSONResponse:
    errors = {
        ResearchNotReady: (409, "Analyze the channel or set seed keywords first"),
        ResearchConflict: (409, "Channel changed during research; retry"),
        ResearchInvalidOutput: (502, "Invalid competitor research result"),
        ResearchUnavailable: (503, "Competitor research unavailable"),
    }
    status, message = errors[type(exc)]
    return JSONResponse(
        status_code=status, content={"detail": message}, headers={"Cache-Control": "no-store"}
    )


async def idea_error(request: Request, exc: Exception) -> JSONResponse:
    status, message = {
        IdeasNotReady: (409, "Analyze the channel or configure audience and pillars first"),
        IdeaNotFound: (404, "Idea not found"),
        IdeaConflict: (409, "Idea is used or channel changed during generation"),
    }[type(exc)]
    return JSONResponse(status_code=status, content={"detail": message})


async def video_not_ready(request: Request, exc: VideoRequiresApprovedIdea) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": "An approved idea is required"})


async def script_error(request: Request, exc: Exception) -> JSONResponse:
    status, message = (
        (404, "Video or script not found")
        if isinstance(exc, ScriptNotFound)
        else (409, "Requires an idle STORY video; generation may already be running or have failed")
    )
    return JSONResponse(status_code=status, content={"detail": message})


async def fact_research_error(request: Request, exc: Exception) -> JSONResponse:
    status, message = {
        InsufficientResearch: (422, "Insufficient sourced facts; generation stopped"),
        ResearchStateConflict: (409, "TOP5 workflow is not ready for this operation"),
        FactResearchUnavailable: (503, "Research provider unavailable"),
    }[type(exc)]
    return JSONResponse(status_code=status, content={"detail": message})


async def director_error(request: Request, exc: Exception) -> JSONResponse:
    status, message = {
        DirectorNotReady: (409, "A complete SCRIPT_READY video is required"),
        DirectorBudgetExceeded: (409, "Visual budget cannot cover the image-only estimate"),
        DirectorPlanNotFound: (404, "Director plan not found"),
    }[type(exc)]
    return JSONResponse(status_code=status, content={"detail": message})


def register_error_handlers(app: FastAPI) -> None:
    for error in (DirectorNotReady, DirectorBudgetExceeded, DirectorPlanNotFound):
        app.add_exception_handler(error, director_error)
    for error in (InsufficientResearch, ResearchStateConflict, FactResearchUnavailable):
        app.add_exception_handler(error, fact_research_error)
    app.add_exception_handler(ScriptNotFound, script_error)
    app.add_exception_handler(ScriptConflict, script_error)
    app.add_exception_handler(VideoRequiresApprovedIdea, video_not_ready)
    for error in (IdeasNotReady, IdeaNotFound, IdeaConflict):
        app.add_exception_handler(error, idea_error)
    for error in (ResearchNotReady, ResearchConflict, ResearchInvalidOutput, ResearchUnavailable):
        app.add_exception_handler(error, research_error)
    app.add_exception_handler(AnalysisConflict, analysis_conflict)
    app.add_exception_handler(LLMError, llm_error)
    app.add_exception_handler(ChannelNotFound, channel_not_found)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(SQLAlchemyError, unavailable_error)
    app.add_exception_handler(RedisError, unavailable_error)
    app.add_exception_handler(Exception, unexpected_error)
