from fastapi import APIRouter

from app.api.v1.activities import router as activities_router
from app.api.v1.companies import router as companies_router
from app.api.v1.episodes import router as episodes_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.projects import router as projects_router
from app.api.v1.questions import router as questions_router
from app.api.v1.recommendations import router as recommendations_router
from app.api.v1.selections import router as selections_router
from app.api.v1.users import router as users_router

router = APIRouter(prefix="/api/v1")
router.include_router(activities_router)
router.include_router(episodes_router)
router.include_router(users_router)
router.include_router(companies_router)
router.include_router(projects_router)
router.include_router(questions_router)
router.include_router(jobs_router)
router.include_router(recommendations_router)
router.include_router(selections_router)
