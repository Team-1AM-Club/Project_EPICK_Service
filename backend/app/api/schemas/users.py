from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UserMeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    display_name: str
    email: str | None
    account_status: str
    created_at: datetime


class HomeProjectSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    in_progress_count: int
    needs_review_count: int
    recent: list[object] = Field(default_factory=list)


class HomeExperienceStoreSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_count: int
    draft_count: int


class HomeNotificationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unread_count: int
    critical_count: int


class HomeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resume_items: list[object] = Field(default_factory=list)
    projects: HomeProjectSummary
    experience_store: HomeExperienceStoreSummary
    notifications: HomeNotificationSummary
    running_job_count: int
