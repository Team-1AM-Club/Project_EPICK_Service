from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query

from app.api.dependencies import CurrentPrincipalDep, OwnerSessionDep
from app.api.errors import (
    ExecutionPolicyUnconfiguredError,
    IdempotencyConflictError,
    InvalidInputError,
    ResourceNotFoundError,
)
from app.api.pagination import validate_page_limit
from app.api.schemas.common import CursorListResponse
from app.api.schemas.inferences import (
    DuplicateDecisionRequest,
    DuplicateDecisionResponse,
    DuplicateSuggestionResponse,
    InferenceDecisionRequest,
    InferenceDecisionResponse,
    InferenceJobStartRequest,
    InferenceSourceReferenceResponse,
    InferenceSuggestionResponse,
)
from app.api.v1.experience_common import complete_idempotency, replay_response, reserve_idempotency
from app.api.v1.workspace_common import cursor_offset, next_cursor
from app.models.lifecycle_operations import (
    ExperienceDuplicateSuggestion,
    InferenceSuggestion,
    InferenceSuggestionSource,
)
from app.repo.lifecycle_operations import LifecycleOperationsRepository
from app.services.idempotency import IdempotencyConflictError as IdempotencyServiceConflictError
from app.services.lifecycle_operations import (
    LifecycleOperationNotFoundError,
    LifecycleOperationsService,
    LifecycleOperationValidationError,
)

router = APIRouter(tags=["inferences"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


@router.post("/episodes/{episode_id}/inference-jobs")
def start_inference_job(
    episode_id: UUID,
    body: InferenceJobStartRequest,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> None:
    """Keep the public endpoint explicit until the engine dispatch gate is approved."""

    repository = LifecycleOperationsRepository(session)
    if repository.get_episode(episode_id=episode_id, owner_user_id=principal.owner_user_id) is None:
        raise ResourceNotFoundError()
    # No synthetic AI result is created here.  The acceptance/dispatch contract is
    # deliberately unavailable until the W1 worker and egress gates are enabled.
    del body
    raise ExecutionPolicyUnconfiguredError()


@router.get(
    "/episodes/{episode_id}/inferences",
    response_model=CursorListResponse[InferenceSuggestionResponse],
)
def list_inferences(
    episode_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query()] = 20,
) -> CursorListResponse[InferenceSuggestionResponse]:
    repository = LifecycleOperationsRepository(session)
    if repository.get_episode(episode_id=episode_id, owner_user_id=principal.owner_user_id) is None:
        raise ResourceNotFoundError()
    page_limit = validate_page_limit(limit)
    resource = f"inferences:{episode_id}"
    offset = cursor_offset(cursor=cursor, resource=resource)
    suggestions = repository.list_inference_suggestions(
        episode_id=episode_id,
        owner_user_id=principal.owner_user_id,
        offset=offset,
        limit=page_limit + 1,
    )
    visible = suggestions[:page_limit]
    return CursorListResponse(
        items=[_suggestion_response(repository, suggestion) for suggestion in visible],
        next_cursor=next_cursor(
            resource=resource,
            offset=offset,
            returned_count=len(visible),
            has_next=len(suggestions) > page_limit,
        ),
    )


@router.patch(
    "/episodes/{episode_id}/inferences/{inference_id}",
    response_model=InferenceSuggestionResponse,
)
def decide_inference(
    episode_id: UUID,
    inference_id: UUID,
    body: InferenceDecisionRequest,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey = None,
) -> InferenceSuggestionResponse:
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="PATCH",
        path_scope=f"/api/v1/episodes/{episode_id}/inferences/{inference_id}",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json"),
    )
    if replayed:
        result = replay_response(record, InferenceSuggestionResponse)
        if result is None:
            raise InvalidInputError(message_ko="이전 추론 결정 응답을 확인할 수 없습니다.")
        return result

    repository = LifecycleOperationsRepository(session)
    suggestion = repository.get_inference_suggestion_for_update(
        suggestion_id=inference_id, owner_user_id=principal.owner_user_id
    )
    if suggestion is None or suggestion.episode_id != episode_id:
        raise ResourceNotFoundError()
    if suggestion.status != "PENDING_DECISION":
        raise InvalidInputError(message_ko="이미 결정되었거나 대체된 추론 제안입니다.")
    try:
        LifecycleOperationsService(session).record_inference_decision(
            owner_user_id=principal.owner_user_id,
            suggestion_id=inference_id,
            decision=body.decision,
            modified_value=body.modified_value,
            reason=body.reason,
        )
    except LifecycleOperationNotFoundError as error:
        raise ResourceNotFoundError() from error
    except LifecycleOperationValidationError as error:
        raise InvalidInputError() from error

    result = _suggestion_response(repository, suggestion)
    complete_idempotency(
        record,
        response_status=200,
        kind="inference",
        resource_id=inference_id,
        response_body=result.model_dump(mode="json"),
    )
    return result


@router.get(
    "/experiences/duplicates",
    response_model=CursorListResponse[DuplicateSuggestionResponse],
)
def list_duplicate_suggestions(
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    activity_id: Annotated[UUID | None, Query()] = None,
    episode_id: Annotated[UUID | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query()] = 20,
) -> CursorListResponse[DuplicateSuggestionResponse]:
    if (activity_id is None) == (episode_id is None):
        raise InvalidInputError(message_ko="activity_id 또는 episode_id 중 하나가 필요합니다.")
    repository = LifecycleOperationsRepository(session)
    if activity_id is not None and repository.get_activity(
        activity_id=activity_id, owner_user_id=principal.owner_user_id
    ) is None:
        raise ResourceNotFoundError()
    if episode_id is not None and repository.get_episode(
        episode_id=episode_id, owner_user_id=principal.owner_user_id
    ) is None:
        raise ResourceNotFoundError()
    page_limit = validate_page_limit(limit)
    resource = f"experience-duplicates:{activity_id or episode_id}"
    offset = cursor_offset(cursor=cursor, resource=resource)
    suggestions = repository.list_duplicate_suggestions(
        owner_user_id=principal.owner_user_id,
        activity_id=activity_id,
        episode_id=episode_id,
        offset=offset,
        limit=page_limit + 1,
    )
    visible = suggestions[:page_limit]
    return CursorListResponse(
        items=[_duplicate_response(repository, suggestion) for suggestion in visible],
        next_cursor=next_cursor(
            resource=resource,
            offset=offset,
            returned_count=len(visible),
            has_next=len(suggestions) > page_limit,
        ),
    )


@router.patch(
    "/experiences/duplicates/{duplicate_id}",
    response_model=DuplicateSuggestionResponse,
)
def decide_duplicate_suggestion(
    duplicate_id: UUID,
    body: DuplicateDecisionRequest,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey = None,
) -> DuplicateSuggestionResponse:
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="PATCH",
        path_scope=f"/api/v1/experiences/duplicates/{duplicate_id}",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json"),
    )
    if replayed:
        result = replay_response(record, DuplicateSuggestionResponse)
        if result is None:
            raise InvalidInputError(message_ko="이전 중복 제안 결정 응답을 확인할 수 없습니다.")
        return result
    repository = LifecycleOperationsRepository(session)
    suggestion = repository.get_duplicate_suggestion_for_update(
        suggestion_id=duplicate_id, owner_user_id=principal.owner_user_id
    )
    if suggestion is None:
        raise ResourceNotFoundError()
    if suggestion.status != "PENDING_DECISION":
        raise InvalidInputError(message_ko="이미 결정되었거나 대체된 중복 제안입니다.")
    try:
        LifecycleOperationsService(session).record_duplicate_decision(
            owner_user_id=principal.owner_user_id,
            suggestion_id=duplicate_id,
            decision=body.decision,
            reason=body.reason,
        )
    except LifecycleOperationNotFoundError as error:
        raise ResourceNotFoundError() from error
    except LifecycleOperationValidationError as error:
        raise InvalidInputError() from error
    result = _duplicate_response(repository, suggestion)
    complete_idempotency(
        record,
        response_status=200,
        kind="duplicate_suggestion",
        resource_id=duplicate_id,
        response_body=result.model_dump(mode="json"),
    )
    return result


