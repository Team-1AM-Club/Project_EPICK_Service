from __future__ import annotations

from datetime import UTC, timedelta
from typing import Any, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.errors import ExecutionPolicyUnconfiguredError, InvalidInputError
from app.api.idempotency import canonical_request_hash, require_idempotency_key
from app.api.pagination import CursorCodec
from app.api.schemas.experience import (
    ActivityListItemResponse,
    ActivityResponse,
    EpisodeListItemResponse,
    EpisodeResponse,
    FieldValue,
    OutcomeValue,
    PeriodValue,
    VersionSummaryResponse,
)
from app.core.config import settings
from app.models.experience import Activity, ActivityVersion, Episode, EpisodeVersion
from app.models.identity import IdempotencyRecord
from app.repo.experience import ExperienceRepository
from app.repo.identity import IdentityRepository
from app.services.idempotency import IdempotencyService

ExperienceKind = Literal["activity", "episode"]
ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


def reserve_idempotency(
    *,
    session: Session,
    owner_user_id: UUID,
    method: str,
    path_scope: str,
    idempotency_key: str | None,
    request_body: dict[str, object],
) -> tuple[IdempotencyRecord, bool]:
    key = require_idempotency_key(idempotency_key)
    return IdempotencyService(IdentityRepository(session)).reserve(
        owner_user_id=owner_user_id,
        method=method,
        path_scope=path_scope,
        idempotency_key=key,
        request_hash=canonical_request_hash(request_body),
        expires_at=_utc_now() + timedelta(seconds=settings.api_idempotency_ttl_seconds),
    )


def complete_idempotency(
    record: IdempotencyRecord,
    *,
    response_status: int,
    kind: ExperienceKind,
    resource_id: UUID,
    response_body: dict[str, Any],
) -> None:
    IdempotencyService.complete(
        record,
        response_status=response_status,
        response_ref=f"{kind}:{resource_id}",
        response_body=response_body,
    )


def replay_resource_id(record: IdempotencyRecord, *, expected_kind: ExperienceKind) -> UUID:
    if record.response_status is None or record.response_ref is None:
        raise InvalidInputError(message_ko="이전 멱등성 요청이 아직 완료되지 않았습니다.")
    prefix, separator, raw_id = record.response_ref.partition(":")
    if separator != ":" or prefix != expected_kind:
        raise InvalidInputError(message_ko="멱등성 응답 참조를 확인할 수 없습니다.")
    try:
        return UUID(raw_id)
    except ValueError:
        raise InvalidInputError(message_ko="멱등성 응답 참조를 확인할 수 없습니다.") from None


def replay_response(
    record: IdempotencyRecord, response_model: type[ResponseModelT]
) -> ResponseModelT | None:
    if record.response_body is None:
        return None
    return response_model.model_validate(record.response_body)


def cursor_offset(*, cursor: str | None, sort: str) -> int:
    if cursor is None:
        return 0
    payload = _cursor_codec().decode(cursor)
    if (
        payload.get("resource") != "activities"
        or payload.get("sort") != sort
        or not isinstance(payload.get("offset"), int)
        or payload["offset"] < 0
    ):
        raise InvalidInputError(message_ko="목록 cursor를 확인해 주세요.")
    return payload["offset"]


def next_activity_cursor(
    *, offset: int, returned_count: int, has_next: bool, sort: str
) -> str | None:
    if not has_next:
        return None
    return _cursor_codec().encode(
        {
            "resource": "activities",
            "sort": sort,
            "offset": offset + returned_count,
        }
    )


