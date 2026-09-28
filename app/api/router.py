from fastapi import APIRouter

from app.api.routes.auth import router as auth_router
from app.api.routes.channels import router as channels_router
from app.api.routes.director import router as director_router
from app.api.routes.ideas import router as ideas_router
from app.api.routes.research import router as research_router
from app.api.routes.scripts import router as scripts_router

router = APIRouter(prefix="/api/v1")
router.include_router(auth_router)

router.include_router(channels_router)

router.include_router(ideas_router)

router.include_router(scripts_router)

router.include_router(research_router)

router.include_router(director_router)
