from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentPrincipalDep, OwnerSessionDep
from app.api.errors import (
    ActionNotAllowedError,
    IdempotencyConflictError,
    InvalidInputError,
    ResourceNotFoundError,
)
from app.api.schemas.account_deletion import (
    AccountDeletionConfirmRequest,
    AccountDeletionPreviewResponse,
    AccountDeletionResponse,
    AccountDeletionRetryRequest,
    AccountDeletionTargetResponse,
)
from app.api.v1.experience_common import complete_idempotency, replay_response, reserve_idempotency
from app.models.deletion import DeletionRequest, DeletionTarget
from app.repo.deletion import DeletionRepository
from app.services.deletion import (
    DeletionConflictError,
    DeletionOrchestrationService,
    DeletionRequestNotFoundError,
    DeletionTransitionError,
    DeletionValidationError,
)
from app.services.idempotency import IdempotencyConflictError as IdempotencyServiceConflictError

router = APIRouter(prefix="/account", tags=["account-deletion"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.post(
    "/deletion-previews",
    status_code=status.HTTP_201_CREATED,
    response_model=AccountDeletionPreviewResponse,
)
def create_account_deletion_preview(
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> AccountDeletionPreviewResponse:
    """Issue the one-time account-deletion confirmation secret exactly once."""

    try:
        preview = DeletionOrchestrationService(session).create_account_deletion_preview(
            owner_user_id=principal.owner_user_id
        )
    except DeletionRequestNotFoundError as error:
        raise ResourceNotFoundError() from error
    except DeletionConflictError as error:
        raise ActionNotAllowedError() from error
    except DeletionValidationError as error:
        raise InvalidInputError() from error
    return AccountDeletionPreviewResponse(
        deletion_request_id=preview.request.id,
        target_type="ACCOUNT",
        scope="ALL_PRIVATE_DATA",
        preview_token=preview.preview_token,
        expires_at=preview.request.preview_expires_at,
    )


@router.post(
    "/deletion-requests",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AccountDeletionResponse,
)
def confirm_account_deletion(
    body: AccountDeletionConfirmRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey = None,
) -> AccountDeletionResponse:
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="POST",
        path_scope="/api/v1/account/deletion-requests",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json"),
    )
    if replayed:
        result = replay_response(record, AccountDeletionResponse)
        if result is None:
            raise InvalidInputError(message_ko="이전 삭제 요청 응답을 확인할 수 없습니다.")
        response.status_code = record.response_status or status.HTTP_202_ACCEPTED
    else:
        try:
            request = DeletionOrchestrationService(session).confirm_and_start_account_deletion(
                owner_user_id=principal.owner_user_id,
                deletion_request_id=body.deletion_request_id,
                preview_token=body.preview_token,
            )
        except DeletionRequestNotFoundError as error:
            raise ResourceNotFoundError() from error
        except DeletionValidationError as error:
            raise InvalidInputError() from error
        except (DeletionConflictError, DeletionTransitionError) as error:
            raise ActionNotAllowedError() from error
        result = _deletion_response(session, request=request, owner_user_id=principal.owner_user_id)
        complete_idempotency(
            record,
            response_status=status.HTTP_202_ACCEPTED,
            kind="account_deletion",
            resource_id=request.id,
            response_body=result.model_dump(mode="json"),
        )
    response.headers["Location"] = f"/api/v1/account/deletion-requests/{result.id}"
    return result


@router.get("/deletion-requests/{deletion_request_id}", response_model=AccountDeletionResponse)
def get_account_deletion(
    deletion_request_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> AccountDeletionResponse:
    request = DeletionRepository(session).get_request(
        deletion_request_id=deletion_request_id, owner_user_id=principal.owner_user_id
    )
    if request is None or request.target_type != "ACCOUNT":
        raise ResourceNotFoundError()
    return _deletion_response(session, request=request, owner_user_id=principal.owner_user_id)


@router.post(
    "/deletion-requests/{deletion_request_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AccountDeletionResponse,
)
def retry_account_deletion_target(
    deletion_request_id: UUID,
    body: AccountDeletionRetryRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey = None,
) -> AccountDeletionResponse:
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="POST",
        path_scope=f"/api/v1/account/deletion-requests/{deletion_request_id}/retry",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json"),
    )
    if replayed:
        result = replay_response(record, AccountDeletionResponse)
        if result is None:
            raise InvalidInputError(message_ko="이전 삭제 재시도 응답을 확인할 수 없습니다.")
        response.status_code = record.response_status or status.HTTP_202_ACCEPTED
    else:
        repository = DeletionRepository(session)
        request = repository.get_request_for_update(
            deletion_request_id=deletion_request_id, owner_user_id=principal.owner_user_id
        )
        if request is None or request.target_type != "ACCOUNT":
            raise ResourceNotFoundError()
        try:
            DeletionOrchestrationService(session).retry_target(
                owner_user_id=principal.owner_user_id,
                deletion_request_id=deletion_request_id,
                deletion_target_id=body.target_id,
            )
        except DeletionRequestNotFoundError as error:
            raise ResourceNotFoundError() from error
        except (DeletionConflictError, DeletionTransitionError) as error:
            raise ActionNotAllowedError() from error
        result = _deletion_response(session, request=request, owner_user_id=principal.owner_user_id)
        complete_idempotency(
            record,
            response_status=status.HTTP_202_ACCEPTED,
            kind="account_deletion",
            resource_id=request.id,
            response_body=result.model_dump(mode="json"),
        )
    response.headers["Location"] = f"/api/v1/account/deletion-requests/{result.id}"
    return result


def _deletion_response(
    session: Session, *, request: DeletionRequest, owner_user_id: UUID
) -> AccountDeletionResponse:
    targets = DeletionRepository(session).list_targets(
        deletion_request_id=request.id, owner_user_id=owner_user_id
    )
    return AccountDeletionResponse(
        id=request.id,
        target_type="ACCOUNT",
        scope="ALL_PRIVATE_DATA",
        status=_public_status(request.status),
        targets=[_target_response(target) for target in targets],
        requested_at=request.requested_at,
        completed_at=request.completed_at,
    )


def _target_response(target: DeletionTarget) -> AccountDeletionTargetResponse:
    return AccountDeletionTargetResponse(
        id=target.id,
        store=target.store_type,
        status=target.status,
    )


def _public_status(status_value: str) -> str:
    if status_value == "REQUESTED":
        return "AWAITING_CONFIRMATION"
    if status_value == "CONFIRMED":
        return "RUNNING"
    return status_value


def _reserve(**kwargs):
    try:
        return reserve_idempotency(**kwargs)
    except IdempotencyServiceConflictError as error:
        raise IdempotencyConflictError() from error
