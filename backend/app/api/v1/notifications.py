from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query

from app.api.dependencies import CurrentPrincipalDep, OwnerSessionDep
from app.api.errors import IdempotencyConflictError, InvalidInputError, ResourceNotFoundError
from app.api.pagination import validate_page_limit
from app.api.schemas.common import CursorListResponse
from app.api.schemas.notifications import NotificationResponse, NotificationUpdateRequest
from app.api.v1.experience_common import complete_idempotency, replay_response, reserve_idempotency
from app.api.v1.workspace_common import cursor_offset, next_cursor
from app.models.lifecycle_operations import Notification
from app.repo.lifecycle_operations import LifecycleOperationsRepository
from app.services.idempotency import IdempotencyConflictError as IdempotencyServiceConflictError
from app.services.lifecycle_operations import (
    LifecycleOperationNotFoundError,
    LifecycleOperationsService,
)

router = APIRouter(tags=["notifications"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.get("/notifications", response_model=CursorListResponse[NotificationResponse])
def list_notifications(
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    unread_only: Annotated[bool, Query()] = False,
    severity: Annotated[str | None, Query()] = None,
    notification_type: Annotated[str | None, Query(alias="type")] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query()] = 20,
) -> CursorListResponse[NotificationResponse]:
    mapped_severity = _storage_severity(severity)
    page_limit = validate_page_limit(limit)
    resource = "notifications"
    offset = cursor_offset(cursor=cursor, resource=resource)
    notifications = LifecycleOperationsRepository(session).list_notifications(
        owner_user_id=principal.owner_user_id,
        unread_only=unread_only,
        severity=mapped_severity,
        notification_type=notification_type,
        offset=offset,
        limit=page_limit + 1,
    )
    visible = notifications[:page_limit]
    return CursorListResponse(
        items=[_notification_response(notification) for notification in visible],
        next_cursor=next_cursor(
            resource=resource,
            offset=offset,
            returned_count=len(visible),
            has_next=len(notifications) > page_limit,
        ),
    )


@router.patch("/notifications/{notification_id}", response_model=NotificationResponse)
def update_notification(
    notification_id: UUID,
    body: NotificationUpdateRequest,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey = None,
) -> NotificationResponse:
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="PATCH",
        path_scope=f"/api/v1/notifications/{notification_id}",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json"),
    )
    if replayed:
        result = replay_response(record, NotificationResponse)
        if result is None:
            raise InvalidInputError(message_ko="이전 알림 변경 응답을 확인할 수 없습니다.")
        return result

    service = LifecycleOperationsService(session)
    try:
        if body.read:
            service.mark_notification_read(
                owner_user_id=principal.owner_user_id, notification_id=notification_id
            )
        if body.archived:
            notification = service.archive_notification(
                owner_user_id=principal.owner_user_id, notification_id=notification_id
            )
        else:
            notification = LifecycleOperationsRepository(session).get_notification(
                notification_id=notification_id, owner_user_id=principal.owner_user_id
            )
            if notification is None:
                raise LifecycleOperationNotFoundError()
    except LifecycleOperationNotFoundError as error:
        raise ResourceNotFoundError() from error

    result = _notification_response(notification)
    complete_idempotency(
        record,
        response_status=200,
        kind="notification",
        resource_id=notification.id,
        response_body=result.model_dump(mode="json"),
    )
    return result


def _notification_response(notification: Notification) -> NotificationResponse:
    return NotificationResponse(
        id=notification.id,
        project_id=notification.project_id,
        type=notification.notification_type,
        severity="CRITICAL" if notification.severity == "ERROR" else notification.severity,
        title=notification.title,
        message=notification.safe_message,
        action_url=notification.action_url,
        read=notification.read_at is not None,
        archived=notification.archived_at is not None,
        created_at=notification.created_at,
    )


def _storage_severity(value: str | None) -> str | None:
    if value is None:
        return None
    mapped = {"INFO": "INFO", "WARNING": "WARNING", "CRITICAL": "ERROR"}.get(value)
    if mapped is None:
        raise InvalidInputError(message_ko="알림 심각도 값을 확인해 주세요.")
    return mapped


def _reserve(**kwargs):
    try:
        return reserve_idempotency(**kwargs)
    except IdempotencyServiceConflictError as error:
        raise IdempotencyConflictError() from error
