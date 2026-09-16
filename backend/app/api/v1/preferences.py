from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Response, status

from app.api.dependencies import CurrentPrincipalDep, OwnerSessionDep
from app.api.errors import (
    ExecutionPolicyUnconfiguredError,
    IdempotencyConflictError,
    InvalidInputError,
    ResourceNotFoundError,
    VersionConflictApiError,
)
from app.api.idempotency import parse_if_match
from app.api.schemas.common import CursorListResponse
from app.api.schemas.preferences import (
    AnalyticsConsentUpdateRequest,
    ConsentResponse,
    ExperienceExclusionCreateRequest,
    ExperienceExclusionResponse,
    FeedbackCreateRequest,
    FeedbackResponse,
    MissingCandidateFeedbackCreateRequest,
    RecommendationPreferenceResponse,
    RecommendationPreferenceUpdateRequest,
    RetentionPolicyStatusResponse,
    RunFeedbackCreateRequest,
    UserSettingsResponse,
    UserSettingsUpdateRequest,
)
from app.api.v1.experience_common import complete_idempotency, replay_response, reserve_idempotency
from app.models.privacy_controls import Consent, Feedback, RecommendationPreference, UserSettings
from app.models.projection import ExperienceExclusion
from app.repo.application_workspace import ApplicationWorkspaceRepository
from app.repo.experience import ExperienceRepository
from app.repo.privacy_controls import PrivacyControlsRepository
from app.repo.projection import ProjectionRepository
from app.repo.recommendations import RecommendationRepository
from app.services.idempotency import IdempotencyConflictError as IdempotencyServiceConflictError
from app.services.privacy_controls import (
    PrivacyControlsConflictError,
    PrivacyControlsNotFoundError,
    PrivacyControlsService,
    PrivacyControlsValidationError,
)
from app.services.projection_outbox import (
    ProjectionNotFoundError,
    ProjectionOutboxService,
    ProjectionValidationError,
)

