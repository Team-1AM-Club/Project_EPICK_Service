from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.dependencies import CurrentPrincipalDep, OwnerSessionDep
from app.api.errors import ResourceNotFoundError
from app.api.schemas.common import CursorListResponse
from app.api.schemas.companies import CompanyResponse, JobPostingListItemResponse
from app.api.v1.workspace_common import cursor_offset, next_cursor
from app.repo.application_workspace import ApplicationWorkspaceRepository

router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("", response_model=CursorListResponse[CompanyResponse])
def list_companies(
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    query: Annotated[str | None, Query(max_length=200)] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CursorListResponse[CompanyResponse]:
    # Catalog reads intentionally accept an authenticated principal but never
    # create Company/Source data or initiate a collection Job.
    del principal
    offset = cursor_offset(cursor=cursor, resource="companies")
    rows = ApplicationWorkspaceRepository(session).list_companies(
        query=query.strip() if query else None, offset=offset, limit=limit + 1
    )
    visible_rows = rows[:limit]
    return CursorListResponse(
        items=[_company_response(company) for company in visible_rows],
        next_cursor=next_cursor(
            resource="companies",
            offset=offset,
            returned_count=len(visible_rows),
            has_next=len(rows) > limit,
        ),
    )


@router.get("/{company_id}", response_model=CompanyResponse)
def get_company(
    company_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
) -> CompanyResponse:
    del principal
    company = ApplicationWorkspaceRepository(session).get_company(company_id=company_id)
    if company is None or company.identification_status == "REJECTED":
        raise ResourceNotFoundError()
    return _company_response(company)


@router.get(
    "/{company_id}/job-postings",
    response_model=CursorListResponse[JobPostingListItemResponse],
)
def list_company_job_postings(
    company_id: UUID,
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CursorListResponse[JobPostingListItemResponse]:
    del principal
    repository = ApplicationWorkspaceRepository(session)
    company = repository.get_company(company_id=company_id)
    if company is None or company.identification_status == "REJECTED":
        raise ResourceNotFoundError()
    offset = cursor_offset(cursor=cursor, resource=f"company-job-postings:{company_id}")
    rows = repository.list_current_job_postings(
        company_id=company_id, offset=offset, limit=limit + 1
    )
    visible_rows = rows[:limit]
    return CursorListResponse(
        items=[
            JobPostingListItemResponse(
                id=posting.id,
                title=version.title,
                status=posting.status,
                role_display=version.role_name,
                published_at=version.published_at,
                source_version_ref=str(version.source_version_id),
            )
            for posting, version in visible_rows
        ],
        next_cursor=next_cursor(
            resource=f"company-job-postings:{company_id}",
            offset=offset,
            returned_count=len(visible_rows),
            has_next=len(rows) > limit,
        ),
    )


def _company_response(company) -> CompanyResponse:
    return CompanyResponse(
        id=company.id,
        display_name=company.display_name,
        official_domain=company.official_domain,
        identification_status=company.identification_status,
    )
