from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.registry import load_all_models
from app.services.w3_authority import (
    W3AuthorityConflictError,
    W3AuthorityNotFoundError,
    W3AuthorityService,
)


class AuthorityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["w1.private.w3-authority-request.v1"]
    job_id: UUID
    source_id: UUID


class AuthorityContextResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    company_id: UUID
    source_id: UUID
    analysis_input_version: Annotated[str, Field(min_length=1, max_length=64, pattern=r"\S")]


class AuthorityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["w1.private.w3-authority-response.v1"] = (
        "w1.private.w3-authority-response.v1"
    )
    context: AuthorityContextResponse
    owner_id: UUID
    owner_epoch: Annotated[int, Field(strict=True, ge=0)]
    active: Annotated[bool, Field(strict=True)]


class W3AuthorityHttpError(RuntimeError):
    def __init__(self, *, code: str, status_code: int, retryable: bool) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code
        self.retryable = retryable


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


def create_w3_authority_private_app(
    *,
    session_factory: Callable[[], Session],
    expected_bearer_token: str,
    expected_principal: str,
) -> FastAPI:
    if not expected_bearer_token or not expected_principal:
        raise ValueError("W3 Authority workload credentials must be configured")

    app = FastAPI(
        title="EPICK W1 private W3 Authority",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.exception_handler(RequestValidationError)
    def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        del request, error
        return _error(code="INVALID_PRIVATE_REQUEST", retryable=False, status_code=422)

    @app.exception_handler(W3AuthorityHttpError)
    def authority_http_error(request: Request, error: W3AuthorityHttpError) -> JSONResponse:
        del request
        return _error(
            code=error.code,
            retryable=error.retryable,
            status_code=error.status_code,
        )

    def require_workload(
        authorization: AuthorizationHeader = None,
        service_principal: ServicePrincipalHeader = None,
    ) -> None:
        if authorization is None or not authorization.startswith("Bearer "):
            raise W3AuthorityHttpError(
                code="W3_AUTHORITY_UNAUTHENTICATED",
                status_code=401,
                retryable=False,
            )
        token = authorization.removeprefix("Bearer ")
        if not token or not hmac.compare_digest(token, expected_bearer_token):
            raise W3AuthorityHttpError(
                code="W3_AUTHORITY_UNAUTHENTICATED",
                status_code=401,
                retryable=False,
            )
        if service_principal != expected_principal:
            raise W3AuthorityHttpError(
                code="W3_AUTHORITY_FORBIDDEN",
                status_code=403,
                retryable=False,
            )

    router = APIRouter(
        prefix="/internal/v1/w3/authority",
        tags=["private-w3-authority"],
        dependencies=[Depends(require_workload)],
    )

    @router.post("/current")
    def current(body: AuthorityRequest) -> AuthorityResponse:
        try:
            with session_factory.begin() as session:
                authorization = W3AuthorityService(session).current(
                    job_id=body.job_id,
                    source_id=body.source_id,
                )
        except W3AuthorityNotFoundError as error:
            raise W3AuthorityHttpError(
                code="W3_AUTHORITY_NOT_FOUND",
                status_code=404,
                retryable=False,
            ) from error
        except W3AuthorityConflictError as error:
            raise W3AuthorityHttpError(
                code="W3_AUTHORITY_CONFLICT",
                status_code=409,
                retryable=False,
            ) from error
        except (SQLAlchemyError, TimeoutError) as error:
            raise W3AuthorityHttpError(
                code="W3_AUTHORITY_UNAVAILABLE",
                status_code=503,
                retryable=True,
            ) from error

        return AuthorityResponse(
            context=AuthorityContextResponse(
                job_id=authorization.context.job_id,
                company_id=authorization.context.company_id,
                source_id=authorization.context.source_id,
                analysis_input_version=authorization.context.analysis_input_version,
            ),
            owner_id=authorization.owner_id,
            owner_epoch=authorization.owner_epoch,
            active=authorization.active,
        )

    app.include_router(router)
    return app


def create_configured_w3_authority_private_app() -> FastAPI:
    if not settings.w3_authority_database_url:
        raise RuntimeError("W3_AUTHORITY_DATABASE_URL is required")
    if not settings.w3_authority_private_bearer:
        raise RuntimeError("W3_AUTHORITY_PRIVATE_BEARER is required")

    load_all_models()
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(settings.w3_authority_database_url, pool_pre_ping=True)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return create_w3_authority_private_app(
        session_factory=sessions,
        expected_bearer_token=settings.w3_authority_private_bearer.get_secret_value(),
        expected_principal=settings.w3_authority_expected_principal,
    )
