from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.common import ApiErrorResponse
from app.api.schemas.jobs import JobProgressResponse, JobStatus


class SourceCollectionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["OFFICIAL_URL"]
    official_url: Annotated[str, Field(min_length=1, max_length=2048)]
    purpose: Literal["COMPANY_PROFILE", "JOB_POSTING"]


class SourceCollectionAcceptanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    source_id: UUID
    status: Literal["QUEUED", "RUNNING", "WAITING_USER", "PAUSED_RATE_LIMIT"]
    replayed: bool


class SourceCollectionProgressResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    source_id: UUID
    status: JobStatus
    stage: str | None
    progress: JobProgressResponse


class SourceCollectionErrorResponse(ApiErrorResponse):
    """Named public error envelope for generated source-collection clients."""