router = APIRouter(tags=["preferences-and-privacy"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]
IfMatch = Annotated[str | None, Header(alias="If-Match")]


@router.get("/settings", response_model=UserSettingsResponse)
def get_settings(
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> UserSettingsResponse:
    settings = PrivacyControlsService(session).get_or_create_user_settings(
        owner_user_id=principal.owner_user_id
    )
    result = _settings_response(settings)
    response.headers["ETag"] = _etag(result.version)
    return result


@router.patch("/settings", response_model=UserSettingsResponse)
def update_settings(
    body: UserSettingsUpdateRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
) -> UserSettingsResponse:
    expected_version = parse_if_match(if_match)
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="PATCH",
        path_scope="/api/v1/settings",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json", exclude_unset=True),
    )
    if replayed:
        result = replay_response(record, UserSettingsResponse)
        if result is None:
            raise InvalidInputError(message_ko="이전 설정 변경 응답을 확인할 수 없습니다.")
    else:
        try:
            settings = PrivacyControlsService(session).update_user_settings(
                owner_user_id=principal.owner_user_id,
                expected_lock_version=expected_version,
                locale=body.locale,
                timezone=body.timezone,
                display_options=body.display_options,
            )
        except PrivacyControlsNotFoundError as error:
            raise ResourceNotFoundError() from error
        except PrivacyControlsConflictError as error:
            _raise_settings_version_conflict(
                session, principal.owner_user_id, expected_version, error
            )
        except PrivacyControlsValidationError as error:
            raise InvalidInputError() from error
        result = _settings_response(settings)
        complete_idempotency(
            record,
            response_status=200,
            kind="settings",
            resource_id=principal.owner_user_id,
            response_body=result.model_dump(mode="json"),
        )
    response.headers["ETag"] = _etag(result.version)
    return result


@router.get("/recommendation-preferences", response_model=RecommendationPreferenceResponse)
def get_recommendation_preferences(
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> RecommendationPreferenceResponse:
    preference = PrivacyControlsService(session).get_or_create_recommendation_preferences(
        owner_user_id=principal.owner_user_id
    )
    result = _recommendation_preference_response(preference)
    response.headers["ETag"] = _etag(result.version)
    return result


@router.patch("/recommendation-preferences", response_model=RecommendationPreferenceResponse)
def update_recommendation_preferences(
    body: RecommendationPreferenceUpdateRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
) -> RecommendationPreferenceResponse:
    expected_version = parse_if_match(if_match)
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="PATCH",
        path_scope="/api/v1/recommendation-preferences",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json", exclude_unset=True),
    )
    if replayed:
        result = replay_response(record, RecommendationPreferenceResponse)
        if result is None:
            raise InvalidInputError(message_ko="이전 추천 선호 변경 응답을 확인할 수 없습니다.")
    else:
        try:
            preference = PrivacyControlsService(session).update_recommendation_preferences(
                owner_user_id=principal.owner_user_id,
                expected_lock_version=expected_version,
                default_candidate_limit=body.default_candidate_limit,
                question_display_mode=body.question_display_mode,
                evidence_display_mode=body.evidence_display_mode,
                show_information_completeness=body.show_information_completeness,
            )
        except PrivacyControlsConflictError as error:
            _raise_preference_version_conflict(
                session, principal.owner_user_id, expected_version, error
            )
        except PrivacyControlsValidationError as error:
            raise InvalidInputError() from error
        result = _recommendation_preference_response(preference)
        complete_idempotency(
            record,
            response_status=200,
            kind="recommendation_preference",
            resource_id=principal.owner_user_id,
            response_body=result.model_dump(mode="json"),
        )
    response.headers["ETag"] = _etag(result.version)
    return result


@router.get(
    "/experience-exclusions", response_model=CursorListResponse[ExperienceExclusionResponse]
)
def list_experience_exclusions(
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> CursorListResponse[ExperienceExclusionResponse]:
    exclusions = ProjectionRepository(session).list_exclusions(
        owner_user_id=principal.owner_user_id
    )
    return CursorListResponse(items=[_exclusion_response(item) for item in exclusions])


@router.post(
    "/experience-exclusions",
    status_code=status.HTTP_201_CREATED,
    response_model=ExperienceExclusionResponse,
)
def create_experience_exclusion(
    body: ExperienceExclusionCreateRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey = None,
) -> ExperienceExclusionResponse:
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="POST",
        path_scope="/api/v1/experience-exclusions",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json"),
    )
    if replayed:
        result = replay_response(record, ExperienceExclusionResponse)
        if result is None:
            raise InvalidInputError(message_ko="이전 제외 생성 응답을 확인할 수 없습니다.")
    else:
        _require_exclusion_target(session, principal.owner_user_id, body)
        try:
            exclusion = ProjectionOutboxService(session).create_experience_exclusion(
                owner_user_id=principal.owner_user_id,
                activity_id=body.activity_id,
                episode_id=body.episode_id,
                scope=body.scope,
                company_id=body.company_id,
                role_id=body.role_id,
                project_id=body.project_id,
                reason=body.reason,
            )
        except (ProjectionNotFoundError, ProjectionValidationError) as error:
            raise InvalidInputError() from error
        result = _exclusion_response(exclusion)
        complete_idempotency(
            record,
            response_status=201,
            kind="experience_exclusion",
            resource_id=exclusion.id,
            response_body=result.model_dump(mode="json"),
        )
    response.headers["Location"] = f"/api/v1/experience-exclusions/{result.id}"
    return result


@router.delete("/experience-exclusions/{exclusion_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_experience_exclusion(
    exclusion_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey = None,
) -> Response:
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="DELETE",
        path_scope=f"/api/v1/experience-exclusions/{exclusion_id}",
        idempotency_key=idempotency_key,
        request_body={},
    )
    if replayed:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    try:
        exclusion = ProjectionOutboxService(session).revoke_experience_exclusion(
            owner_user_id=principal.owner_user_id, exclusion_id=exclusion_id
        )
    except ProjectionNotFoundError as error:
        raise ResourceNotFoundError() from error
    complete_idempotency(
        record,
        response_status=status.HTTP_204_NO_CONTENT,
        kind="experience_exclusion",
        resource_id=exclusion.id,
        response_body={},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/consents", response_model=list[ConsentResponse])
def get_consents(
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> list[ConsentResponse]:
    consent = PrivacyControlsRepository(session).get_latest_consent(
        owner_user_id=principal.owner_user_id, consent_type="ANALYTICS"
    )
    return [_consent_response(consent)]


@router.patch("/consents/analytics", response_model=ConsentResponse)
def update_analytics_consent(
    body: AnalyticsConsentUpdateRequest,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey = None,
) -> ConsentResponse:
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="PATCH",
        path_scope="/api/v1/consents/analytics",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json"),
    )
    if replayed:
        result = replay_response(record, ConsentResponse)
        if result is None:
            raise InvalidInputError(message_ko="이전 동의 변경 응답을 확인할 수 없습니다.")
        return result
    try:
        consent = PrivacyControlsService(session).record_consent(
            owner_user_id=principal.owner_user_id,
            consent_type="ANALYTICS",
            policy_version=body.policy_version,
            granted=body.opted_in,
        )
    except (PrivacyControlsNotFoundError, PrivacyControlsValidationError) as error:
        raise InvalidInputError() from error
    result = _consent_response(consent)
    complete_idempotency(
        record,
        response_status=200,
        kind="analytics_consent",
        resource_id=consent.id,
        response_body=result.model_dump(mode="json"),
    )
    return result


@router.get("/data-retention", response_model=RetentionPolicyStatusResponse)
def get_data_retention() -> RetentionPolicyStatusResponse:
    """No selectable retention policy is published before the governance decision."""

    return RetentionPolicyStatusResponse(status="PENDING_CONFIGURATION")


@router.patch("/data-retention")
def update_data_retention() -> None:
    """Reject writes until a product-approved selectable policy exists."""

    raise ExecutionPolicyUnconfiguredError()


@router.post(
    "/recommendation-runs/{run_id}/feedback",
    status_code=status.HTTP_201_CREATED,
    response_model=FeedbackResponse,
)
def create_run_feedback(
    run_id: UUID,
    body: RunFeedbackCreateRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey = None,
) -> FeedbackResponse:
    run = RecommendationRepository(session).get_run(
        run_id=run_id, owner_user_id=principal.owner_user_id
    )
    if run is None:
        raise ResourceNotFoundError()
    return _create_feedback(
        path_scope=f"/api/v1/recommendation-runs/{run_id}/feedback",
        body=body,
        response=response,
        principal=principal,
        session=session,
        idempotency_key=idempotency_key,
        run_id=run.id,
        snapshot_id=run.snapshot_id,
    )


@router.post(
    "/recommendation-runs/{run_id}/missing-candidate-feedback",
    status_code=status.HTTP_201_CREATED,
    response_model=FeedbackResponse,
)
def create_missing_candidate_feedback(
    run_id: UUID,
    body: MissingCandidateFeedbackCreateRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey = None,
) -> FeedbackResponse:
    run = RecommendationRepository(session).get_run(
        run_id=run_id, owner_user_id=principal.owner_user_id
    )
    if run is None or ExperienceRepository(session).get_episode(
        episode_id=body.episode_id, owner_user_id=principal.owner_user_id
    ) is None:
        raise ResourceNotFoundError()
    payload = FeedbackCreateRequest(
        category_l1="MISSING_CANDIDATE",
        category_l2=body.category_l2,
        other_text=body.other_text,
    )
    return _create_feedback(
        path_scope=f"/api/v1/recommendation-runs/{run_id}/missing-candidate-feedback",
        body=payload,
        response=response,
        principal=principal,
        session=session,
        idempotency_key=idempotency_key,
        run_id=run.id,
        snapshot_id=run.snapshot_id,
        missing_episode_id=body.episode_id,
    )


@router.post("/feedback", status_code=status.HTTP_201_CREATED, response_model=FeedbackResponse)
def create_feedback(
    body: FeedbackCreateRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey = None,
) -> FeedbackResponse:
    return _create_feedback(
        path_scope="/api/v1/feedback",
        body=body,
        response=response,
        principal=principal,
        session=session,
        idempotency_key=idempotency_key,
    )


def _create_feedback(
    *,
    path_scope: str,
    body: FeedbackCreateRequest,
    response: Response,
    principal,
    session,
    idempotency_key: str | None,
    run_id: UUID | None = None,
    snapshot_id: UUID | None = None,
    missing_episode_id: UUID | None = None,
) -> FeedbackResponse:
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="POST",
        path_scope=path_scope,
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json"),
    )
    if replayed:
        result = replay_response(record, FeedbackResponse)
        if result is None:
            raise InvalidInputError(message_ko="이전 피드백 응답을 확인할 수 없습니다.")
        response.status_code = record.response_status or status.HTTP_201_CREATED
        return result
    try:
        feedback = PrivacyControlsService(session).record_feedback(
            owner_user_id=principal.owner_user_id,
            category_l1=body.category_l1,
            category_l2=body.category_l2,
            decision_helpfulness=body.decision_helpfulness,
            other_text=body.other_text,
            run_id=run_id,
            snapshot_id=snapshot_id,
            missing_episode_id=missing_episode_id,
        )
    except (PrivacyControlsNotFoundError, PrivacyControlsValidationError) as error:
        raise InvalidInputError() from error
    result = _feedback_response(feedback)
    complete_idempotency(
        record,
        response_status=status.HTTP_201_CREATED,
        kind="feedback",
        resource_id=feedback.id,
        response_body=result.model_dump(mode="json"),
    )
    return result


