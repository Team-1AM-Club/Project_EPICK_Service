from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CandidateSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: UUID
    recommendation_run_id: UUID
    result_version: Annotated[str, Field(min_length=1, max_length=64)]
    replace_existing: bool = False


class EpisodeReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_id: UUID
    version: int


class SelectionWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["WARNING"]
    code: str
    message: str


class MaterialSelectionResponse(BaseModel):
    """Current single-candidate selection exposed by the synthetic M1 flow."""

    model_config = ConfigDict(extra="forbid")

    selection_id: UUID
    project_id: UUID
    question_id: UUID
    recommendation_run_id: UUID
    candidate_id: UUID
    episode_ref: EpisodeReference
    selected_at: datetime
    warnings: list[SelectionWarning] = Field(default_factory=list)
