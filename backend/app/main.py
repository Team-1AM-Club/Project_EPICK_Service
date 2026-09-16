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


def create_app() -> FastAPI:
    """Create an application instance without making health semantics depend on v1."""

    app = FastAPI(title="EPICK Service API")
    app.middleware("http")(correlation_id_middleware)
    _register_api_error_handlers(app)
    app.include_router(api_v1_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        """FastAPI 프로세스가 실행 중인지"""
        return {"status": "ok"}

    @app.get("/health/ready")
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
