from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentPrincipalDep, OwnerSessionDep
from app.api.errors import (
    ActionNotAllowedError,
    ApiFieldError,
    IdempotencyConflictError,
    InvalidInputError,
    ResourceNotFoundError,
    VersionConflictApiError,
)
from app.api.idempotency import parse_if_match
from app.api.schemas.experience import (
    CompleteRequest,
    EpisodeCreateRequest,
    EpisodeListItemResponse,
    EpisodeMutationResponse,
    EpisodeResponse,
    EpisodeUpdateRequest,
    VersionSummaryResponse,
)
from app.api.v1.experience_common import (
    complete_idempotency,
    episode_list_item,
    episode_response,
    episode_version_summary,
    replay_resource_id,
    replay_response,
    reserve_idempotency,
)
from app.repo.experience import ExperienceRepository
from app.services.experience import (
    AvailabilityValidationError,
    ExperienceNotFoundError,
    ExperienceService,
    InvalidExperienceStateError,
    VersionConflictError,
)
from app.services.idempotency import IdempotencyConflictError as IdempotencyServiceConflictError

router = APIRouter(tags=["episodes"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]
IfMatch = Annotated[str | None, Header(alias="If-Match")]


@router.get("/activities/{activity_id}/episodes", response_model=list[EpisodeListItemResponse])
def list_episodes(
    activity_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> list[EpisodeListItemResponse]:
    _get_activity(session, activity_id, principal.owner_user_id)
    return [
        episode_list_item(episode, version)
        for episode, version in ExperienceRepository(session).list_episodes(
            activity_id=activity_id,
            owner_user_id=principal.owner_user_id,
        )
    ]


@router.post(
    "/activities/{activity_id}/episodes",
    status_code=status.HTTP_201_CREATED,
    response_model=EpisodeResponse,
)
def create_episode(
    activity_id: UUID,
    body: EpisodeCreateRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey = None,
) -> EpisodeResponse:
    _get_activity(session, activity_id, principal.owner_user_id)
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="POST",
        path_scope=f"/api/v1/activities/{activity_id}/episodes",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json"),
    )
    if replayed:
        result = replay_response(record, EpisodeResponse)
        if result is None:
            episode = _get_episode(
                session,
                replay_resource_id(record, expected_kind="episode"),
                principal.owner_user_id,
            )
            if episode.activity_id != activity_id:
                raise ResourceNotFoundError()
            result = _episode_response(session, episode.id, principal.owner_user_id)
    else:
        try:
            episode = ExperienceService(session).create_episode(
                owner_user_id=principal.owner_user_id,
                activity_id=activity_id,
                title=body.title,
                situation_text=body.situation.value,
                situation_availability=body.situation.availability,
                problem_text=body.problem.value,
                problem_availability=body.problem.availability,
                goal_text=body.goal.value,
                goal_availability=body.goal.availability,
                actions_text=body.actions.value,
                actions_availability=body.actions.availability,
                decisions_text=body.decisions.value,
                decisions_availability=body.decisions.availability,
                decision_reasons_text=body.decision_reasons.value,
                decision_reasons_availability=body.decision_reasons.availability,
                result_text=body.result.value,
                result_availability=body.result.availability,
                learning_text=body.learning.value,
                learning_availability=body.learning.availability,
                technologies=body.technologies,
                original_narrative=body.original_narrative,
                usage_enabled=body.usage_enabled,
            )
        except ExperienceNotFoundError as error:
            raise ResourceNotFoundError() from error
        except AvailabilityValidationError as error:
            raise InvalidInputError() from error
        result = _episode_response(session, episode.id, principal.owner_user_id)
        complete_idempotency(
            record,
            response_status=201,
            kind="episode",
            resource_id=episode.id,
            response_body=result.model_dump(mode="json"),
        )

    response.headers["Location"] = f"/api/v1/episodes/{result.id}"
    response.headers["ETag"] = _etag(result.current_version)
    return result


@router.get("/episodes/{episode_id}", response_model=EpisodeResponse)
def get_episode(
    episode_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    response: Response,
) -> EpisodeResponse:
    result = _episode_response(session, episode_id, principal.owner_user_id)
    response.headers["ETag"] = _etag(
        _get_episode(session, episode_id, principal.owner_user_id).lock_version
    )
    return result


@router.patch("/episodes/{episode_id}", response_model=EpisodeMutationResponse)
def update_episode(
    episode_id: UUID,
    body: EpisodeUpdateRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
) -> EpisodeMutationResponse:
    expected_version = parse_if_match(if_match)
    update_fields = body.model_fields_set - {"change_reason"}
    if not update_fields:
        raise InvalidInputError(fields=(ApiFieldError(field="body", reason="NO_CHANGES"),))
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="PATCH",
        path_scope=f"/api/v1/episodes/{episode_id}",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json", exclude_unset=True),
    )
    if replayed:
        replayed_result = replay_response(record, EpisodeMutationResponse)
        if replayed_result is not None:
            response.headers["ETag"] = _etag(replayed_result.current_version)
            return replayed_result
        if replay_resource_id(record, expected_kind="episode") != episode_id:
            raise ResourceNotFoundError()
    else:
        try:
            ExperienceService(session).append_episode_version(
                episode_id=episode_id,
                owner_user_id=principal.owner_user_id,
                expected_lock_version=expected_version,
                change_reason=body.change_reason,
                **_episode_updates(body),
            )
        except ExperienceNotFoundError as error:
            raise ResourceNotFoundError() from error
        except VersionConflictError as error:
            _raise_episode_version_conflict(
                session,
                episode_id=episode_id,
                owner_user_id=principal.owner_user_id,
                expected_version=expected_version,
                source_error=error,
            )
        except AvailabilityValidationError as error:
            raise InvalidInputError() from error

    result = _episode_response(session, episode_id, principal.owner_user_id)
    mutation = EpisodeMutationResponse(**result.model_dump(), changed_fields=sorted(update_fields))
    if not replayed:
        complete_idempotency(
            record,
            response_status=200,
            kind="episode",
            resource_id=episode_id,
            response_body=mutation.model_dump(mode="json"),
        )
    response.headers["ETag"] = _etag(mutation.current_version)
    return mutation


