from fastapi import APIRouter

from app.api.schemas.common import ApiErrorResponse
from app.api.v1.account import router as account_router
from app.api.v1.activities import router as activities_router
from app.api.v1.auth import router as auth_router
from app.api.v1.companies import router as companies_router
from app.api.v1.episodes import router as episodes_router
from app.api.v1.inferences import router as inferences_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.preferences import router as preferences_router
from app.api.v1.projects import router as projects_router
from app.api.v1.questions import router as questions_router
from app.api.v1.recommendations import router as recommendations_router
from app.api.v1.selections import router as selections_router
from app.api.v1.users import router as users_router

API_ERROR_RESPONSES = {
    401: {"model": ApiErrorResponse, "description": "인증 정보가 없거나 유효하지 않습니다."},
    404: {"model": ApiErrorResponse, "description": "요청한 리소스를 찾을 수 없습니다."},
    409: {
        "model": ApiErrorResponse,
        "description": "현재 상태 또는 멱등성 키가 요청과 충돌합니다.",
    },
    412: {"model": ApiErrorResponse, "description": "If-Match version이 현재 리소스와 다릅니다."},
    422: {"model": ApiErrorResponse, "description": "요청 값 또는 완료 조건이 유효하지 않습니다."},
    503: {"model": ApiErrorResponse, "description": "현재 구성되지 않은 실행 정책이 필요합니다."},
}


router = APIRouter(prefix="/api/v1", responses=API_ERROR_RESPONSES)
router.include_router(auth_router)
router.include_router(account_router)
router.include_router(activities_router)
router.include_router(episodes_router)
router.include_router(users_router)
router.include_router(companies_router)
router.include_router(projects_router)
router.include_router(questions_router)
router.include_router(jobs_router)
router.include_router(inferences_router)
router.include_router(notifications_router)
router.include_router(recommendations_router)
router.include_router(selections_router)
router.include_router(preferences_router)
