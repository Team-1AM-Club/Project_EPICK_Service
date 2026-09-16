from __future__ import annotations

from fastapi import APIRouter

from app.api.dependencies import CurrentPrincipalDep, OwnerSessionDep
from app.api.errors import ResourceNotFoundError
from app.api.schemas.users import (
    HomeExperienceStoreSummary,
    HomeNotificationSummary,
    HomeProjectSummary,
    HomeResponse,
    UserMeResponse,
)
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
