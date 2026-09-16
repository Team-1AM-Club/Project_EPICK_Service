from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

RecommendationRunStatus = Literal[
    "PENDING", "RUNNING", "SUCCEEDED", "LIMITED", "FAILED", "CANCELLED"
]
RecommendationResultStatus = Literal["PENDING", "READY", "LIMITED", "FAILED"]
RecommendationResultOrigin = Literal["SYNTHETIC", "ENGINE"]
CandidateMatchStatus = Literal[
    "DIRECT_MATCH", "PARTIAL_RELEVANCE", "NEEDS_VERIFICATION", "NO_RELEVANT_EVIDENCE"
]
CandidateValidationStatus = Literal["PENDING", "PASSED", "LIMITED", "FAILED"]


class RecommendationRunCreateRequest(BaseModel):
    """Version fences for freezing a new synthetic recommendation input."""

    model_config = ConfigDict(extra="forbid")

    question_version: Annotated[int, Field(ge=1)]
    # ``0`` is the explicit first-run value.  Otherwise this must equal the
    # currently active ProjectSnapshot number before a new snapshot is frozen.
    snapshot_version: Annotated[int, Field(ge=0)]
    candidate_limit: Annotated[int, Field(ge=1, le=20)] = 5
    include_excluded: bool = False
    allow_limited_analysis: bool = True


class RecommendationRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    project_id: UUID
    question_id: UUID
    question_version: int
    snapshot_id: UUID
    snapshot_version: int
    status: RecommendationRunStatus
    result_status: RecommendationResultStatus
    result_origin: RecommendationResultOrigin
    requested_candidate_limit: int
    limited_analysis: bool
    limitations: list[str] = Field(default_factory=list)
    candidates_url: str
    created_at: datetime
    completed_at: datetime | None


class RecommendationCandidateResponse(BaseModel):
    """The W4-independent, persisted Candidate summary surface only."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    candidate_no: int
    episode_version_id: UUID
    match_status: CandidateMatchStatus
    short_reason: str
    strength_summary: str | None
    limitation_summary: str | None
    validation_status: CandidateValidationStatus
    result_version: str