def _suggestion_response(
    repository: LifecycleOperationsRepository, suggestion: InferenceSuggestion
) -> InferenceSuggestionResponse:
    decision = repository.get_latest_inference_decision(
        suggestion_id=suggestion.id, owner_user_id=suggestion.owner_user_id
    )
    return InferenceSuggestionResponse(
        id=suggestion.id,
        episode_id=suggestion.episode_id,
        episode_version_id=suggestion.episode_version_id,
        suggestion_type=suggestion.suggestion_type,
        proposed_value=suggestion.proposed_value,
        status=_public_status(suggestion.status),
        sources=[
            _source_response(source)
            for source in repository.list_inference_sources(
                suggestion_id=suggestion.id, owner_user_id=suggestion.owner_user_id
            )
        ],
        latest_decision=(
            InferenceDecisionResponse(
                id=decision.id,
                decision=decision.decision,
                modified_value=decision.modified_value,
                reason=decision.reason,
                decided_at=decision.decided_at,
            )
            if decision is not None
            else None
        ),
        created_at=suggestion.created_at,
    )


def _source_response(source: InferenceSuggestionSource) -> InferenceSourceReferenceResponse:
    reference_type = (
        "EPISODE_VERSION"
        if source.episode_version_id is not None
        else "SOURCE_VERSION"
        if source.source_version_id is not None
        else "EVIDENCE_SPAN"
    )
    return InferenceSourceReferenceResponse(
        field_name=source.field_name,
        source_span_start=source.source_span_start,
        source_span_end=source.source_span_end,
        reference_type=reference_type,
    )


def _duplicate_response(
    repository: LifecycleOperationsRepository, suggestion: ExperienceDuplicateSuggestion
) -> DuplicateSuggestionResponse:
    decision = repository.get_latest_duplicate_decision(suggestion_id=suggestion.id)
    return DuplicateSuggestionResponse(
        id=suggestion.id,
        left_episode_id=suggestion.left_episode_id,
        left_episode_version_id=suggestion.left_episode_version_id,
        right_episode_id=suggestion.right_episode_id,
        right_episode_version_id=suggestion.right_episode_version_id,
        reason=suggestion.reason,
        status=_public_status(suggestion.status),
        latest_decision=(
            DuplicateDecisionResponse(
                id=decision.id,
                decision=decision.decision,
                reason=decision.reason,
                decided_at=decision.decided_at,
            )
            if decision is not None
            else None
        ),
        created_at=suggestion.created_at,
    )


def _public_status(value: str) -> str:
    return "PENDING" if value == "PENDING_DECISION" else value


def _reserve(**kwargs):
    try:
        return reserve_idempotency(**kwargs)
    except IdempotencyServiceConflictError as error:
        raise IdempotencyConflictError() from error
