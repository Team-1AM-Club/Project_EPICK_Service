from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UserSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locale: str
    timezone: str
    display_options: dict[str, object]
    version: int
    updated_at: datetime


class UserSettingsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locale: Annotated[str | None, Field(min_length=1, max_length=32)] = None
    timezone: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    display_options: dict[str, object] | None = None

    @model_validator(mode="after")
    def require_change(self) -> UserSettingsUpdateRequest:
        if self.locale is None and self.timezone is None and self.display_options is None:
            raise ValueError("at least one setting is required")
        return self


class RecommendationPreferenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_candidate_limit: int
    question_display_mode: str
    evidence_display_mode: str
    show_information_completeness: bool
    version: int
    updated_at: datetime


class RecommendationPreferenceUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_candidate_limit: Annotated[int | None, Field(ge=1, le=100)] = None
    question_display_mode: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    evidence_display_mode: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    show_information_completeness: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> RecommendationPreferenceUpdateRequest:
        if (
            self.default_candidate_limit is None
            and self.question_display_mode is None
            and self.evidence_display_mode is None
            and self.show_information_completeness is None
        ):
            raise ValueError("at least one preference is required")
        return self


ExclusionScope = Literal["GLOBAL", "COMPANY", "ROLE", "PROJECT"]


class ExperienceExclusionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_id: UUID | None = None
    episode_id: UUID | None = None
    scope: ExclusionScope
    company_id: UUID | None = None
    role_id: UUID | None = None
    project_id: UUID | None = None
    reason: Annotated[str | None, Field(min_length=1, max_length=1024)] = None

    @model_validator(mode="after")
    def validate_target_and_scope(self) -> ExperienceExclusionCreateRequest:
        if (self.activity_id is None) == (self.episode_id is None):
            raise ValueError("exactly one of activity_id and episode_id is required")
        contexts = (self.company_id, self.role_id, self.project_id)
        if self.scope == "GLOBAL" and any(item is not None for item in contexts):
            raise ValueError("GLOBAL accepts no context")
        if self.scope == "COMPANY" and not (
            self.company_id is not None and self.role_id is None and self.project_id is None
        ):
            raise ValueError("COMPANY requires company_id only")
        if self.scope == "ROLE" and not (
            self.company_id is None and self.role_id is not None and self.project_id is None
        ):
            raise ValueError("ROLE requires role_id only")
        if self.scope == "PROJECT" and not (
            self.company_id is None and self.role_id is None and self.project_id is not None
        ):
            raise ValueError("PROJECT requires project_id only")
        return self


class ExperienceExclusionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    activity_id: UUID | None
    episode_id: UUID | None
    scope: ExclusionScope
    company_id: UUID | None
    role_id: UUID | None
    project_id: UUID | None
    reason: str | None
    created_at: datetime
    revoked_at: datetime | None


class ConsentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ANALYTICS"]
    opted_in: bool
    policy_version: str | None
    decided_at: datetime | None


class AnalyticsConsentUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    opted_in: bool
    policy_version: Annotated[str, Field(min_length=1, max_length=64)]


class RetentionPolicyStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["PENDING_CONFIGURATION"]


Helpfulness = Literal["HELPFUL", "NOT_HELPFUL", "NOT_SURE"]


class FeedbackResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    category_l1: str
    category_l2: str | None
    decision_helpfulness: Helpfulness | None
    run_id: UUID | None
    missing_activity_id: UUID | None
    missing_episode_id: UUID | None
    created_at: datetime


class FeedbackCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_l1: Annotated[str, Field(min_length=1, max_length=64)]
    category_l2: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    decision_helpfulness: Helpfulness | None = None
    other_text: Annotated[str | None, Field(min_length=1, max_length=4096)] = None


class RunFeedbackCreateRequest(FeedbackCreateRequest):
    pass


class MissingCandidateFeedbackCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_id: UUID
    category_l2: Annotated[str | None, Field(min_length=1, max_length=64)] = None
    other_text: Annotated[str | None, Field(min_length=1, max_length=4096)] = None