def _settings_response(settings: UserSettings) -> UserSettingsResponse:
    return UserSettingsResponse(
        locale=settings.locale,
        timezone=settings.timezone,
        display_options=settings.display_options,
        version=settings.lock_version,
        updated_at=settings.updated_at,
    )


def _recommendation_preference_response(
    preference: RecommendationPreference,
) -> RecommendationPreferenceResponse:
    return RecommendationPreferenceResponse(
        default_candidate_limit=preference.default_candidate_limit,
        question_display_mode=preference.question_display_mode,
        evidence_display_mode=preference.evidence_display_mode,
        show_information_completeness=preference.show_information_completeness,
        version=preference.lock_version,
        updated_at=preference.updated_at,
    )


def _exclusion_response(exclusion: ExperienceExclusion) -> ExperienceExclusionResponse:
    return ExperienceExclusionResponse(
        id=exclusion.id,
        activity_id=exclusion.activity_id,
        episode_id=exclusion.episode_id,
        scope=exclusion.scope,
        company_id=exclusion.company_id,
        role_id=exclusion.role_id,
        project_id=exclusion.project_id,
        reason=exclusion.reason,
        created_at=exclusion.created_at,
        revoked_at=exclusion.revoked_at,
    )


def _consent_response(consent: Consent | None) -> ConsentResponse:
    return ConsentResponse(
        type="ANALYTICS",
        opted_in=consent.granted if consent is not None else False,
        policy_version=consent.policy_version if consent is not None else None,
        decided_at=consent.decided_at if consent is not None else None,
    )


