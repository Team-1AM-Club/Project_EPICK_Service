from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.api.private.schemas.w4_recommendations import (
    AcquireRequest,
    AcquireResponse,
    AuthorizeRequest,
    AuthorizeResponse,
    ContextResponse,
    FailRequest,
    FailResponse,
    LeaseRequest,
    PublishRequest,
    PublishResponse,
)
from app.core.config import settings
from app.models.registry import load_all_models
from app.services.w4_recommendation_run_store import (
    W4RecommendationRunStoreService,
    W4RecommendationStoreError,
)

AuthorizationHeader = Annotated[str | None, Header()]
ServicePrincipalHeader = Annotated[str | None, Header(alias="X-EPICK-Service-Principal")]


def _error(*, code: str, retryable: bool, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "schema_version": "w1.private.error.v1",
            "code": code,
            "retryable": retryable,
            "correlation_id": str(uuid4()),
        },
    )


def create_w4_recommendation_private_app(
    *,
    session_factory: Callable[[], Session],
    expected_bearer_token: str,
    expected_principal: str,
    lease_seconds: int,
) -> FastAPI:
    if not expected_bearer_token or not expected_principal:
        raise ValueError("W4 recommendation workload credentials must be configured")
    app = FastAPI(
        title="EPICK W1 private W4 recommendation RunStore",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.exception_handler(RequestValidationError)
    def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        del request, error
        return _error(code="INVALID_PRIVATE_REQUEST", retryable=False, status_code=422)

    @app.exception_handler(W4RecommendationStoreError)
    def store_error(request: Request, error: W4RecommendationStoreError) -> JSONResponse:
        del request
        status_code = 503 if error.retryable else 404
        return _error(code=error.code, retryable=error.retryable, status_code=status_code)

    def require_workload(
        authorization: AuthorizationHeader = None,
        service_principal: ServicePrincipalHeader = None,
    ) -> None:
        if authorization is None or not authorization.startswith("Bearer "):
            raise W4RecommendationStoreError("W4_WORKLOAD_UNAUTHENTICATED")
        token = authorization.removeprefix("Bearer ")
        if not token or not hmac.compare_digest(token, expected_bearer_token):
            raise W4RecommendationStoreError("W4_WORKLOAD_UNAUTHENTICATED")
        if service_principal != expected_principal:
            raise W4RecommendationStoreError("W4_WORKLOAD_UNAUTHENTICATED")

    workload = [Depends(require_workload)]

    @app.get(
        "/internal/v1/w4/recommendations/health",
        dependencies=workload,
        response_model=None,
    )
    def health() -> dict[str, str] | JSONResponse:
        try:
            with session_factory.begin() as session:
                session.execute(text("SELECT 1"))
            return {
                "status": "ok",
                "database": "worker_accessible",
                "real_data": "disabled",
            }
        except SQLAlchemyError:
            return _error(code="W4_STORE_UNAVAILABLE", retryable=True, status_code=503)

    @app.post(
        "/internal/v1/w4/recommendations/acquire",
        dependencies=workload,
        response_model=None,
    )
    def acquire(body: AcquireRequest) -> AcquireResponse | JSONResponse:
        try:
            with session_factory.begin() as session:
                binding = W4RecommendationRunStoreService(
                    session, lease_seconds=lease_seconds
                ).acquire(owner_user_id=body.owner_user_id, run_id=body.run_id)
                return AcquireResponse(completed=binding is None, binding=binding)
        except SQLAlchemyError:
            return _error(code="W4_STORE_UNAVAILABLE", retryable=True, status_code=503)

    @app.post(
        "/internal/v1/w4/recommendations/context",
        dependencies=workload,
        response_model=None,
    )
    def context(body: LeaseRequest) -> ContextResponse | JSONResponse:
        try:
            with session_factory.begin() as session:
                value = W4RecommendationRunStoreService(
                    session, lease_seconds=lease_seconds
                ).load_context(run_id=body.run_id, lease_token=body.lease_token)
                if value is None:
                    return _error(code="W4_RUN_NOT_FOUND", retryable=False, status_code=404)
                return ContextResponse(context=value)
        except SQLAlchemyError:
            return _error(code="W4_STORE_UNAVAILABLE", retryable=True, status_code=503)

    @app.post(
        "/internal/v1/w4/recommendations/authorize",
        dependencies=workload,
        response_model=None,
    )
    def authorize(body: AuthorizeRequest) -> AuthorizeResponse | JSONResponse:
        try:
            with session_factory.begin() as session:
                allowed = W4RecommendationRunStoreService(
                    session, lease_seconds=lease_seconds
                ).authorize(
                    run_id=body.run_id,
                    lease_token=body.lease_token,
                    action=body.action,
                    owner_user_id=body.user_id,
                    project_id=body.project_id,
                    context_version=body.context_version,
                )
                return AuthorizeResponse(allowed=allowed)
        except SQLAlchemyError:
            return _error(code="W4_STORE_UNAVAILABLE", retryable=True, status_code=503)

    @app.post(
        "/internal/v1/w4/recommendations/publish",
        dependencies=workload,
        response_model=None,
    )
    def publish(body: PublishRequest) -> PublishResponse | JSONResponse:
        try:
            with session_factory.begin() as session:
                published = W4RecommendationRunStoreService(
                    session, lease_seconds=lease_seconds
                ).publish(
                    run_id=body.run_id,
                    lease_token=body.lease_token,
                    publication=body.publication,
                )
                return PublishResponse(published=published)
        except SQLAlchemyError:
            return _error(code="W4_STORE_UNAVAILABLE", retryable=True, status_code=503)

    @app.post(
        "/internal/v1/w4/recommendations/fail",
        dependencies=workload,
        response_model=None,
    )
    def fail(body: FailRequest) -> FailResponse | JSONResponse:
        try:
            with session_factory.begin() as session:
                failed = W4RecommendationRunStoreService(session, lease_seconds=lease_seconds).fail(
                    run_id=body.run_id, lease_token=body.lease_token, code=body.code
                )
                return FailResponse(failed=failed)
        except SQLAlchemyError:
            return _error(code="W4_STORE_UNAVAILABLE", retryable=True, status_code=503)

    return app


def create_configured_w4_recommendation_private_app() -> FastAPI:
    if not settings.worker_database_url:
        raise RuntimeError("WORKER_DATABASE_URL is required for the W4 recommendation adapter")
    if not settings.w4_recommendation_private_bearer:
        raise RuntimeError("W4_RECOMMENDATION_PRIVATE_BEARER is required")
    # This private process has no public routers to import the rest of the ORM
    # graph. Load it before the first worker flush so string FK targets such as
    # recommendation_runs.job_id -> jobs.id are present in Base.metadata.
    load_all_models()
    from sqlalchemy import create_engine

    engine = create_engine(settings.worker_database_url, pool_pre_ping=True)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return create_w4_recommendation_private_app(
        session_factory=sessions,
        expected_bearer_token=settings.w4_recommendation_private_bearer.get_secret_value(),
        expected_principal=settings.w4_recommendation_private_audience,
        lease_seconds=settings.w4_recommendation_lease_seconds,
    )
