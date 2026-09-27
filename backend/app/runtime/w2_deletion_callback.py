"""Isolated authenticated W2 v2 deletion callback; never mount on the public API."""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Callable
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.deletion import DeletionRequest, DeletionTarget
from app.models.registry import load_all_models
from app.runtime.w2_private_deletion_v2 import (
    W2PrivateDeletionV2Error,
    parse_w2_private_deletion_ack_v2,
)
from app.runtime.w2_private_deletion_v2_boundary import W2PrivateDeletionV2BoundaryError
from app.services.deletion import (
    DeletionConflictError,
    DeletionOrchestrationError,
    DeletionOrchestrationService,
    DeletionTransitionError,
    StaleDeletionAcknowledgementError,
)


class W2DeletionCallbackResponse(BaseModel):
    status: Literal["ACKNOWLEDGED"] = "ACKNOWLEDGED"


class W2DeletionReadinessResponse(BaseModel):
    status: Literal["ready"] = "ready"


AuthorizationHeader = Annotated[str | None, Header()]
ServicePrincipalHeader = Annotated[str | None, Header(alias="X-EPICK-Service-Principal")]
_LOGGER = logging.getLogger(__name__)


def _error(code: str, status_code: int) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"code": code})


class _CallbackAuthorizationError(Exception):
    def __init__(self, code: str, status_code: int) -> None:
        self.code = code
        self.status_code = status_code


def create_w2_deletion_callback_app(
    *, session_factory: Callable[[], Session], expected_bearer_token: str
) -> FastAPI:
    if not expected_bearer_token:
        raise ValueError("W2 deletion callback bearer must be configured")

    app = FastAPI(
        title="EPICK W1 private W2 deletion callback",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def ack_error(code: str, status_code: int, deletion_id: UUID | None) -> JSONResponse:
        diagnostic_id = uuid4().hex
        deletion_ref = sha256(deletion_id.bytes).hexdigest() if deletion_id is not None else None
        _LOGGER.warning(
            "w2_deletion_ack_rejected diagnostic_id=%s status=%s code=%s deletion_ref_sha256=%s",
            diagnostic_id,
            status_code,
            code,
            deletion_ref,
        )
        response = _error(code, status_code)
        response.headers["X-EPICK-Deletion-Diagnostic-ID"] = diagnostic_id
        return response

    @app.exception_handler(_CallbackAuthorizationError)
    def authorization_error(request: Request, error: _CallbackAuthorizationError) -> JSONResponse:
        del request
        return _error(error.code, error.status_code)

    def require_w2_principal(
        authorization: AuthorizationHeader = None,
        service_principal: ServicePrincipalHeader = None,
    ) -> None:
        if authorization is None or not authorization.startswith("Bearer "):
            raise _CallbackAuthorizationError("UNAUTHENTICATED_SERVICE_PRINCIPAL", 401)
        received = authorization.removeprefix("Bearer ")
        if not received or not hmac.compare_digest(received, expected_bearer_token):
            raise _CallbackAuthorizationError("UNAUTHENTICATED_SERVICE_PRINCIPAL", 401)
        if service_principal != "w2":
            raise _CallbackAuthorizationError("FORBIDDEN_SERVICE_PRINCIPAL", 403)

    @app.get("/internal/health/ready", response_model=W2DeletionReadinessResponse)
    def readiness() -> W2DeletionReadinessResponse | JSONResponse:
        try:
            with session_factory.begin() as session:
                session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return _error("INTERNAL_RETRYABLE", 503)
        return W2DeletionReadinessResponse()

    @app.post(
        "/internal/v1/w2-private/deletion/ack",
        response_model=W2DeletionCallbackResponse,
        dependencies=[Depends(require_w2_principal)],
    )
    def apply_ack(body: dict[str, object]) -> W2DeletionCallbackResponse | JSONResponse:
        ack_body = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        deletion_id: UUID | None = None
        try:
            ack = parse_w2_private_deletion_ack_v2(ack_body)
            deletion_id = ack.deletion_id
            with session_factory.begin() as session:
                binding = session.execute(
                    select(DeletionTarget.deletion_request_id, DeletionRequest.owner_user_id)
                    .join(DeletionRequest, DeletionRequest.id == DeletionTarget.deletion_request_id)
                    .where(DeletionTarget.id == ack.deletion_id)
                ).one_or_none()
                if binding is None or binding.owner_user_id is None:
                    return ack_error("W2_DELETION_V2_TARGET_NOT_FOUND", 409, deletion_id)
                DeletionOrchestrationService(session).apply_w2_private_deletion_ack_v2(
                    owner_user_id=binding.owner_user_id,
                    deletion_request_id=binding.deletion_request_id,
                    deletion_target_id=ack.deletion_id,
                    ack_body=ack_body,
                )
            return W2DeletionCallbackResponse()
        except W2PrivateDeletionV2Error:
            return ack_error("W2_DELETION_V2_ACK_INVALID", 409, deletion_id)
        except StaleDeletionAcknowledgementError:
            return ack_error("W2_DELETION_V2_STALE_EPOCH", 409, deletion_id)
        except DeletionTransitionError:
            return ack_error("W2_DELETION_V2_STATE_INVALID", 409, deletion_id)
        except DeletionConflictError:
            return ack_error("W2_DELETION_V2_CONFLICT", 409, deletion_id)
        except (W2PrivateDeletionV2BoundaryError, DeletionOrchestrationError):
            return ack_error("W2_DELETION_V2_BINDING_INVALID", 409, deletion_id)
        except SQLAlchemyError:
            return ack_error("INTERNAL_RETRYABLE", 503, deletion_id)

    return app


def create_configured_w2_deletion_callback_app() -> FastAPI:
    if not settings.deletion_worker_database_url:
        raise RuntimeError("DELETION_WORKER_DATABASE_URL is required")
    if not settings.w1_w2_deletion_bearer:
        raise RuntimeError("W1_W2_DELETION_BEARER is required")
    from sqlalchemy import create_engine

    load_all_models()
    engine = create_engine(settings.deletion_worker_database_url, pool_pre_ping=True)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return create_w2_deletion_callback_app(
        session_factory=sessions, expected_bearer_token=settings.w1_w2_deletion_bearer
    )
