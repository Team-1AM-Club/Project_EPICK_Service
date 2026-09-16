from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentPrincipalDep, OwnerSessionDep
from app.api.errors import (
    ActionNotAllowedError,
    ApiFieldError,
    CompletionRequirementsNotMetError,
    IdempotencyConflictError,
    InvalidInputError,
    ResourceNotFoundError,
    VersionConflictApiError,
)
from app.api.idempotency import parse_if_match
from app.api.schemas.common import CursorListResponse
from app.api.schemas.experience import (
    ActivityCreateRequest,
    ActivityListItemResponse,
    ActivityMutationResponse,
    ActivityResponse,
    ActivitySort,
    ActivityUpdateRequest,
    CompleteRequest,
    VersionSummaryResponse,
)
from app.api.v1.experience_common import (
    activity_list_item,
    activity_response,
    activity_version_summary,
    complete_idempotency,
    cursor_offset,
    next_activity_cursor,
    replay_resource_id,
    replay_response,
    reserve_idempotency,
)
from app.repo.experience import ExperienceRepository
from app.services.experience import (
    AvailabilityValidationError,
    CompletionRequirementsError,
    ExperienceNotFoundError,
    ExperienceService,
    InvalidExperienceStateError,
    VersionConflictError,
)
from app.services.idempotency import IdempotencyConflictError as IdempotencyServiceConflictError

