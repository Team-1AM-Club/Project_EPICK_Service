from fastapi import APIRouter

from app.api.v1.activities import router as activities_router
from app.api.v1.episodes import router as episodes_router

router = APIRouter(prefix="/api/v1")
router.include_router(activities_router)
router.include_router(episodes_router)
