from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.runtime.w4_question_core_context import (
    W4_QUESTION_CORE_CONTEXT_PRINCIPAL,
    W4_QUESTION_CORE_CONTEXT_SCHEMA_VERSION,
    resolve_w4_question_core_context,
)


class W4QuestionCoreContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["w1.private.w4-question-core-context.v1"]
    context_key: UUID


class W4QuestionCoreContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["w1.private.w4-question-core-context.v1"] = (
        W4_QUESTION_CORE_CONTEXT_SCHEMA_VERSION
    )
    context_key: UUID
    job_id: UUID
    question_version_id: UUID
    source_id: UUID
    analysis_input_version: str
    authorization_revision: str
    current_decision_version: int
    data_kind: Literal["SYNTHETIC", "REAL"]
    processing_allowed: bool
    question_current: bool
    source_active: bool
    revoked: bool
    valid_until: str


class _ContextAuthorizationError(Exception):
    def __init__(self, *, status_code: int, code: str) -> None:
        self.status_code = status_code
        self.code = code


AuthorizationHeader = Annotated[str | None, Header()]
ServicePrincipalHeader = Annotated[str | None, Header(alias="X-EPICK-Service-Principal")]


def _private_error(*, code: str, retryable: bool, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "schema_version": "w1.private.error.v1",
            "code": code,
            "retryable": retryable,
            "correlation_id": str(uuid4()),
        },
    )


def create_w4_question_core_context_app(
    *, session_factory: Callable[[], Session], expected_bearer_token: str
) -> FastAPI:
    """Create W4's dedicated private context adapter.

    This is not the W2 lookup listener and does not expose a W1 public router.
    It accepts only an opaque W1-issued context key and returns no owner, prompt,
    company, source URL, or raw fence/deletion epoch values.
    """

    if not expected_bearer_token:
        raise ValueError("the W4 Question Core context bearer token must be configured")

    app = FastAPI(
        title="EPICK W1 private W4 Question Core context",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.exception_handler(_ContextAuthorizationError)
    def context_authorization_error(
        request: Request, error: _ContextAuthorizationError
    ) -> JSONResponse:
        del request
        return _private_error(code=error.code, retryable=False, status_code=error.status_code)

    @app.exception_handler(RequestValidationError)
    def context_validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        del request, error
        return _private_error(code="INVALID_W4_CONTEXT_REQUEST", retryable=False, status_code=422)

    def require_w4_service_principal(
        authorization: AuthorizationHeader = None,
        service_principal: ServicePrincipalHeader = None,
    ) -> None:
        if authorization is None or not authorization.startswith("Bearer "):
            raise _ContextAuthorizationError(
                status_code=401,
                code="UNAUTHENTICATED_SERVICE_PRINCIPAL",
            )
        received_token = authorization.removeprefix("Bearer ")
        if not received_token or not hmac.compare_digest(received_token, expected_bearer_token):
            raise _ContextAuthorizationError(
                status_code=401,
                code="UNAUTHENTICATED_SERVICE_PRINCIPAL",
            )
        if service_principal != W4_QUESTION_CORE_CONTEXT_PRINCIPAL:
            raise _ContextAuthorizationError(
                status_code=403,
                code="FORBIDDEN_SERVICE_PRINCIPAL",
            )

    @app.post(
        "/internal/v1/w4/question-core-contexts/resolve",
        response_model=W4QuestionCoreContextResponse,
        dependencies=[Depends(require_w4_service_principal)],
    )
    def resolve_context(
        body: W4QuestionCoreContextRequest,
    ) -> W4QuestionCoreContextResponse | JSONResponse:
        try:
            with session_factory.begin() as session:
                resolved = resolve_w4_question_core_context(
                    session=session,
                    context_key=body.context_key,
                )
                if resolved is None:
                    return _private_error(
                        code="W4_CONTEXT_NOT_FOUND",
                        retryable=False,
                        status_code=404,
                    )
                return W4QuestionCoreContextResponse(
                    context_key=resolved.context_key,
                    job_id=resolved.job_id,
                    question_version_id=resolved.question_version_id,
                    source_id=resolved.source_id,
                    analysis_input_version=resolved.analysis_input_version,
                    authorization_revision=resolved.authorization_revision,
                    current_decision_version=resolved.current_decision_version,
                    data_kind=resolved.data_kind,
                    processing_allowed=resolved.processing_allowed,
                    question_current=resolved.question_current,
                    source_active=resolved.source_active,
                    revoked=resolved.revoked,
                    valid_until=resolved.valid_until.isoformat().replace("+00:00", "Z"),
                )
        except SQLAlchemyError:
            return _private_error(code="INTERNAL_RETRYABLE", retryable=True, status_code=503)

    return app


def create_configured_w4_question_core_context_app() -> FastAPI:
    """Build the W4 adapter with its own read-only DB login and bearer."""

    if not settings.w4_context_database_url:
        raise RuntimeError(
            "W4_CONTEXT_DATABASE_URL must be set before starting the W4 context adapter"
        )
    if not settings.w1_w4_context_bearer:
        raise RuntimeError(
            "W1_W4_CONTEXT_BEARER must be set before starting the W4 context adapter"
        )
    from sqlalchemy import create_engine

    context_engine = create_engine(settings.w4_context_database_url, pool_pre_ping=True)
    context_sessions = sessionmaker(bind=context_engine, autoflush=False, expire_on_commit=False)
    return create_w4_question_core_context_app(
        session_factory=context_sessions,
        expected_bearer_token=settings.w1_w4_context_bearer,
    )