router = APIRouter(prefix="/activities", tags=["activities"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]
IfMatch = Annotated[str | None, Header(alias="If-Match")]


@router.get("", response_model=CursorListResponse[ActivityListItemResponse])
def list_activities(
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    registration_status: Annotated[str | None, Query(alias="status")] = None,
    activity_type: Annotated[list[str] | None, Query()] = None,
    usage_enabled: Annotated[bool | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    sort: ActivitySort = "updated_at_desc",
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CursorListResponse[ActivityListItemResponse]:
    if registration_status not in {None, "DRAFT", "COMPLETED"}:
        raise InvalidInputError(
            fields=(ApiFieldError(field="status", reason="UNSUPPORTED_STATUS"),)
        )
    offset = cursor_offset(cursor=cursor, sort=sort)
    rows = ExperienceRepository(session).list_activities(
        owner_user_id=principal.owner_user_id,
        registration_status=registration_status,
        activity_types=tuple(activity_type or ()),
        usage_enabled=usage_enabled,
        query=q,
        sort=sort,
        offset=offset,
        limit=limit + 1,
    )
    has_next = len(rows) > limit
    visible_rows = rows[:limit]
    return CursorListResponse(
        items=[activity_list_item(*row) for row in visible_rows],
        next_cursor=next_activity_cursor(
            offset=offset,
            returned_count=len(visible_rows),
            has_next=has_next,
            sort=sort,
        ),
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ActivityResponse)
def create_activity(
    body: ActivityCreateRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey = None,
) -> ActivityResponse:
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="POST",
        path_scope="/api/v1/activities",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json"),
    )
    if replayed:
        result = replay_response(record, ActivityResponse)
        if result is None:
            activity = _get_activity(
                session,
                activity_id=replay_resource_id(record, expected_kind="activity"),
                owner_user_id=principal.owner_user_id,
            )
            result = _activity_response(session, activity.id, principal.owner_user_id)
    else:
        try:
            activity = ExperienceService(session).create_activity(
                owner_user_id=principal.owner_user_id,
                title=body.title,
                organization_text=body.organization.value,
                organization_availability=body.organization.availability,
                activity_type=body.activity_type,
                start_date=body.period.start_date,
                end_date=body.period.end_date,
                period_precision=body.period.precision,
                period_availability=body.period.availability,
                role_text=body.role.value,
                role_availability=body.role.availability,
                outcome_status=body.outcome.status,
                outcome_text=body.outcome.summary.value,
                outcome_availability=_outcome_availability(
                    body.outcome.status, body.outcome.summary.value
                ),
                original_narrative=body.original_narrative,
                usage_enabled=body.usage_enabled,
            )
        except AvailabilityValidationError as error:
            raise InvalidInputError() from error
        result = _activity_response(session, activity.id, principal.owner_user_id)
        complete_idempotency(
            record,
            response_status=201,
            kind="activity",
            resource_id=activity.id,
            response_body=result.model_dump(mode="json"),
        )

    response.headers["Location"] = f"/api/v1/activities/{result.id}"
    response.headers["ETag"] = _etag(result.current_version)
    return result


@router.get("/{activity_id}", response_model=ActivityResponse)
def get_activity(
    activity_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    response: Response,
) -> ActivityResponse:
    result = _activity_response(session, activity_id, principal.owner_user_id)
    response.headers["ETag"] = _etag(
        _get_activity(session, activity_id, principal.owner_user_id).lock_version
    )
    return result


@router.patch("/{activity_id}", response_model=ActivityMutationResponse)
def update_activity(
    activity_id: UUID,
    body: ActivityUpdateRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
) -> ActivityMutationResponse:
    expected_version = parse_if_match(if_match)
    update_fields = body.model_fields_set - {"change_reason"}
    if not update_fields:
        raise InvalidInputError(fields=(ApiFieldError(field="body", reason="NO_CHANGES"),))
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="PATCH",
        path_scope=f"/api/v1/activities/{activity_id}",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json", exclude_unset=True),
    )
    if replayed:
        replayed_result = replay_response(record, ActivityMutationResponse)
        if replayed_result is not None:
            response.headers["ETag"] = _etag(replayed_result.current_version)
            return replayed_result
        if replay_resource_id(record, expected_kind="activity") != activity_id:
            raise ResourceNotFoundError()
    else:
        try:
            ExperienceService(session).append_activity_version(
                activity_id=activity_id,
                owner_user_id=principal.owner_user_id,
                expected_lock_version=expected_version,
                change_reason=body.change_reason,
                **_activity_updates(body),
            )
        except ExperienceNotFoundError as error:
            raise ResourceNotFoundError() from error
        except VersionConflictError as error:
            _raise_activity_version_conflict(
                session,
                activity_id=activity_id,
                owner_user_id=principal.owner_user_id,
                expected_version=expected_version,
                source_error=error,
            )
        except AvailabilityValidationError as error:
            raise InvalidInputError() from error

    result = _activity_response(session, activity_id, principal.owner_user_id)
    mutation = ActivityMutationResponse(
        **result.model_dump(),
        changed_fields=sorted(update_fields),
        affected_project_count=0,
        reanalyze_available=False,
    )
    if not replayed:
        complete_idempotency(
            record,
            response_status=200,
            kind="activity",
            resource_id=activity_id,
            response_body=mutation.model_dump(mode="json"),
        )
    response.headers["ETag"] = _etag(mutation.current_version)
    return mutation


@router.post("/{activity_id}/complete", response_model=ActivityMutationResponse)
def complete_activity(
    activity_id: UUID,
    body: CompleteRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
) -> ActivityMutationResponse:
    expected_version = parse_if_match(if_match)
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="POST",
        path_scope=f"/api/v1/activities/{activity_id}/complete",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json"),
    )
    if replayed:
        replayed_result = replay_response(record, ActivityMutationResponse)
        if replayed_result is not None:
            response.headers["ETag"] = _etag(replayed_result.current_version)
            return replayed_result
        if replay_resource_id(record, expected_kind="activity") != activity_id:
            raise ResourceNotFoundError()
    else:
        try:
            ExperienceService(session).complete_activity(
                activity_id=activity_id,
                owner_user_id=principal.owner_user_id,
                expected_lock_version=expected_version,
            )
        except ExperienceNotFoundError as error:
            raise ResourceNotFoundError() from error
        except VersionConflictError as error:
            _raise_activity_version_conflict(
                session,
                activity_id=activity_id,
                owner_user_id=principal.owner_user_id,
                expected_version=expected_version,
                source_error=error,
            )
        except CompletionRequirementsError as error:
            raise CompletionRequirementsNotMetError(
                fields=tuple(
                    ApiFieldError(field=field, reason="REQUIRED_FOR_COMPLETION")
                    for field in error.missing_fields
                )
            ) from error
        except InvalidExperienceStateError as error:
            raise ActionNotAllowedError() from error

    result = _activity_response(session, activity_id, principal.owner_user_id)
    mutation = ActivityMutationResponse(
        **result.model_dump(),
        changed_fields=[],
        affected_project_count=0,
        reanalyze_available=False,
    )
    if not replayed:
        complete_idempotency(
            record,
            response_status=200,
            kind="activity",
            resource_id=activity_id,
            response_body=mutation.model_dump(mode="json"),
        )
    response.headers["ETag"] = _etag(mutation.current_version)
    return mutation