@router.post("/episodes/{episode_id}/complete", response_model=EpisodeMutationResponse)
def complete_episode(
    episode_id: UUID,
    body: CompleteRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
) -> EpisodeMutationResponse:
    expected_version = parse_if_match(if_match)
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="POST",
        path_scope=f"/api/v1/episodes/{episode_id}/complete",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json"),
    )
    if replayed:
        replayed_result = replay_response(record, EpisodeMutationResponse)
        if replayed_result is not None:
            response.headers["ETag"] = _etag(replayed_result.current_version)
            return replayed_result
        if replay_resource_id(record, expected_kind="episode") != episode_id:
            raise ResourceNotFoundError()
    else:
        try:
            ExperienceService(session).complete_episode(
                episode_id=episode_id,
                owner_user_id=principal.owner_user_id,
                expected_lock_version=expected_version,
            )
        except ExperienceNotFoundError as error:
            raise ResourceNotFoundError() from error
        except VersionConflictError as error:
            _raise_episode_version_conflict(
                session,
                episode_id=episode_id,
                owner_user_id=principal.owner_user_id,
                expected_version=expected_version,
                source_error=error,
            )
        except InvalidExperienceStateError as error:
            raise ActionNotAllowedError() from error

    result = _episode_response(session, episode_id, principal.owner_user_id)
    mutation = EpisodeMutationResponse(**result.model_dump(), changed_fields=[])
    if not replayed:
        complete_idempotency(
            record,
            response_status=200,
            kind="episode",
            resource_id=episode_id,
            response_body=mutation.model_dump(mode="json"),
        )
    response.headers["ETag"] = _etag(mutation.current_version)
    return mutation


