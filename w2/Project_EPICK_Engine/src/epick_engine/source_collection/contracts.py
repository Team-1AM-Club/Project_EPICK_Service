"""Runtime models for the adopted W2 source-collection contract v0.2."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AnyUrl,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

NonEmptyStr = Annotated[str, Field(min_length=1)]
PositiveInt = Annotated[int, Field(ge=1)]
NonNegativeInt = Annotated[int, Field(ge=0)]
Sha256Hex = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class ContractModel(BaseModel):
    """Strict immutable base for externally exchanged contract values."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class DateStatus(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    CONFLICTING = "conflicting"
    NOT_APPLICABLE = "not_applicable"


class DatePrecision(StrEnum):
    YEAR = "year"
    MONTH = "month"
    DATE = "date"
    DATETIME = "datetime"


class DateValue(ContractModel):
    status: DateStatus
    raw_text: NonEmptyStr | None
    value: NonEmptyStr | None
    precision: DatePrecision | None
    timezone: NonEmptyStr | None

    @model_validator(mode="after")
    def validate_known_value(self) -> DateValue:
        if self.status is DateStatus.KNOWN:
            if self.value is None or self.precision is None:
                raise ValueError("known date requires value and precision")
        elif self.value is not None or self.precision is not None:
            raise ValueError("unknown, conflicting, and not_applicable dates cannot invent a value")
        return self


class LocatorKind(StrEnum):
    CSS = "css"
    XPATH = "xpath"
    JSON_POINTER = "json_pointer"
    NORMALIZED_TEXT = "normalized_text"


class Locator(ContractModel):
    kind: LocatorKind
    value: NonEmptyStr
    normalization_version: NonEmptyStr | None
    start: NonNegativeInt | None
    end: NonNegativeInt | None

    @model_validator(mode="after")
    def validate_range(self) -> Locator:
        has_start = self.start is not None
        has_end = self.end is not None
        if has_start != has_end:
            raise ValueError("locator start and end must be provided together")
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise ValueError("locator end must be greater than start")
        if self.kind is LocatorKind.NORMALIZED_TEXT:
            if self.normalization_version is None or not has_start:
                raise ValueError(
                    "normalized_text locator requires normalization_version, start, and end"
                )
        return self


class Evidence(ContractModel):
    evidence_id: UUID
    source_version_id: UUID
    section_title: NonEmptyStr | None
    text_excerpt: NonEmptyStr
    locator: Locator
    chunk_order: NonNegativeInt


class PostingSectionKind(StrEnum):
    TITLE = "title"
    ROLE = "role"
    ORGANIZATION = "organization"
    DUTIES = "duties"
    REQUIRED = "required"
    PREFERRED = "preferred"
    GENERAL = "general"
    LOCATION = "location"
    EMPLOYMENT_TYPE = "employment_type"
    PUBLISHED = "published"
    DEADLINE = "deadline"


