from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import CurrentPrincipalDep, OwnerSessionDep
from app.api.errors import ResourceNotFoundError
from app.api.pagination import validate_page_limit
from app.api.schemas.common import CursorListResponse
from app.api.schemas.users import (
    HomeExperienceStoreSummary,
    HomeNotificationSummary,
    HomeProjectSummary,
    HomeResponse,
    ResumeItemResponse,
    ResumeItemType,
    UserMeResponse,
)
from app.api.v1.workspace_common import cursor_offset, next_cursor
from app.repo.application_workspace import ApplicationWorkspaceRepository

router = APIRouter(tags=["users"])


@router.get("/users/me", response_model=UserMeResponse)
def get_current_user(principal: CurrentPrincipalDep, session: OwnerSessionDep) -> UserMeResponse:
    user = ApplicationWorkspaceRepository(session).get_user(owner_user_id=principal.owner_user_id)
    if user is None:
        raise ResourceNotFoundError()
    return UserMeResponse(
        id=user.id,
        display_name=user.display_name,
        email=user.email,
        account_status=user.account_status,
        created_at=user.created_at,
    )


@router.get("/home", response_model=HomeResponse)
def get_home(principal: CurrentPrincipalDep, session: OwnerSessionDep) -> HomeResponse:
    repository = ApplicationWorkspaceRepository(session)
    if repository.get_user(owner_user_id=principal.owner_user_id) is None:
        raise ResourceNotFoundError()
    counts = repository.home_counts(owner_user_id=principal.owner_user_id)
    return HomeResponse(
        resume_items=[
            _resume_item_response(item)
            for item in repository.list_resume_items(
                owner_user_id=principal.owner_user_id,
                resume_type=None,
                offset=0,
                limit=5,
            )
        ],
        projects=HomeProjectSummary(
            in_progress_count=counts["in_progress_count"],
            needs_review_count=counts["needs_review_count"],
        ),
        experience_store=HomeExperienceStoreSummary(
            activity_count=counts["activity_count"],
            draft_count=counts["draft_count"],
        ),
        notifications=HomeNotificationSummary(
            unread_count=counts["unread_count"],
            critical_count=counts["critical_count"],
        ),
        running_job_count=counts["running_job_count"],
    )


@router.get("/resume-items", response_model=CursorListResponse[ResumeItemResponse])
def list_resume_items(
    principal: CurrentPrincipalDep,
    session: OwnerSessionDep,
    resume_type: Annotated[ResumeItemType | None, Query(alias="type")] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query()] = 20,
) -> CursorListResponse[ResumeItemResponse]:
    repository = ApplicationWorkspaceRepository(session)
    if repository.get_user(owner_user_id=principal.owner_user_id) is None:
        raise ResourceNotFoundError()
    page_limit = validate_page_limit(limit)
    resource = f"resume-items:{resume_type or 'ALL'}"
    offset = cursor_offset(cursor=cursor, resource=resource)
    items = repository.list_resume_items(
        owner_user_id=principal.owner_user_id,
        resume_type=resume_type,
        offset=offset,
        limit=page_limit + 1,
    )
    visible = items[:page_limit]
    return CursorListResponse(
        items=[_resume_item_response(item) for item in visible],
        next_cursor=next_cursor(
            resource=resource,
            offset=offset,
            returned_count=len(visible),
            has_next=len(items) > page_limit,
        ),
    )


def _resume_item_response(item) -> ResumeItemResponse:
    return ResumeItemResponse(
        resource_type=item.resource_type,
        resource_id=item.resource_id,
        title=item.title,
        current_step=item.current_step,
        updated_at=item.updated_at,
        resume_url=item.resume_url,
        blocking_reason=item.blocking_reason,
    )
