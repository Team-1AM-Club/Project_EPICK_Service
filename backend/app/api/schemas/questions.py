from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

QuestionSource = Literal["USER_INPUT", "OFFICIAL_POSTING"]


class QuestionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: Annotated[str, Field(min_length=1, max_length=20_000)]
    character_limit: Annotated[int | None, Field(gt=0, le=100_000)] = None
    display_order: Annotated[int, Field(ge=0)]
    source: QuestionSource = "USER_INPUT"


class QuestionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: Annotated[str | None, Field(min_length=1, max_length=20_000)] = None
    character_limit: Annotated[int | None, Field(gt=0, le=100_000)] = None
    source: QuestionSource | None = None

    @model_validator(mode="after")
    def require_a_question_change(self) -> QuestionUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("at least one question field must be provided")
        return self


class QuestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    project_id: UUID
    current_version: int
    prompt: str
    character_limit: int | None
    display_order: int
    source: QuestionSource
    status: Literal["ACTIVE", "ARCHIVED"]
    created_at: datetime
    updated_at: datetime


class QuestionListItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    current_version: int
    prompt: str
    character_limit: int | None
    display_order: int
    source: QuestionSource
    updated_at: datetime


class QuestionMutationResponse(QuestionResponse):
    changed_fields: list[str] = Field(default_factory=list)


class QuestionVersionSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    created_at: datetime
    is_current: bool