class PostingSection(ContractModel):
    section_key: NonEmptyStr
    kind: PostingSectionKind
    heading_raw: NonEmptyStr | None
    text_raw: NonEmptyStr
    evidence_ids: Annotated[list[UUID], Field(min_length=1)]
    order: NonNegativeInt
    relation_text: NonEmptyStr | None

    @field_validator("evidence_ids")
    @classmethod
    def evidence_ids_are_unique(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("posting section evidence_ids must be unique")
        return value


class OfficialStatus(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    REJECTED = "rejected"


class AccessClass(StrEnum):
    PUBLIC = "public"
    RESTRICTED = "restricted"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class Permission(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    UNKNOWN = "unknown"


class Policy(ContractModel):
    policy_decision_id: UUID
    official_status: OfficialStatus
    access_class: AccessClass
    collection_permission: Permission
    excerpt_storage_permission: Permission
    body_storage_permission: Permission
    redistribution_permission: Permission
    checked_at: AwareDatetime
    policy_version: NonEmptyStr


class SourceType(StrEnum):
    COMPANY_WEBSITE = "company_website"
    JOB_POSTING = "job_posting"
    PRESS_RELEASE = "press_release"
    EXECUTIVE_MESSAGE = "executive_message"
    IR_DISCLOSURE = "ir_disclosure"
    OFFICIAL_API = "official_api"


class AcquisitionStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    PARTIALLY_EXTRACTED = "PARTIALLY_EXTRACTED"
    ACCESS_DENIED = "ACCESS_DENIED"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"


class ExtractionStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    NOT_ATTEMPTED = "not_attempted"


class AccuracyStatus(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED_IN_SCOPE = "verified_in_scope"
    ERROR_CONFIRMED = "error_confirmed"
    SUPERSEDED = "superseded"


class FreshnessStatus(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"


class Representation(StrEnum):
    STATIC_HTML = "static_html"
    RENDERED_HTML = "rendered_html"
    OFFICIAL_JSON = "official_json"


class RetentionScope(StrEnum):
    EXCERPTS_ONLY = "excerpts_only"
    NORMALIZED_BODY = "normalized_body"


class SourceMetadata(ContractModel):
    representation: Representation
    content_type: NonEmptyStr
    normalization_version: NonEmptyStr


class SourceEnvelope(ContractModel):
    schema_version: Literal["w2.source.v1"]
    source_id: UUID
    source_version_id: UUID
    extraction_revision_id: UUID
    company_id: UUID
    source_type: SourceType
    url_or_path: AnyUrl
    title: NonEmptyStr | None
    policy: Policy
    acquisition_status: Literal[
        AcquisitionStatus.AVAILABLE,
        AcquisitionStatus.PARTIALLY_EXTRACTED,
    ]
    extraction_status: Literal[ExtractionStatus.COMPLETE, ExtractionStatus.PARTIAL]
    accuracy_status: AccuracyStatus
    freshness_status: FreshnessStatus
    published_at: DateValue
    collected_at: AwareDatetime
    checked_at: AwareDatetime
    valid_from: DateValue
    valid_to: DateValue
    content_hash: Sha256Hex
    hash_profile_version: NonEmptyStr
    parser_version: NonEmptyStr
    language: NonEmptyStr | None
    evidence_spans: Annotated[list[Evidence], Field(min_length=1)]
    posting_sections: list[PostingSection]
    retention_scope: RetentionScope
    normalized_body_ref: UUID | None
    limitations: list[NonEmptyStr]
    metadata: SourceMetadata

    @model_validator(mode="after")
    def validate_envelope_consistency(self) -> SourceEnvelope:
        if self.policy.collection_permission is not Permission.ALLOWED:
            raise ValueError("source envelope requires allowed collection_permission")
        if self.policy.excerpt_storage_permission is not Permission.ALLOWED:
            raise ValueError("source envelope requires allowed excerpt_storage_permission")

        if self.retention_scope is RetentionScope.EXCERPTS_ONLY:
            if self.normalized_body_ref is not None:
                raise ValueError("excerpts_only retention cannot include normalized_body_ref")
        elif (
            self.normalized_body_ref is None
            or self.policy.body_storage_permission is not Permission.ALLOWED
        ):
            raise ValueError(
                "normalized_body retention requires a body reference and body storage permission"
            )

        expected_extraction = (
            ExtractionStatus.COMPLETE
            if self.acquisition_status is AcquisitionStatus.AVAILABLE
            else ExtractionStatus.PARTIAL
        )
        if self.extraction_status is not expected_extraction:
            raise ValueError("acquisition_status and extraction_status do not match")

        evidence_ids: set[UUID] = set()
        for evidence in self.evidence_spans:
            if evidence.source_version_id != self.source_version_id:
                raise ValueError("evidence source_version_id does not match envelope")
            if evidence.evidence_id in evidence_ids:
                raise ValueError("source envelope evidence_id must be unique")
            evidence_ids.add(evidence.evidence_id)
        for section in self.posting_sections:
            if not set(section.evidence_ids) <= evidence_ids:
                raise ValueError("posting section references unknown evidence_id")
        return self


class CollectionStage(StrEnum):
    POLICY = "policy"
    FETCH = "fetch"
    PARSE = "parse"
    PERSIST = "persist"
    DELIVER = "deliver"


class CoreSourceDecision(ContractModel):
    is_core: bool
    decided_by: NonEmptyStr
    rationale: NonEmptyStr
    decision_revision: PositiveInt
    analysis_input_version: PositiveInt


class CollectionCommand(ContractModel):
    schema_version: Literal["w2.collection.v1"]
    command_id: UUID
    job_id: UUID
    authenticated_owner_ref: UUID
    project_ref: str | None
    company_id: UUID
    source_id: UUID
    input_version: PositiveInt
    execution_fence: NonEmptyStr
    purpose_ref: UUID
    core_source_decision: CoreSourceDecision
    resume_stage: CollectionStage
    policy_revision: PositiveInt | None
    owner_deletion_epoch: NonNegativeInt

    @model_validator(mode="after")
    def validate_command_revisions(self) -> CollectionCommand:
        if self.core_source_decision.analysis_input_version != self.input_version:
            raise ValueError("core source analysis_input_version must match command input_version")
        if self.resume_stage is not CollectionStage.POLICY and self.policy_revision is None:
            raise ValueError("non-policy resume requires policy_revision")
        return self


class SourceReference(ContractModel):
    source_id: UUID
    source_version_id: UUID
    extraction_revision_id: UUID


class Failure(ContractModel):
    source_id: UUID
    stage: CollectionStage
    code: NonEmptyStr
    missing_sections: list[NonEmptyStr]
    impact: NonEmptyStr
    core_decision_revision: PositiveInt


class CompanyCandidate(ContractModel):
    company_id: UUID
    legal_name: NonEmptyStr
    identity_evidence_refs: list[NonEmptyStr]


class CoreFailureDecisionContext(ContractModel):
    source_id: UUID
    core_decision_revision: PositiveInt
    choices: tuple[
        Literal["continue_limited"],
        Literal["stop"],
        Literal["retry"],
    ]


class UserRetryContext(ContractModel):
    source_id: UUID
    resume_stage: CollectionStage
    retry_not_before: AwareDatetime | None


class CorrectInputContext(ContractModel):
    source_id: UUID
    reason_code: NonEmptyStr
    candidates: list[CompanyCandidate]
    identity_evidence_refs: list[NonEmptyStr]


class AlternativeSourceReason(StrEnum):
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    NOT_FOUND = "NOT_FOUND"
    ACCESS_DENIED = "ACCESS_DENIED"
    SOURCE_POLICY_BLOCKED = "SOURCE_POLICY_BLOCKED"


class FindAlternativeSourceContext(ContractModel):
    source_id: UUID
    reason_code: AlternativeSourceReason


class CoreFailureDecisionAction(ContractModel):
    code: Literal["core_failure_decision"]
    label_ko: NonEmptyStr
    context: CoreFailureDecisionContext


class UserRetryAction(ContractModel):
    code: Literal["user_retry"]
    label_ko: NonEmptyStr
    context: UserRetryContext


class CorrectInputAction(ContractModel):
    code: Literal["correct_input"]
    label_ko: NonEmptyStr
    context: CorrectInputContext


class FindAlternativeSourceAction(ContractModel):
    code: Literal["find_alternative_source"]
    label_ko: NonEmptyStr
    context: FindAlternativeSourceContext


type RequiredAction = Annotated[
    CoreFailureDecisionAction | UserRetryAction | CorrectInputAction | FindAlternativeSourceAction,
    Field(discriminator="code"),
]


class CompletionKind(StrEnum):
    NONE = "none"
    PARTIAL = "partial"
    COMPLETE = "complete"


class CollectionResult(ContractModel):
    schema_version: Literal["w2.collection.v1"]
    command_id: UUID
    job_id: UUID
    input_version: PositiveInt
    result_version: PositiveInt
    successful_source_refs: Annotated[list[SourceReference], Field(max_length=1)]
    failures: list[Failure]
    completion_kind: CompletionKind
    resume_stage: CollectionStage | None
    checkpoint_ref: str | None
    retry_not_before: AwareDatetime | None
    message_ko: NonEmptyStr
    required_actions: list[RequiredAction]
    source_id: UUID
    policy_revision: PositiveInt | None

    @model_validator(mode="after")
    def validate_result_consistency(self) -> CollectionResult:
        successes = len(self.successful_source_refs)
        failures = len(self.failures)
        if self.completion_kind is CompletionKind.COMPLETE and (successes != 1 or failures != 0):
            raise ValueError("complete result requires one success and no failures")
        if self.completion_kind is CompletionKind.NONE and (successes != 0 or failures == 0):
            raise ValueError("none result requires failures and no success")
        if self.completion_kind is CompletionKind.PARTIAL and (successes != 1 or failures == 0):
            raise ValueError("partial result requires one success and failures")
        if self.policy_revision is None:
            if self.completion_kind is not CompletionKind.NONE or any(
                failure.stage is not CollectionStage.POLICY for failure in self.failures
            ):
                raise ValueError("null policy_revision only permits policy-stage failure")

        for reference in self.successful_source_refs:
            if reference.source_id != self.source_id:
                raise ValueError("successful source reference source_id does not match result")
        for failure in self.failures:
            if failure.source_id != self.source_id:
                raise ValueError("failure source_id does not match result source_id")

        action_codes: set[str] = set()
        for action in self.required_actions:
            if action.code in action_codes:
                raise ValueError("duplicate required action code")
            action_codes.add(action.code)
            if action.context.source_id != self.source_id:
                raise ValueError("required action source_id does not match result source_id")
        return self


class SourceObservationSnapshot(ContractModel):
    observation_id: UUID
    source_id: UUID
    source_version_id: UUID | None
    policy_decision_id: UUID | None
    observed_at: AwareDatetime
    access_class: AccessClass
    acquisition_status: AcquisitionStatus | None
    http_status: Annotated[int, Field(ge=100, le=599)] | None
    checked_url: AnyUrl
    error_code: NonEmptyStr | None
    representation: Representation | None


class RestrictionStatus(StrEnum):
    ACTIVE = "active"
    CLEARED = "cleared"


class SourceRestrictionSnapshot(ContractModel):
    restriction_id: UUID
    source_id: UUID
    source_version_id: UUID | None
    restriction_revision: PositiveInt
    restriction_status: RestrictionStatus
    accuracy_status: AccuracyStatus
    reason_code: NonEmptyStr
    changed_at: AwareDatetime
    replacement_ref: UUID | None


type SourceEventPayload = SourceEnvelope | SourceObservationSnapshot | SourceRestrictionSnapshot


class SourceEventType(StrEnum):
    VERSION_AVAILABLE = "source.version.available"
    OBSERVATION_CHANGED = "source.observation.changed"
    RESTRICTION_CHANGED = "source.restriction.changed"


class SourceEvent(ContractModel):
    event_id: UUID
    event_type: SourceEventType
    schema_version: Literal["w2.source.v1"]
    aggregate_id: UUID
    aggregate_revision: PositiveInt
    occurred_at: AwareDatetime
    payload: SourceEventPayload

    @model_validator(mode="after")
    def validate_event_consistency(self) -> SourceEvent:
        if self.aggregate_id != self.payload.source_id:
            raise ValueError("event aggregate_id does not match payload source_id")
        if self.event_type is SourceEventType.VERSION_AVAILABLE:
            matches_type = isinstance(self.payload, SourceEnvelope)
        elif self.event_type is SourceEventType.OBSERVATION_CHANGED:
            matches_type = isinstance(self.payload, SourceObservationSnapshot)
        else:
            matches_type = isinstance(self.payload, SourceRestrictionSnapshot)
        if not matches_type:
            raise ValueError("event_type does not match payload")
        return self


type CollectionPayload = CollectionCommand | CollectionResult | SourceEnvelope | SourceEvent


def parse_collection_payload(value: dict[str, object]) -> CollectionPayload:
    """Parse one top-level contract value without guessing missing discriminators."""

    if "event_type" in value:
        return SourceEvent.model_validate(value)
    if "authenticated_owner_ref" in value:
        return CollectionCommand.model_validate(value)
    if "completion_kind" in value:
        return CollectionResult.model_validate(value)
    return SourceEnvelope.model_validate(value)
