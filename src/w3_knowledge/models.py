"""W3의 입력·검증·반환 계약.

이 모듈은 전송 DTO만 정의한다. 원문을 임의로 가져오거나 외부 서비스를
호출하지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    PENDING = "PENDING"
    NOT_REQUIRED = "NOT_REQUIRED"


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    PENDING = "PENDING"
    FAILED = "FAILED"


class UsageStatus(StrEnum):
    USABLE = "USABLE"
    RESTRICTED = "RESTRICTED"
    BLOCKED = "BLOCKED"


class ProcessingStatus(StrEnum):
    COMPLETED = "COMPLETED"
    LIMITED = "LIMITED"
    PAUSED = "PAUSED"
    FAILED = "FAILED"


class RequirementPresenceState(StrEnum):
    FOUND = "FOUND"
    NONE_IN_EXAMINED_SCOPE = "NONE_IN_EXAMINED_SCOPE"
    NOT_ASSESSED = "NOT_ASSESSED"


class Necessity(StrEnum):
    REQUIRED = "REQUIRED"
    PREFERRED = "PREFERRED"
    GENERAL = "GENERAL"


class ConditionOperator(StrEnum):
    AND = "AND"
    OR = "OR"
    LEAF = "LEAF"


class Audience(StrEnum):
    W2_REVIEW = "W2_REVIEW"
    W4_KNOWLEDGE = "W4_KNOWLEDGE"


class EvaluationMode(StrEnum):
    SYNTHETIC = "SYNTHETIC"
    PROVIDER_MOCK = "PROVIDER_MOCK"
    SAMPLE_DIAGNOSTIC = "SAMPLE_DIAGNOSTIC"
    LIVE = "LIVE"


class Purpose(StrEnum):
    SYNTHETIC_ACCEPTANCE = "SYNTHETIC_ACCEPTANCE"
    SAMPLE_DIAGNOSTIC = "SAMPLE_DIAGNOSTIC"
    PROVIDER_POC = "PROVIDER_POC"
    PRODUCTION_STRUCTURE = "PRODUCTION_STRUCTURE"


class PolicyDecision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    UNKNOWN = "UNKNOWN"


class NativeLocator(StrictModel):
    """원본을 재현하기 위한 위치. text offset과 혼동하지 않는다."""

    locator_type: Literal["url_fragment", "document_page", "json_pointer", "external_reference"]
    value: str = Field(min_length=1)
    reproducible: bool = False


class SourceRef(StrictModel):
    source_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._:-]{2,127}$")
    source_version_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._:-]{2,127}$")
    source_kind: str = Field(min_length=1)
    parent_source_id: str | None = None
    observed_at: datetime | None = None


class IntegrityAssertion(StrictModel):
    algorithm: Literal["sha256"] = "sha256"
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    scope: Literal["artifact", "excerpt"] = "artifact"
    reported_by: Literal["W3_CURRENT", "UPSTREAM_REPORTED"] = "W3_CURRENT"


class Artifact(StrictModel):
    artifact_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._:-]{2,127}$")
    source_ref: SourceRef
    upstream_evidence_id: str | None = None
    text: str | None = None
    native_locator: NativeLocator | None = None
    expected_integrity: IntegrityAssertion | None = None
    upstream_integrity: tuple[IntegrityAssertion, ...] = ()
    collection_status: str = "UNKNOWN"
    parsing_status: str = "UNKNOWN"
    access_status: str = "UNKNOWN"
    retention_status: str = "UNKNOWN"
    required_for_scope: bool = False


class Evidence(StrictModel):
    evidence_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._:-]{2,127}$")
    source_ref: SourceRef
    artifact_id: str
    excerpt: str | None = None
    start_offset: int | None = Field(default=None, ge=0)
    end_offset: int | None = Field(default=None, ge=0)
    native_locator: NativeLocator | None = None
    expected_integrity: IntegrityAssertion | None = None
    observed_integrity: IntegrityAssertion | None = None

    @model_validator(mode="after")
    def _offset_pair(self) -> Evidence:
        if (self.start_offset is None) != (self.end_offset is None):
            raise ValueError("start_offset와 end_offset은 함께 제공해야 합니다.")
        if (
            self.start_offset is not None
            and self.end_offset is not None
            and self.end_offset < self.start_offset
        ):
            raise ValueError("end_offset은 start_offset보다 앞설 수 없습니다.")
        return self


class ValidationCheck(StrictModel):
    check_id: str
    status: CheckStatus
    code: str
    message: str
    source_ref: SourceRef | None = None
    evidence_id: str | None = None
    reported_by: Literal["W3_CURRENT", "UPSTREAM_REPORTED"] = "W3_CURRENT"


class Limitation(StrictModel):
    code: str
    impact: str
    source_ref: SourceRef | None = None
    artifact_id: str | None = None


class SourceReview(StrictModel):
    source_ref: SourceRef
    checks: tuple[ValidationCheck, ...]
    limitations: tuple[Limitation, ...] = ()
    process_status: ProcessingStatus


class ConditionNode(StrictModel):
    node_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9._:-]{2,127}$")
    operator: ConditionOperator
    text: str
    children: tuple[str, ...] = ()
    evidence_id: str | None = None
    same_experience_required: bool = False
    comparison_basis: str | None = None
    exception_text: str | None = None

    @model_validator(mode="after")
    def _tree_shape(self) -> ConditionNode:
        if self.operator == ConditionOperator.LEAF and self.children:
            raise ValueError("LEAF 조건에는 children을 둘 수 없습니다.")
        if self.operator != ConditionOperator.LEAF and not self.children:
            raise ValueError("AND/OR 조건에는 children이 필요합니다.")
        return self


class ClaimCandidate(StrictModel):
    candidate_id: str
    statement: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    subject: str | None = None
    scope: str | None = None
    period: str | None = None
    unit: str | None = None
    comparison_basis: str | None = None


class RequirementCandidate(StrictModel):
    candidate_id: str
    original_text: str = Field(min_length=1)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    necessity: Necessity
    condition_nodes: tuple[ConditionNode, ...]
    root_node_id: str
    company_scope: str | None = None
    posting_scope: str | None = None
    role_scope: str | None = None
    time_scope: str | None = None
    technical_terms: tuple[str, ...] = ()


class Claim(ClaimCandidate):
    verification_status: VerificationStatus
    usage_status: UsageStatus
    checks: tuple[ValidationCheck, ...]


class Requirement(RequirementCandidate):
    verification_status: VerificationStatus
    usage_status: UsageStatus
    checks: tuple[ValidationCheck, ...]


class SourceInput(StrictModel):
    source_ref: SourceRef
    artifacts: tuple[Artifact, ...] = Field(min_length=1)
    envelope_checks: tuple[ValidationCheck, ...] = ()
    limitations: tuple[Limitation, ...] = ()


class ExecutionContext(StrictModel):
    actor_id: str = Field(min_length=1)
    audience: Audience
    purpose: Purpose
    mode: EvaluationMode
    request_id: str = Field(min_length=1)


class StructureRequest(StrictModel):
    sources: tuple[SourceInput, ...] = Field(min_length=1)
    context: ExecutionContext

    @field_validator("sources")
    @classmethod
    def _unique_source_versions(cls, sources: tuple[SourceInput, ...]) -> tuple[SourceInput, ...]:
        keys = [
            (source.source_ref.source_id, source.source_ref.source_version_id) for source in sources
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("동일 source/version 입력이 중복되었습니다.")
        return sources


class KnowledgeBundle(StrictModel):
    evidences: tuple[Evidence, ...] = ()
    claims: tuple[Claim, ...] = ()
    requirements: tuple[Requirement, ...] = ()
    requirement_presence: RequirementPresenceState
    source_reviews: tuple[SourceReview, ...]
    limitations: tuple[Limitation, ...] = ()
    mode: EvaluationMode
    purpose: Purpose


class StructureResponse(StrictModel):
    bundle: KnowledgeBundle
    status: ProcessingStatus
    errors: tuple[SafeError, ...] = ()


from .errors import SafeError  # noqa: E402  # 순환 타입 해석용
