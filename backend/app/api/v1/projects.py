from __future__ import annotations

from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Header, Query, Response, status
from sqlalchemy.orm import Session

from app.api.dependencies import CurrentPrincipalDep, OwnerSessionDep
from app.api.errors import (
    ApiFieldError,
    ExecutionPolicyUnconfiguredError,
    IdempotencyConflictError,
    InvalidInputError,
    ResourceNotFoundError,
    VersionConflictApiError,
)
from app.api.idempotency import parse_if_match
from app.api.schemas.common import CursorListResponse
from app.api.schemas.projects import (
    ProjectCreateRequest,
    ProjectJobPostingLinkRequest,
    ProjectListItemResponse,
    ProjectMutationResponse,
    ProjectResponse,
    ProjectUpdateRequest,
    ProjectVersionSummaryResponse,
)
from app.api.v1.experience_common import (
    complete_idempotency,
    replay_resource_id,
    replay_response,
    reserve_idempotency,
)
from app.api.v1.workspace_common import (
    cursor_offset,
    next_cursor,
    project_list_item,
    project_response,
    project_version_summary,
)
from app.repo.application_workspace import ApplicationWorkspaceRepository
from app.services.application_workspace import (
    UNSET,
    ApplicationWorkspaceError,
    ApplicationWorkspaceNotFoundError,
    ApplicationWorkspaceService,
    WorkspaceVersionConflictError,
)
from app.services.idempotency import IdempotencyConflictError as IdempotencyServiceConflictError

router = APIRouter(prefix="/application-projects", tags=["application-projects"])

IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]
IfMatch = Annotated[str | None, Header(alias="If-Match")]


@router.get("", response_model=CursorListResponse[ProjectListItemResponse])
def list_projects(
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CursorListResponse[ProjectListItemResponse]:
    offset = cursor_offset(cursor=cursor, resource="application-projects")
    rows = ApplicationWorkspaceRepository(session).list_projects(
        owner_user_id=principal.owner_user_id, offset=offset, limit=limit + 1
    )
    visible_rows = rows[:limit]
    return CursorListResponse(
        items=[
            project_list_item(project, version, is_paused=is_paused)
            for project, version, is_paused in visible_rows
        ],
        next_cursor=next_cursor(
            resource="application-projects",
            offset=offset,
            returned_count=len(visible_rows),
            has_next=len(rows) > limit,
        ),
    )


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProjectResponse)
def create_project(
    body: ProjectCreateRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    idempotency_key: IdempotencyKey = None,
) -> ProjectResponse:
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="POST",
        path_scope="/api/v1/application-projects",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json"),
    )
    if replayed:
        result = replay_response(record, ProjectResponse)
        if result is None:
            result = _project_response(
                session,
                project_id=replay_resource_id(record, expected_kind="project"),
                owner_user_id=principal.owner_user_id,
            )
    else:
        repository = ApplicationWorkspaceRepository(session)
        _require_selectable_company(repository, body.company_id)
        _require_job_posting_matches_company(repository, body.job_posting_id, body.company_id)
        try:
            project = ApplicationWorkspaceService(session).create_project(
                owner_user_id=principal.owner_user_id,
                company_id=body.company_id,
                title=body.title,
                role_name=body.role_name,
                season=body.season,
                organization_name=body.organization_name,
                job_posting_id=body.job_posting_id,
            )
        except ApplicationWorkspaceError as error:
            raise InvalidInputError() from error
        result = _project_response(
            session, project_id=project.id, owner_user_id=principal.owner_user_id
        )
        complete_idempotency(
            record,
            response_status=201,
            kind="project",
            resource_id=project.id,
            response_body=result.model_dump(mode="json"),
        )
    response.headers["Location"] = f"/api/v1/application-projects/{result.id}"
    response.headers["ETag"] = _etag(result.current_version)
    return result


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    response: Response,
) -> ProjectResponse:
    result = _project_response(
        session, project_id=project_id, owner_user_id=principal.owner_user_id
    )
    response.headers["ETag"] = _etag(result.current_version)
    return result


