from __future__ import annotations

from app.api.errors import ExecutionPolicyUnconfiguredError, InvalidInputError
from app.api.pagination import CursorCodec
from app.api.schemas.projects import (
    ProjectListItemResponse,
    ProjectResponse,
    ProjectVersionSummaryResponse,
)
from app.api.schemas.questions import (
    QuestionListItemResponse,
    QuestionResponse,
    QuestionVersionSummaryResponse,
)
from app.core.config import settings
from app.models.application_workspace import (
    ApplicationProject,
    ApplicationProjectVersion,
    ProjectQuestion,
    QuestionVersion,
)

_PROJECT_STATUS_MAP = {
    "DRAFT": "DRAFT",
    "COLLECTING": "IN_PROGRESS",
    "READY": "IN_PROGRESS",
    "RECOMMENDING": "IN_PROGRESS",
    "MATERIALS_SELECTED": "MATERIALS_SELECTED",
    "STALE": "NEEDS_REVIEW",
    "ARCHIVED": "ARCHIVED",
}


def cursor_offset(*, cursor: str | None, resource: str) -> int:
    if cursor is None:
        return 0
    payload = _cursor_codec().decode(cursor)
    if (
        payload.get("resource") != resource
        or not isinstance(payload.get("offset"), int)
        or payload["offset"] < 0
    ):
        raise InvalidInputError(message_ko="목록 cursor를 확인해 주세요.")
    return payload["offset"]


def next_cursor(*, resource: str, offset: int, returned_count: int, has_next: bool) -> str | None:
    if not has_next:
        return None
    return _cursor_codec().encode({"resource": resource, "offset": offset + returned_count})


def project_status(*, internal_status: str, is_paused: bool) -> str:
    if is_paused:
        return "PAUSED"
    try:
        return _PROJECT_STATUS_MAP[internal_status]
    except KeyError as error:
        raise InvalidInputError(message_ko="Project 상태를 확인할 수 없습니다.") from error


def project_response(
    project: ApplicationProject,
    version: ApplicationProjectVersion,
    *,
    is_paused: bool,
) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        current_version=version.version_no,
        title=version.title,
        company_id=version.company_id,
        job_posting_id=version.job_posting_id,
        season=version.season,
        organization_name=version.organization_name,
        role_name=version.role_name,
        status=project_status(internal_status=project.status, is_paused=is_paused),
        current_step=project.current_step,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def project_list_item(
    project: ApplicationProject,
    version: ApplicationProjectVersion,
    *,
    is_paused: bool,
) -> ProjectListItemResponse:
    return ProjectListItemResponse(
        id=project.id,
        current_version=version.version_no,
        title=version.title,
        company_id=version.company_id,
        status=project_status(internal_status=project.status, is_paused=is_paused),
        current_step=project.current_step,
        updated_at=project.updated_at,
    )


def project_version_summary(
    version: ApplicationProjectVersion, *, is_current: bool
) -> ProjectVersionSummaryResponse:
    return ProjectVersionSummaryResponse(
        version=version.version_no,
        created_at=version.created_at,
        change_reason=version.change_reason,
        is_current=is_current,
    )


def question_response(question: ProjectQuestion, version: QuestionVersion) -> QuestionResponse:
    return QuestionResponse(
        id=question.id,
        project_id=question.project_id,
        current_version=version.version_no,
        prompt=version.prompt,
        character_limit=version.character_limit,
        display_order=question.display_order,
        source=_question_source(version.source),
        status=question.status,
        created_at=question.created_at,
        updated_at=question.updated_at,
    )


def question_list_item(
    question: ProjectQuestion, version: QuestionVersion
) -> QuestionListItemResponse:
    return QuestionListItemResponse(
        id=question.id,
        current_version=version.version_no,
        prompt=version.prompt,
        character_limit=version.character_limit,
        display_order=question.display_order,
        source=_question_source(version.source),
        updated_at=question.updated_at,
    )


def question_version_summary(
    version: QuestionVersion, *, is_current: bool
) -> QuestionVersionSummaryResponse:
    return QuestionVersionSummaryResponse(
        version=version.version_no,
        created_at=version.created_at,
        is_current=is_current,
    )


def _question_source(source: str) -> str:
    # PG-0/PG-1 fixtures used USER before the public v1 spelling was fixed.
    # The public API never exposes that legacy storage value.
    if source == "USER":
        return "USER_INPUT"
    return source


def _cursor_codec() -> CursorCodec:
    if not settings.api_cursor_signing_key:
        raise ExecutionPolicyUnconfiguredError()
    return CursorCodec(settings.api_cursor_signing_key)