@router.get("/episodes/{episode_id}/versions", response_model=list[VersionSummaryResponse])
def list_episode_versions(
    episode_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> list[VersionSummaryResponse]:
    episode = _get_episode(session, episode_id, principal.owner_user_id)
    repository = ExperienceRepository(session)
    return [
        episode_version_summary(version, is_current=version.id == episode.current_version_id)
        for version in repository.list_episode_versions(
            episode_id=episode_id,
            owner_user_id=principal.owner_user_id,
        )
    ]


@router.get("/episodes/{episode_id}/versions/{version_no}", response_model=EpisodeResponse)
def get_episode_version(
    episode_id: UUID,
    version_no: int,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    response: Response,
) -> EpisodeResponse:
    episode = _get_episode(session, episode_id, principal.owner_user_id)
    version = ExperienceRepository(session).get_episode_version_by_number(
        episode_id=episode_id,
        version_no=version_no,
        owner_user_id=principal.owner_user_id,
    )
    if version is None:
        raise ResourceNotFoundError()
    response.headers["ETag"] = _etag(episode.lock_version)
    return episode_response(
        episode=episode, version=version, repository=ExperienceRepository(session)
    )


def _reserve(**kwargs):
    try:
        return reserve_idempotency(**kwargs)
    except IdempotencyServiceConflictError as error:
        raise IdempotencyConflictError() from error


def _get_activity(session: Session, activity_id: UUID, owner_user_id: UUID):
    activity = ExperienceRepository(session).get_activity(
        activity_id=activity_id,
        owner_user_id=owner_user_id,
    )
    if activity is None:
        raise ResourceNotFoundError()
    return activity


def _get_episode(session: Session, episode_id: UUID, owner_user_id: UUID):
    episode = ExperienceRepository(session).get_episode(
        episode_id=episode_id,
        owner_user_id=owner_user_id,
    )
    if episode is None:
        raise ResourceNotFoundError()
    return episode


def _episode_response(session: Session, episode_id: UUID, owner_user_id: UUID) -> EpisodeResponse:
    episode = _get_episode(session, episode_id, owner_user_id)
    if episode.current_version_id is None:
        raise ResourceNotFoundError()
    repository = ExperienceRepository(session)
    version = repository.get_episode_version(
        episode_version_id=episode.current_version_id,
        owner_user_id=owner_user_id,
    )
    if version is None:
        raise ResourceNotFoundError()
    return episode_response(episode=episode, version=version, repository=repository)


def _episode_updates(body: EpisodeUpdateRequest) -> dict[str, object]:
    updates: dict[str, object] = {}
    fields = body.model_fields_set
    if "title" in fields:
        if body.title is None:
            raise InvalidInputError(
                fields=(ApiFieldError(field="title", reason="MUST_NOT_BE_NULL"),)
            )
        updates["title"] = body.title
    for field_name in (
        "situation",
        "problem",
        "goal",
        "actions",
        "decisions",
        "decision_reasons",
        "result",
        "learning",
    ):
        if field_name not in fields:
            continue
        value = getattr(body, field_name)
        if value is None:
            updates[f"{field_name}_text"] = None
            updates[f"{field_name}_availability"] = "NOT_PROVIDED"
        else:
            updates[f"{field_name}_text"] = value.value
            updates[f"{field_name}_availability"] = value.availability
    if "technologies" in fields:
        if body.technologies is None:
            raise InvalidInputError(
                fields=(ApiFieldError(field="technologies", reason="USE_EMPTY_ARRAY_TO_CLEAR"),)
            )
        updates["technologies"] = body.technologies
    if "original_narrative" in fields:
        updates["original_narrative"] = body.original_narrative
    if "usage_enabled" in fields:
        if body.usage_enabled is None:
            raise InvalidInputError(
                fields=(ApiFieldError(field="usage_enabled", reason="MUST_NOT_BE_NULL"),)
            )
        updates["usage_enabled"] = body.usage_enabled
    return updates


def _raise_episode_version_conflict(
    session: Session,
    *,
    episode_id: UUID,
    owner_user_id: UUID,
    expected_version: int,
    source_error: Exception,
) -> None:
    episode = _get_episode(session, episode_id, owner_user_id)
    raise VersionConflictApiError(
        expected_version=expected_version,
        actual_version=episode.lock_version,
    ) from source_error


def _etag(version: int) -> str:
    return f'"{version}"'