@router.patch("/{project_id}", response_model=ProjectMutationResponse)
def update_project(
    project_id: UUID,
    body: ProjectUpdateRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
) -> ProjectMutationResponse:
    expected_version = parse_if_match(if_match)
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="PATCH",
        path_scope=f"/api/v1/application-projects/{project_id}",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json", exclude_unset=True),
    )
    if replayed:
        result = replay_response(record, ProjectMutationResponse)
        if result is None:
            if replay_resource_id(record, expected_kind="project") != project_id:
                raise ResourceNotFoundError()
            result = _project_mutation_response(
                session,
                project_id=project_id,
                owner_user_id=principal.owner_user_id,
                changed_fields=sorted(body.model_fields_set - {"change_reason"}),
            )
    else:
        repository = ApplicationWorkspaceRepository(session)
        current = _current_project_version(
            repository, project_id=project_id, owner_user_id=principal.owner_user_id
        )
        target_company_id = (
            body.company_id if "company_id" in body.model_fields_set else current.company_id
        )
        if target_company_id is None:
            raise InvalidInputError(
                fields=(ApiFieldError(field="company_id", reason="MUST_NOT_BE_NULL"),)
            )
        _require_selectable_company(repository, target_company_id)
        target_job_posting_id = (
            body.job_posting_id
            if "job_posting_id" in body.model_fields_set
            else current.job_posting_id
        )
        _require_job_posting_matches_company(repository, target_job_posting_id, target_company_id)
        try:
            ApplicationWorkspaceService(session).append_project_version(
                owner_user_id=principal.owner_user_id,
                project_id=project_id,
                expected_lock_version=expected_version,
                company_id=body.company_id if "company_id" in body.model_fields_set else UNSET,
                title=body.title if "title" in body.model_fields_set else UNSET,
                role_name=body.role_name if "role_name" in body.model_fields_set else UNSET,
                season=body.season if "season" in body.model_fields_set else UNSET,
                organization_name=body.organization_name
                if "organization_name" in body.model_fields_set
                else UNSET,
                job_posting_id=body.job_posting_id
                if "job_posting_id" in body.model_fields_set
                else UNSET,
                change_reason=body.change_reason,
            )
        except ApplicationWorkspaceNotFoundError as error:
            raise ResourceNotFoundError() from error
        except WorkspaceVersionConflictError as error:
            _raise_project_version_conflict(
                repository,
                project_id=project_id,
                owner_user_id=principal.owner_user_id,
                expected_version=expected_version,
                source_error=error,
            )
        except ApplicationWorkspaceError as error:
            raise InvalidInputError() from error
        changed_fields = sorted(body.model_fields_set - {"change_reason"})
        result = _project_mutation_response(
            session,
            project_id=project_id,
            owner_user_id=principal.owner_user_id,
            changed_fields=changed_fields,
        )
        complete_idempotency(
            record,
            response_status=200,
            kind="project",
            resource_id=project_id,
            response_body=result.model_dump(mode="json"),
        )
    response.headers["ETag"] = _etag(result.current_version)
    return result


@router.post("/{project_id}/job-posting", response_model=ProjectMutationResponse)
def link_existing_job_posting(
    project_id: UUID,
    body: ProjectJobPostingLinkRequest,
    response: Response,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    if_match: IfMatch = None,
    idempotency_key: IdempotencyKey = None,
) -> ProjectMutationResponse:
    if body.mode == "OFFICIAL_URL":
        _require_supported_official_url(body.official_url)
        raise ExecutionPolicyUnconfiguredError()
    expected_version = parse_if_match(if_match)
    record, replayed = _reserve(
        session=session,
        owner_user_id=principal.owner_user_id,
        method="POST",
        path_scope=f"/api/v1/application-projects/{project_id}/job-posting",
        idempotency_key=idempotency_key,
        request_body=body.model_dump(mode="json"),
    )
    if replayed:
        result = replay_response(record, ProjectMutationResponse)
        if result is None:
            if replay_resource_id(record, expected_kind="project") != project_id:
                raise ResourceNotFoundError()
            result = _project_mutation_response(
                session,
                project_id=project_id,
                owner_user_id=principal.owner_user_id,
                changed_fields=["job_posting_id"],
            )
    else:
        repository = ApplicationWorkspaceRepository(session)
        current = _current_project_version(
            repository, project_id=project_id, owner_user_id=principal.owner_user_id
        )
        _require_job_posting_matches_company(repository, body.job_posting_id, current.company_id)
        try:
            ApplicationWorkspaceService(session).append_project_version(
                owner_user_id=principal.owner_user_id,
                project_id=project_id,
                expected_lock_version=expected_version,
                job_posting_id=body.job_posting_id,
                change_reason="기존 공고 연결 변경",
            )
        except ApplicationWorkspaceNotFoundError as error:
            raise ResourceNotFoundError() from error
        except WorkspaceVersionConflictError as error:
            _raise_project_version_conflict(
                repository,
                project_id=project_id,
                owner_user_id=principal.owner_user_id,
                expected_version=expected_version,
                source_error=error,
            )
        result = _project_mutation_response(
            session,
            project_id=project_id,
            owner_user_id=principal.owner_user_id,
            changed_fields=["job_posting_id"],
        )
        complete_idempotency(
            record,
            response_status=200,
            kind="project",
            resource_id=project_id,
            response_body=result.model_dump(mode="json"),
        )
    response.headers["ETag"] = _etag(result.current_version)
    return result


