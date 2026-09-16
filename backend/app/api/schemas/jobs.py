from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

JobStatus = Literal[
    "QUEUED",
    "RUNNING",
    "WAITING_USER",
    "PAUSED_RATE_LIMIT",
    "SUCCEEDED",
    "FAILED_RETRYABLE",
    "FAILED_FINAL",
    "CANCEL_REQUESTED",
    "CANCELLED",
]
JobCompleteness = Literal["none", "partial", "complete"]
DispatchStatus = Literal["OUTBOX_PENDING", "ENQUEUED", "CLAIMED", "BLOCKED", "INVALIDATED"]
JobActionCode = Literal["RETRY", "CONTINUE_LIMITED", "STOP"]


class JobInputRefResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    id: UUID | str
    version: int | str


class JobProgressResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    completed_units: int
    total_units: int | None
    percent: int | None


class JobRequiredActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    code: JobActionCode
    status: Literal["OPEN"]
    context_code: str | None
    expected_input_version: str | None
    expected_result_version: str | None


class JobCheckpointResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    last_completed_stage: str | None = None
    analysis_input_version: str | None = None


class JobFailureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str | None
    message: str | None
    retryable: bool
    retry_after_seconds: int | None


class JobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    job_type: str
    status: JobStatus
    completeness: JobCompleteness
    dispatch_status: DispatchStatus
    stage: str | None
    input_refs: list[JobInputRefResponse] = Field(default_factory=list)
    progress: JobProgressResponse
    required_actions: list[JobRequiredActionResponse] = Field(default_factory=list)
    checkpoint: JobCheckpointResponse
    failure: JobFailureResponse
    limitations: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class JobListItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    job_type: str
    status: JobStatus
    completeness: JobCompleteness
    dispatch_status: DispatchStatus
    stage: str | None
    progress: JobProgressResponse
    required_action_count: int
    created_at: datetime
    updated_at: datetime


class JobActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_action_id: UUID
    action: JobActionCode
    expected_input_version: str | None = Field(..., max_length=64)
    expected_result_version: str | None = Field(..., max_length=64)
    acknowledge_rate_limit: bool = False


class JobRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    required_action_id: UUID
    from_stage: Annotated[str, Field(min_length=1, max_length=64)]
    expected_input_version: str | None = Field(..., max_length=64)
    expected_result_version: str | None = Field(..., max_length=64)
    acknowledge_rate_limit: bool = False


class JobCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