def activity_response(activity: Activity, version: ActivityVersion) -> ActivityResponse:
    return ActivityResponse(
        id=activity.id,
        current_version=version.version_no,
        title=version.title,
        organization=_field_value(version.organization_text, version.organization_availability),
        activity_type=version.activity_type,
        period=PeriodValue(
            start_date=version.start_date,
            end_date=version.end_date,
            precision=version.period_precision,
            availability=version.period_availability,
        ),
        role=_field_value(version.role_text, version.role_availability),
        outcome=OutcomeValue(
            status=version.outcome_status,
            summary=_field_value(
                version.outcome_text,
                "PROVIDED" if version.outcome_text is not None else "NOT_PROVIDED",
            ),
        ),
        original_narrative=version.original_narrative,
        registration_status=activity.registration_status,
        usage_enabled=activity.usage_enabled,
        created_at=activity.created_at,
        updated_at=activity.updated_at,
    )


def activity_list_item(
    activity: Activity, version: ActivityVersion, episode_count: int
) -> ActivityListItemResponse:
    return ActivityListItemResponse(
        id=activity.id,
        current_version=version.version_no,
        title=version.title,
        organization_display=version.organization_text,
        activity_type=version.activity_type,
        period_display=_period_display(version),
        registration_status=activity.registration_status,
        usage_enabled=activity.usage_enabled,
        episode_count=episode_count,
        updated_at=activity.updated_at,
    )


def episode_response(
    *,
    episode: Episode,
    version: EpisodeVersion,
    repository: ExperienceRepository,
) -> EpisodeResponse:
    return EpisodeResponse(
        id=episode.id,
        activity_id=episode.activity_id,
        current_version=version.version_no,
        title=version.title,
        situation=_field_value(version.situation_text, version.situation_availability),
        problem=_field_value(version.problem_text, version.problem_availability),
        goal=_field_value(version.goal_text, version.goal_availability),
        actions=_field_value(version.actions_text, version.actions_availability),
        decisions=_field_value(version.decisions_text, version.decisions_availability),
        decision_reasons=_field_value(
            version.decision_reasons_text, version.decision_reasons_availability
        ),
        result=_field_value(version.result_text, version.result_availability),
        learning=_field_value(version.learning_text, version.learning_availability),
        technologies=[
            skill.raw_name
            for skill in repository.list_episode_skills(
                episode_version_id=version.id,
                owner_user_id=episode.owner_user_id,
            )
        ],
        original_narrative=version.original_narrative,
        registration_status=episode.registration_status,
        usage_enabled=episode.usage_enabled,
        created_at=episode.created_at,
        updated_at=episode.updated_at,
    )


def episode_list_item(episode: Episode, version: EpisodeVersion) -> EpisodeListItemResponse:
    return EpisodeListItemResponse(
        id=episode.id,
        activity_id=episode.activity_id,
        current_version=version.version_no,
        title=version.title,
        registration_status=episode.registration_status,
        usage_enabled=episode.usage_enabled,
        updated_at=episode.updated_at,
    )


def activity_version_summary(
    version: ActivityVersion, *, is_current: bool
) -> VersionSummaryResponse:
    return VersionSummaryResponse(
        version=version.version_no,
        created_at=version.created_at,
        created_by=version.created_by,
        change_reason=version.change_reason,
        is_current=is_current,
    )


def episode_version_summary(version: EpisodeVersion, *, is_current: bool) -> VersionSummaryResponse:
    return VersionSummaryResponse(
        version=version.version_no,
        created_at=version.created_at,
        created_by="USER",
        change_reason=version.change_reason,
        is_current=is_current,
    )


def _field_value(value: str | None, availability: str) -> FieldValue:
    return FieldValue(value=value, availability=availability)


def _period_display(version: ActivityVersion) -> str | None:
    if version.period_availability != "PROVIDED":
        return None
    if version.start_date is not None and version.end_date is not None:
        return f"{version.start_date.isoformat()} ~ {version.end_date.isoformat()}"
    if version.start_date is not None:
        return version.start_date.isoformat()
    if version.end_date is not None:
        return version.end_date.isoformat()
    return version.period_precision


def _cursor_codec() -> CursorCodec:
    if not settings.api_cursor_signing_key:
        raise ExecutionPolicyUnconfiguredError()
    return CursorCodec(settings.api_cursor_signing_key)


def _utc_now():
    from datetime import datetime

    return datetime.now(UTC)