@router.get("/{project_id}/versions", response_model=list[ProjectVersionSummaryResponse])
def list_project_versions(
    project_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> list[ProjectVersionSummaryResponse]:
    repository = ApplicationWorkspaceRepository(session)
    project = repository.get_project(project_id=project_id, owner_user_id=principal.owner_user_id)
    if project is None:
        raise ResourceNotFoundError()
    return [
        project_version_summary(version, is_current=version.id == project.current_version_id)
        for version in repository.list_project_versions(
            project_id=project_id, owner_user_id=principal.owner_user_id
        )
    ]


def _project_response(
    session: Session, *, project_id: UUID, owner_user_id: UUID
) -> ProjectResponse:
    repository = ApplicationWorkspaceRepository(session)
    project = repository.get_project(project_id=project_id, owner_user_id=owner_user_id)
    if project is None:
        raise ResourceNotFoundError()
    version = repository.get_current_project_version(project=project, owner_user_id=owner_user_id)
    if version is None:
        raise ResourceNotFoundError()
    return project_response(
        project,
        version,
        is_paused=repository.has_paused_job(project_id=project.id, owner_user_id=owner_user_id),
    )


def _project_mutation_response(
    session: Session,
    *,
    project_id: UUID,
    owner_user_id: UUID,
    changed_fields: list[str],
) -> ProjectMutationResponse:
    return ProjectMutationResponse(
        **_project_response(
            session, project_id=project_id, owner_user_id=owner_user_id
        ).model_dump(),
        changed_fields=changed_fields,
    )


def _current_project_version(
    repository: ApplicationWorkspaceRepository, *, project_id: UUID, owner_user_id: UUID
):
    project = repository.get_project(project_id=project_id, owner_user_id=owner_user_id)
    if project is None:
        raise ResourceNotFoundError()
    version = repository.get_current_project_version(project=project, owner_user_id=owner_user_id)
    if version is None:
        raise ResourceNotFoundError()
    return version


def _require_selectable_company(
    repository: ApplicationWorkspaceRepository, company_id: UUID
) -> None:
    company = repository.get_company(company_id=company_id)
    if company is None:
        raise ResourceNotFoundError()
    if company.identification_status != "VERIFIED":
        raise InvalidInputError(
            fields=(ApiFieldError(field="company_id", reason="NOT_A_RESOLVED_CATALOG_ENTRY"),)
        )


def _require_job_posting_matches_company(
    repository: ApplicationWorkspaceRepository,
    job_posting_id: UUID | None,
    company_id: UUID,
) -> None:
    if job_posting_id is None:
        return
    if repository.get_job_posting(job_posting_id=job_posting_id, company_id=company_id) is None:
        raise InvalidInputError(
            fields=(ApiFieldError(field="job_posting_id", reason="NOT_IN_SELECTED_COMPANY"),)
        )


def _raise_project_version_conflict(
    repository: ApplicationWorkspaceRepository,
    *,
    project_id: UUID,
    owner_user_id: UUID,
    expected_version: int,
    source_error: Exception,
) -> None:
    project = repository.get_project(project_id=project_id, owner_user_id=owner_user_id)
    if project is None:
        raise ResourceNotFoundError() from source_error
    raise VersionConflictApiError(
        expected_version=expected_version, actual_version=project.lock_version
    ) from source_error


def _require_supported_official_url(raw_url: str | None) -> None:
    if raw_url is None:
        raise InvalidInputError(fields=(ApiFieldError(field="official_url", reason="REQUIRED"),))
    parsed = urlsplit(raw_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.port
        not in {
            None,
            80,
            443,
        }
    ):
        raise InvalidInputError(
            fields=(ApiFieldError(field="official_url", reason="UNSUPPORTED_URL"),)
        )


def _reserve(**kwargs):
    try:
        return reserve_idempotency(**kwargs)
    except IdempotencyServiceConflictError as error:
        raise IdempotencyConflictError() from error


def _etag(version: int) -> str:
    return f'"{version}"'
