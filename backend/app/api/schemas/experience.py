from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.experience import FieldAvailability

RegistrationStatus = Literal["DRAFT", "COMPLETED"]
ActivitySort = Literal["updated_at_desc", "start_date_desc", "created_at_desc"]


class FieldValue(BaseModel):
    """A user value together with an explicit reason when it is unavailable."""

    model_config = ConfigDict(extra="forbid")

    value: str | None = None
    availability: FieldAvailability = FieldAvailability.NOT_PROVIDED
    # No note column exists in the immutable repository yet. Accepting arbitrary
    # note text would silently discard user data, so v1 deliberately accepts null only.
    note: None = None

    @model_validator(mode="after")
    def validate_value_matches_availability(self) -> FieldValue:
        is_provided = self.availability == FieldAvailability.PROVIDED
        if is_provided != (self.value is not None):
            raise ValueError("value must match availability")
        return self


class PeriodValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date | None = None
    end_date: date | None = None
    precision: str | None = Field(default=None, max_length=32)
    availability: FieldAvailability = FieldAvailability.NOT_PROVIDED

    @model_validator(mode="after")
    def validate_value_matches_availability(self) -> PeriodValue:
        has_value = any((self.start_date, self.end_date, self.precision))
        is_provided = self.availability == FieldAvailability.PROVIDED
        if is_provided != has_value:
            raise ValueError("period must match availability")
        return self


class OutcomeValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str | None = Field(default=None, max_length=32)
    summary: FieldValue = Field(default_factory=FieldValue)

    @model_validator(mode="after")
    def validate_status(self) -> OutcomeValue:
        allowed_statuses = {
            "SUCCEEDED",
            "PARTIALLY_ACHIEVED",
            "FAILED",
            "IN_PROGRESS",
            "NO_CLEAR_OUTCOME",
        }
        if self.status is not None and self.status not in allowed_statuses:
            raise ValueError("unknown outcome status")
        if self.status is None and self.summary.availability == FieldAvailability.PROVIDED:
            raise ValueError("outcome status is required when a summary is provided")
        return self


class ActivityCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=500)]
    organization: FieldValue = Field(default_factory=FieldValue)
    activity_type: str | None = Field(default=None, max_length=32)
    period: PeriodValue = Field(default_factory=PeriodValue)
    role: FieldValue = Field(default_factory=FieldValue)
    outcome: OutcomeValue = Field(default_factory=OutcomeValue)
    original_narrative: str | None = Field(default=None, max_length=20_000)
    usage_enabled: bool = True


class ActivityUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_reason: Annotated[str, Field(min_length=1, max_length=1_000)]
    title: str | None = Field(default=None, max_length=500)
    organization: FieldValue | None = None
    activity_type: str | None = Field(default=None, max_length=32)
    period: PeriodValue | None = None
    role: FieldValue | None = None
    outcome: OutcomeValue | None = None
    original_narrative: str | None = Field(default=None, max_length=20_000)
    usage_enabled: bool | None = None


class EpisodeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=500)]
    situation: FieldValue = Field(default_factory=FieldValue)
    problem: FieldValue = Field(default_factory=FieldValue)
    goal: FieldValue = Field(default_factory=FieldValue)
    actions: FieldValue = Field(default_factory=FieldValue)
    decisions: FieldValue = Field(default_factory=FieldValue)
    decision_reasons: FieldValue = Field(default_factory=FieldValue)
    result: FieldValue = Field(default_factory=FieldValue)
    learning: FieldValue = Field(default_factory=FieldValue)
    technologies: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        default_factory=list,
        max_length=100,
    )
    original_narrative: str | None = Field(default=None, max_length=20_000)
    usage_enabled: bool = True


class EpisodeUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_reason: Annotated[str, Field(min_length=1, max_length=1_000)]
    title: str | None = Field(default=None, max_length=500)
    situation: FieldValue | None = None
    problem: FieldValue | None = None
    goal: FieldValue | None = None
    actions: FieldValue | None = None
    decisions: FieldValue | None = None
    decision_reasons: FieldValue | None = None
    result: FieldValue | None = None
    learning: FieldValue | None = None
    technologies: list[Annotated[str, Field(min_length=1, max_length=128)]] | None = Field(
        default=None,
        max_length=100,
    )
    original_narrative: str | None = Field(default=None, max_length=20_000)
    usage_enabled: bool | None = None


class CompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sensitive_content_confirmation: None = None


class VersionSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    created_at: datetime
    created_by: str
    change_reason: str | None
    is_current: bool


class ActivityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    current_version: int
    title: str
    organization: FieldValue
    activity_type: str | None
    period: PeriodValue
    role: FieldValue
    outcome: OutcomeValue
    original_narrative: str | None
    registration_status: RegistrationStatus
    usage_enabled: bool
    created_at: datetime
    updated_at: datetime


class ActivityListItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    current_version: int
    title: str
    organization_display: str | None
    activity_type: str | None
    period_display: str | None
    registration_status: RegistrationStatus
    usage_enabled: bool
    episode_count: int
    updated_at: datetime


class ActivityMutationResponse(ActivityResponse):
    changed_fields: list[str] = Field(default_factory=list)
    affected_project_count: int = 0
    reanalyze_available: bool = False


class EpisodeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    activity_id: UUID
    current_version: int
    title: str
    situation: FieldValue
    problem: FieldValue
    goal: FieldValue
    actions: FieldValue
    decisions: FieldValue
    decision_reasons: FieldValue
    result: FieldValue
    learning: FieldValue
    technologies: list[str] = Field(default_factory=list)
    original_narrative: str | None
    registration_status: RegistrationStatus
    usage_enabled: bool
    created_at: datetime
    updated_at: datetime


class EpisodeListItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    activity_id: UUID
    current_version: int
    title: str
    registration_status: RegistrationStatus
    usage_enabled: bool
    updated_at: datetime


class EpisodeMutationResponse(EpisodeResponse):
    changed_fields: list[str] = Field(default_factory=list)
