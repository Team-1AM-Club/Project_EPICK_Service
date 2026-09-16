from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.errors import ApiFieldError, ApiProblem, ResourceNotFoundError
from app.api.middleware import correlation_id_middleware, get_correlation_id
from app.api.schemas.common import ApiErrorBody, ApiErrorResponse, ErrorFieldResponse
from app.api.v1.router import router as api_v1_router
from app.db.session import engine

OPENAPI_TAGS = [
    {"name": "health", "description": "프로세스와 데이터베이스 준비 상태를 확인합니다."},
    {"name": "activities", "description": "사용자 Activity와 immutable version을 관리합니다."},
    {
        "name": "episodes",
        "description": "Activity에 속한 Episode와 immutable version을 관리합니다.",
    },
    {"name": "users", "description": "현재 사용자와 홈 read model을 조회합니다."},
    {"name": "companies", "description": "이미 식별된 Company 및 채용공고 catalog를 조회합니다."},
    {
        "name": "application-projects",
        "description": "지원 Project와 연결된 JobPosting을 관리합니다.",
    },
    {"name": "project-questions", "description": "지원 문항과 문항 version을 관리합니다."},
    {
        "name": "recommendations",
        "description": "snapshot에 고정된 합성 추천 결과를 조회합니다.",
    },
    {"name": "material-selections", "description": "추천 후보의 소재 선택을 관리합니다."},
    {
        "name": "jobs",
        "description": "비동기 Job 상태와 사용자 action 수락 상태를 조회·제출합니다.",
    },
    {"name": "inferences", "description": "추론 및 중복 suggestion을 검토합니다."},
    {"name": "notifications", "description": "사용자 알림을 조회하고 상태를 변경합니다."},
    {
        "name": "preferences-and-privacy",
        "description": "설정, 제외, 동의, 보존 상태와 피드백을 관리합니다.",
    },
    {"name": "account-deletion", "description": "계정 삭제 요청과 처리 상태를 관리합니다."},
]


def create_app() -> FastAPI:
    """Create an application instance without making health semantics depend on v1."""

    app = FastAPI(
        title="EPICK Service API",
        version="1.0.0",
        description=(
            "EPICK Service의 공개 v1 HTTP API입니다. 비동기 요청의 `202 Accepted`는 "
            "DB 수락만 의미하며 실제 worker dispatch 또는 외부 엔진 완료를 뜻하지 않습니다."
        ),
        openapi_tags=OPENAPI_TAGS,
    )
    app.middleware("http")(correlation_id_middleware)
    _register_api_error_handlers(app)
    app.include_router(api_v1_router)

    @app.get(
        "/health",
        tags=["health"],
        summary="프로세스 liveness 확인",
        response_description="실행 중인 API 프로세스 상태",
    )
    def health() -> dict[str, str]:
        """FastAPI 프로세스가 실행 중인지"""
        return {"status": "ok"}

    @app.get(
        "/health/ready",
        tags=["health"],
        summary="데이터베이스 readiness 확인",
        response_description="PostgreSQL 연결 가능 상태",
        responses={503: {"description": "PostgreSQL에 연결할 수 없습니다."}},
    )
    def readiness() -> dict[str, str]:
        """PostgreSQL에 연결할 수 있는지 체크"""
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))

            return {
                "status": "ready",
                "database": "connected",
            }

        except SQLAlchemyError as error:
            raise HTTPException(
                status_code=503,
                detail="database unavailable",
            ) from error

    return app


def _register_api_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiProblem)
    async def api_problem_handler(request: Request, error: ApiProblem) -> JSONResponse:
        return _api_error_response(request, error)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        if not _is_api_request(request):
            return await http_exception_handler(
                request,
                HTTPException(status_code=422, detail=error.errors()),
            )
        fields = tuple(
            ApiFieldError(
                field=".".join(str(part) for part in item["loc"]),
                reason=str(item["type"]).upper(),
            )
            for item in error.errors()
        )
        return _api_error_response(
            request,
            ApiProblem(
                status_code=422,
                code="INVALID_INPUT",
                message_ko="요청 값을 확인해 주세요.",
                fields=fields,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request, error: StarletteHTTPException
    ) -> JSONResponse:
        if not _is_api_request(request):
            return await http_exception_handler(request, error)
        if error.status_code == 404:
            return _api_error_response(request, ResourceNotFoundError())
        return _api_error_response(
            request,
            ApiProblem(
                status_code=error.status_code,
                code="INVALID_INPUT" if error.status_code == 422 else "REQUEST_REJECTED",
                message_ko="요청을 처리할 수 없습니다.",
            ),
        )


def _is_api_request(request: Request) -> bool:
    return request.url.path == "/api/v1" or request.url.path.startswith("/api/v1/")


def _api_error_response(request: Request, error: ApiProblem) -> JSONResponse:
    payload = ApiErrorResponse(
        error=ApiErrorBody(
            code=error.code,
            message_ko=error.message_ko,
            retryable=error.retryable,
            actions=list(error.actions),
            correlation_id=get_correlation_id(request),
            fields=[
                ErrorFieldResponse(field=field.field, reason=field.reason) for field in error.fields
            ],
        )
    )
    return JSONResponse(status_code=error.status_code, content=payload.model_dump(mode="json"))


app = create_app()
