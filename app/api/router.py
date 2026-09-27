from fastapi import APIRouter

from app.api.routes.auth import router as auth_router
from app.api.routes.channels import router as channels_router

router = APIRouter(prefix="/api/v1")
router.include_router(auth_router)

router.include_router(channels_router)
