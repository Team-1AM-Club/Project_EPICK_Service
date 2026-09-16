from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.identity import User
from app.models.jobs import Job, JobCommand

_W2_SERVICE_PRINCIPAL = "w2"
_LOOKUP_SCHEMA_VERSION = "w1.private.command-lookup.v1"


class LookupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["w1.private.command-lookup.v1"]
    command_id: UUID
    execution_fence: Annotated[int, Field(ge=1)]
    owner_deletion_epoch: Annotated[int, Field(ge=0)]


class LookupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["w1.private.command-lookup.v1"] = _LOOKUP_SCHEMA_VERSION
    command_id: UUID
    status: Literal[
        "AVAILABLE",
        "NOT_FOUND",
        "STALE_FENCE",
        "STALE_DELETION_EPOCH",
        "DELETED",
        "INVALIDATED",
        "EXPIRED",
    ]
    reason_code: str | None
    command: dict[str, Any] | None


class _LookupAuthorizationError(Exception):
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


def create_lookup_app(
    *,
    session_factory: Callable[[], Session],
    expected_bearer_token: str,
) -> FastAPI:
    """Create the isolated W2-only lookup app.

    It intentionally does not import the public API router or `SessionLocal`: production passes a
    session factory bound to the read-only `epick_lookup_login` database URL.
    """

    if not expected_bearer_token:
        raise ValueError("the W2 lookup bearer token must be configured")

    app = FastAPI(
        title="EPICK W1 private command lookup",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.exception_handler(_LookupAuthorizationError)
    def lookup_authorization_error(
        request: Request, error: _LookupAuthorizationError
    ) -> JSONResponse:
        del request
        return _private_error(code=error.code, retryable=False, status_code=error.status_code)

    @app.exception_handler(RequestValidationError)
    def lookup_validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        del request, error
        return _private_error(code="INVALID_LOOKUP_REQUEST", retryable=False, status_code=422)

    def require_w2_service_principal(
        authorization: AuthorizationHeader = None,
        service_principal: ServicePrincipalHeader = None,
    ) -> None:
        if authorization is None or not authorization.startswith("Bearer "):
            raise _LookupAuthorizationError(
                status_code=401,
                code="UNAUTHENTICATED_SERVICE_PRINCIPAL",
            )
        received_token = authorization.removeprefix("Bearer ")
        if not received_token or not hmac.compare_digest(received_token, expected_bearer_token):
            raise _LookupAuthorizationError(
                status_code=401,
                code="UNAUTHENTICATED_SERVICE_PRINCIPAL",
            )
        # `w2` is the versioned contract principal.  IAM/ALB identity is an additional R-3
        # network boundary and does not replace this message-level guard.
        if service_principal != _W2_SERVICE_PRINCIPAL:
            raise _LookupAuthorizationError(
                status_code=403,
                code="FORBIDDEN_SERVICE_PRINCIPAL",
            )

    @app.post(
        "/internal/v1/job-commands/lookup",
        response_model=LookupResponse,
        dependencies=[Depends(require_w2_service_principal)],
    )
    def lookup_job_command(body: LookupRequest) -> LookupResponse | JSONResponse:
        try:
            with session_factory.begin() as session:
                return _lookup_command(session=session, request=body)
        except SQLAlchemyError:
            # Do not include driver, URL, command ID, or payload information in the protected
            # error body.  W2 may retry only this infrastructure outcome.
            return _private_error(code="INTERNAL_RETRYABLE", retryable=True, status_code=503)

    return app


def _lookup_command(*, session: Session, request: LookupRequest) -> LookupResponse:
    # The lookup login intentionally has column-level grants only.  Do not replace these
    # projections with `select(JobCommand)` or `select(Job)`: that would silently require more
    # privileges than `runtime_privileges.sql` grants in staging.
    command = session.execute(
        select(
            JobCommand.id,
            JobCommand.job_id,
            JobCommand.owner_user_id,
            JobCommand.command_type,
            JobCommand.execution_fence,
            JobCommand.owner_deletion_epoch,
            JobCommand.payload,
            JobCommand.status,
        ).where(JobCommand.id == request.command_id)
    ).mappings().one_or_none()
    if command is None:
        return _semantic_response(
            request=request,
            status="NOT_FOUND",
            reason_code="COMMAND_NOT_FOUND",
        )
    job = session.execute(
        select(
            Job.id,
            Job.owner_user_id,
            Job.status,
            Job.execution_fence,
            Job.owner_deletion_epoch,
            Job.active_lease_id,
        ).where(Job.id == command["job_id"])
    ).mappings().one_or_none()
    owner = session.execute(
        select(User.id, User.deletion_epoch, User.account_status).where(
            User.id == command["owner_user_id"]
        )
    ).mappings().one_or_none()
    if request.execution_fence != command["execution_fence"]:
        return _semantic_response(
            request=request,
            status="STALE_FENCE",
            reason_code="EXECUTION_FENCE_MISMATCH",
        )
    if request.owner_deletion_epoch != command["owner_deletion_epoch"]:
        return _semantic_response(
            request=request,
            status="STALE_DELETION_EPOCH",
            reason_code="OWNER_DELETION_EPOCH_MISMATCH",
        )
    if owner is None or owner["account_status"] in {"DELETION_PENDING", "DELETED"}:
        return _semantic_response(
            request=request,
            status="DELETED",
            reason_code="OWNER_DATA_DELETED",
        )
    if owner["deletion_epoch"] != request.owner_deletion_epoch:
        return _semantic_response(
            request=request,
            status="STALE_DELETION_EPOCH",
            reason_code="OWNER_DELETION_EPOCH_MISMATCH",
        )
    if job is None:
        return _semantic_response(request=request, status="NOT_FOUND", reason_code="JOB_NOT_FOUND")
    if job["execution_fence"] != request.execution_fence:
        return _semantic_response(
            request=request,
            status="STALE_FENCE",
            reason_code="EXECUTION_FENCE_MISMATCH",
        )
    if job["owner_deletion_epoch"] != request.owner_deletion_epoch:
        return _semantic_response(
            request=request,
            status="STALE_DELETION_EPOCH",
            reason_code="OWNER_DELETION_EPOCH_MISMATCH",
        )
    if command["status"] == "INVALIDATED" or job["status"] == "CANCEL_REQUESTED":
        return _semantic_response(
            request=request,
            status="INVALIDATED",
            reason_code="COMMAND_INVALIDATED",
        )
    if command["status"] in {"CONSUMED", "FAILED"} or job["status"] in {
        "SUCCEEDED",
        "FAILED_FINAL",
        "CANCELLED",
    }:
        return _semantic_response(request=request, status="EXPIRED", reason_code="COMMAND_EXPIRED")
    command_payload = command["payload"]
    w2_command = command_payload.get("w2_command") if isinstance(command_payload, dict) else None
    if (
        command["command_type"] != "W2_SOURCE_COLLECTION"
        # Relay delivery is at-least-once: SQS can expose a W2 command after send succeeds but
        # before the relay commits its `PENDING -> ENQUEUED` bookkeeping.  A W2 caller can only
        # possess this opaque command ID after that send, so PENDING is still safe to validate
        # against the canonical current Job/lease/fence/epoch state here.
        or command["status"] not in {"PENDING", "ENQUEUED"}
        or job["status"] != "RUNNING"
        or job["active_lease_id"] is None
        or not isinstance(w2_command, dict)
    ):
        return _semantic_response(request=request, status="EXPIRED", reason_code="COMMAND_EXPIRED")
    return LookupResponse(
        command_id=request.command_id,
        status="AVAILABLE",
        reason_code=None,
        command=w2_command,
    )


def _semantic_response(
    *, request: LookupRequest, status: str, reason_code: str
) -> LookupResponse:
    return LookupResponse(
        command_id=request.command_id,
        status=status,  # type: ignore[arg-type]
        reason_code=reason_code,
        command=None,
    )


def create_configured_lookup_app() -> FastAPI:
    """Build the production lookup adapter without reusing API DB credentials."""

    if not settings.lookup_database_url:
        raise RuntimeError("LOOKUP_DATABASE_URL must be set before starting the lookup adapter")
    if not settings.w1_w2_lookup_bearer:
        raise RuntimeError("W1_W2_LOOKUP_BEARER must be set before starting the lookup adapter")
    from sqlalchemy import create_engine

    lookup_engine = create_engine(settings.lookup_database_url, pool_pre_ping=True)
    lookup_sessions = sessionmaker(bind=lookup_engine, autoflush=False, expire_on_commit=False)
    return create_lookup_app(
        session_factory=lookup_sessions,
        expected_bearer_token=settings.w1_w2_lookup_bearer,
    )
