from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ProjectPublicStatus = Literal[
    "DRAFT", "IN_PROGRESS", "PAUSED", "MATERIALS_SELECTED", "NEEDS_REVIEW", "ARCHIVED"
]


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=500)]
    company_id: UUID
    job_posting_id: UUID | None = None
    season: Annotated[str | None, Field(max_length=64)] = None
    organization_name: Annotated[str | None, Field(max_length=500)] = None
    role_name: Annotated[str, Field(min_length=1, max_length=500)]
    # URL collection is an egress-gated Job and must not be silently persisted
    # by the normal Project creation path.
    official_job_posting_url: None = None


class ProjectUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_reason: Annotated[str, Field(min_length=1, max_length=1_000)]
    title: Annotated[str | None, Field(max_length=500)] = None
    company_id: UUID | None = None
    job_posting_id: UUID | None = None
    season: Annotated[str | None, Field(max_length=64)] = None
    organization_name: Annotated[str | None, Field(max_length=500)] = None
    role_name: Annotated[str | None, Field(max_length=500)] = None
    official_job_posting_url: None = None

    @model_validator(mode="after")
    def require_a_project_change(self) -> ProjectUpdateRequest:
        if self.model_fields_set <= {"change_reason"}:
            raise ValueError("at least one project field must be provided")
        return self


class ProjectJobPostingLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["EXISTING", "OFFICIAL_URL"]
    job_posting_id: UUID | None = None
    official_url: str | None = Field(default=None, max_length=2_048)

    @model_validator(mode="after")
    def validate_mode_payload(self) -> ProjectJobPostingLinkRequest:
        is_existing_link = self.job_posting_id is not None and self.official_url is None
        if self.mode == "EXISTING" and is_existing_link:
            return self
        is_official_url_link = self.official_url is not None and self.job_posting_id is None
        if self.mode == "OFFICIAL_URL" and is_official_url_link:
            return self
        raise ValueError("job posting link fields do not match mode")


class ProjectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    current_version: int
    title: str
    company_id: UUID
    job_posting_id: UUID | None
    season: str | None
    organization_name: str | None
    role_name: str
    status: ProjectPublicStatus
    current_step: str | None
    created_at: datetime
    updated_at: datetime


class ProjectListItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    current_version: int
    title: str
    company_id: UUID
    status: ProjectPublicStatus
    current_step: str | None
    updated_at: datetime


class ProjectMutationResponse(ProjectResponse):
    changed_fields: list[str] = Field(default_factory=list)


class ProjectVersionSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    created_at: datetime
    change_reason: str | None
    is_current: bool