@router.get("/{activity_id}/versions", response_model=list[VersionSummaryResponse])
def list_activity_versions(
    activity_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> list[VersionSummaryResponse]:
    activity = _get_activity(session, activity_id, principal.owner_user_id)
    repository = ExperienceRepository(session)
    return [
        activity_version_summary(version, is_current=version.id == activity.current_version_id)
        for version in repository.list_activity_versions(
            activity_id=activity_id,
            owner_user_id=principal.owner_user_id,
        )
    ]


@router.get("/{activity_id}/versions/{version_no}", response_model=ActivityResponse)
def get_activity_version(
    activity_id: UUID,
    version_no: int,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    response: Response,
) -> ActivityResponse:
    activity = _get_activity(session, activity_id, principal.owner_user_id)
    version = ExperienceRepository(session).get_activity_version_by_number(
        activity_id=activity_id,
        version_no=version_no,
        owner_user_id=principal.owner_user_id,
    )
    if version is None:
        raise ResourceNotFoundError()
    response.headers["ETag"] = _etag(activity.lock_version)
    return activity_response(activity, version)


def _reserve(**kwargs):
    try:
        return reserve_idempotency(**kwargs)
    except IdempotencyServiceConflictError as error:
        raise IdempotencyConflictError() from error


def _activity_response(
    session: Session, activity_id: UUID, owner_user_id: UUID
) -> ActivityResponse:
    activity = _get_activity(session, activity_id, owner_user_id)
    if activity.current_version_id is None:
        raise ResourceNotFoundError()
    version = ExperienceRepository(session).get_activity_version(
        activity_version_id=activity.current_version_id,
        owner_user_id=owner_user_id,
    )
    if version is None:
        raise ResourceNotFoundError()
    return activity_response(activity, version)


def _get_activity(session: Session, activity_id: UUID, owner_user_id: UUID):
    activity = ExperienceRepository(session).get_activity(
        activity_id=activity_id,
        owner_user_id=owner_user_id,
    )
    if activity is None:
        raise ResourceNotFoundError()
    return activity


def _activity_updates(body: ActivityUpdateRequest) -> dict[str, object]:
    updates: dict[str, object] = {}
    fields = body.model_fields_set
    if "title" in fields:
        if body.title is None:
            raise InvalidInputError(
                fields=(ApiFieldError(field="title", reason="MUST_NOT_BE_NULL"),)
            )
        updates["title"] = body.title
    if "organization" in fields:
        if body.organization is None:
            updates.update(
                organization_text=None,
                organization_availability="NOT_PROVIDED",
            )
        else:
            updates.update(
                organization_text=body.organization.value,
                organization_availability=body.organization.availability,
            )
    if "activity_type" in fields:
        updates.update(
            activity_type=body.activity_type,
            activity_type_availability=(
                "PROVIDED" if body.activity_type is not None else "NOT_PROVIDED"
            ),
        )
    if "period" in fields:
        if body.period is None:
            updates.update(
                start_date=None,
                end_date=None,
                period_precision=None,
                period_availability="NOT_PROVIDED",
            )
        else:
            updates.update(
                start_date=body.period.start_date,
                end_date=body.period.end_date,
                period_precision=body.period.precision,
                period_availability=body.period.availability,
            )
    if "role" in fields:
        if body.role is None:
            updates.update(role_text=None, role_availability="NOT_PROVIDED")
        else:
            updates.update(role_text=body.role.value, role_availability=body.role.availability)
    if "outcome" in fields:
        if body.outcome is None:
            updates.update(
                outcome_status=None,
                outcome_text=None,
                outcome_availability="NOT_PROVIDED",
            )
        else:
            updates.update(
                outcome_status=body.outcome.status,
                outcome_text=body.outcome.summary.value,
                outcome_availability=_outcome_availability(
                    body.outcome.status,
                    body.outcome.summary.value,
                ),
            )
    if "original_narrative" in fields:
        updates["original_narrative"] = body.original_narrative
    if "usage_enabled" in fields:
        if body.usage_enabled is None:
            raise InvalidInputError(
                fields=(ApiFieldError(field="usage_enabled", reason="MUST_NOT_BE_NULL"),)
            )
        updates["usage_enabled"] = body.usage_enabled
    return updates


def _raise_activity_version_conflict(
    session: Session,
    *,
    activity_id: UUID,
    owner_user_id: UUID,
    expected_version: int,
    source_error: Exception,
) -> None:
    activity = _get_activity(session, activity_id, owner_user_id)
    raise VersionConflictApiError(
        expected_version=expected_version,
        actual_version=activity.lock_version,
    ) from source_error


def _etag(version: int) -> str:
    return f'"{version}"'


def _outcome_availability(status: str | None, summary: str | None) -> str:
    """Map the public outcome object to the legacy combined DB availability column."""

    return "PROVIDED" if status is not None or summary is not None else "NOT_PROVIDED"
