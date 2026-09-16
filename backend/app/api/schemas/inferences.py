from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

InferenceStatus = Literal["PENDING", "DECIDED", "SUPERSEDED"]
InferenceDecisionKind = Literal["APPROVED", "MODIFIED", "REJECTED"]
DuplicateStatus = Literal["PENDING", "DECIDED", "SUPERSEDED"]
DuplicateDecisionKind = Literal["KEEP_SEPARATE", "DISMISSED"]


class InferenceJobStartRequest(BaseModel):
    """Acceptance shape reserved for the future inference worker boundary."""

    model_config = ConfigDict(extra="forbid")

    episode_version: Annotated[int, Field(ge=1)]
    inference_types: Annotated[list[str], Field(min_length=1, max_length=16)]
    replace_pending: bool = False


class InferenceSourceReferenceResponse(BaseModel):
    """Reference metadata only; never the underlying source text."""

    model_config = ConfigDict(extra="forbid")

    field_name: str | None
    source_span_start: int | None
    source_span_end: int | None
    reference_type: Literal["EPISODE_VERSION", "SOURCE_VERSION", "EVIDENCE_SPAN"]


class InferenceDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    decision: InferenceDecisionKind
    modified_value: dict[str, object] | None
    reason: str | None
    decided_at: datetime


class InferenceSuggestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    episode_id: UUID
    episode_version_id: UUID
    suggestion_type: str
    proposed_value: dict[str, object]
    status: InferenceStatus
    sources: list[InferenceSourceReferenceResponse] = Field(default_factory=list)
    latest_decision: InferenceDecisionResponse | None
    created_at: datetime


class InferenceDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: InferenceDecisionKind
    modified_value: dict[str, object] | None = None
    reason: Annotated[str | None, Field(max_length=4096)] = None

    @model_validator(mode="after")
    def validate_modified_value(self) -> InferenceDecisionRequest:
        if self.decision == "MODIFIED" and self.modified_value is None:
            raise ValueError("MODIFIED requires modified_value")
        if self.decision != "MODIFIED" and self.modified_value is not None:
            raise ValueError("modified_value is allowed only for MODIFIED")
        return self


class DuplicateDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    decision: DuplicateDecisionKind
    reason: str | None
    decided_at: datetime


class DuplicateSuggestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    left_episode_id: UUID
    left_episode_version_id: UUID
    right_episode_id: UUID
    right_episode_version_id: UUID
    reason: str
    status: DuplicateStatus
    latest_decision: DuplicateDecisionResponse | None
    created_at: datetime


class DuplicateDecisionRequest(BaseModel):
    """Merge is intentionally absent: MVP has no public merge execution path."""

    model_config = ConfigDict(extra="forbid")

    decision: DuplicateDecisionKind
    reason: Annotated[str | None, Field(max_length=4096)] = None