def _feedback_response(feedback: Feedback) -> FeedbackResponse:
    # ``other_text`` intentionally never re-enters an API response or analytics path.
    return FeedbackResponse(
        id=feedback.id,
        category_l1=feedback.category_l1,
        category_l2=feedback.category_l2,
        decision_helpfulness=feedback.decision_helpfulness,
        run_id=feedback.run_id,
        missing_activity_id=feedback.missing_activity_id,
        missing_episode_id=feedback.missing_episode_id,
        created_at=feedback.created_at,
    )


def _require_exclusion_target(
    session, owner_user_id: UUID, body: ExperienceExclusionCreateRequest
) -> None:
    experiences = ExperienceRepository(session)
    if body.activity_id is not None and experiences.get_activity(
        activity_id=body.activity_id, owner_user_id=owner_user_id
    ) is None:
        raise ResourceNotFoundError()
    if body.episode_id is not None and experiences.get_episode(
        episode_id=body.episode_id, owner_user_id=owner_user_id
    ) is None:
        raise ResourceNotFoundError()
    if body.project_id is not None and ApplicationWorkspaceRepository(session).get_project(
        project_id=body.project_id, owner_user_id=owner_user_id
    ) is None:
        raise ResourceNotFoundError()


def _raise_settings_version_conflict(
    session, owner_user_id: UUID, expected: int, source: Exception
) -> None:
    current = PrivacyControlsRepository(session).get_user_settings(owner_user_id=owner_user_id)
    raise VersionConflictApiError(
        expected_version=expected, actual_version=current.lock_version if current is not None else 1
    ) from source


def _raise_preference_version_conflict(
    session, owner_user_id: UUID, expected: int, source: Exception
) -> None:
    current = PrivacyControlsRepository(session).get_recommendation_preferences(
        owner_user_id=owner_user_id
    )
    raise VersionConflictApiError(
        expected_version=expected, actual_version=current.lock_version if current is not None else 1
    ) from source


def _etag(version: int) -> str:
    return f'"{version}"'


def _reserve(**kwargs):
    try:
        return reserve_idempotency(**kwargs)
    except IdempotencyServiceConflictError as error:
        raise IdempotencyConflictError() from error
