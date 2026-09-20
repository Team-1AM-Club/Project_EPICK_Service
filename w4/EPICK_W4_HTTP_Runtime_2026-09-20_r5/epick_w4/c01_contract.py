"""W4-owned server envelope for the pinned W3 C01 r2 candidate contract."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator

from .handoff_contract import (
    Candidate,
    CompanyContextSummary,
    CompanyEvidence,
    CompanySupport,
    Day,
    Digest,
    Identifier,
    Record,
    ServerContext,
    ServiceOutput,
    Confirmation,
)

PROFILE = "w3-c01/0.2-candidate/r2"
COMMIT = "05f26b4c0a52aecf155a66df0fcaa38e6fbd4cf5"
UUIDText = Annotated[
    str, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
]


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("C01_TIMEZONE_REQUIRED")
    return parsed.timestamp()


class IndexKey(Record):
    source_version_id: UUIDText
    extraction_revision_id: UUIDText
    representation: Annotated[str, Field(min_length=1, max_length=200)]
    normalization_version: Annotated[str, Field(min_length=1, max_length=200)]


class SourceMetadata(Record):
    """Trusted host assertions, bound to the exact extraction; never inferred."""

    source_id: UUIDText
    index_key: IndexKey
    scope_id: Identifier | None
    content_sha256: Digest | None
    published_at: Day | None
    valid_from: Day | None
    valid_to: Day | None
    parse_status: Literal["PARSED", "PARTIAL", "UNPARSED", "UNKNOWN"]
    required_for_scope: bool
    expires_at: str

    @field_validator("published_at", "valid_from", "valid_to")
    @classmethod
    def valid_day(cls, value):
        if value is not None:
            date.fromisoformat(value)
        return value

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value):
        timestamp(value)
        return value


class SourceBinding(Record):
    signal: dict
    knowledge_generation: Annotated[int, Field(ge=0, le=2**63 - 1)]
    knowledge: dict
    metadata: SourceMetadata


class C01Knowledge(Record):
    schema_version: Literal["w4-c01-knowledge/0.1-proposal"]
    profile: Literal["w3-c01/0.2-candidate/r2"]
    upstream_commit: Literal["05f26b4c0a52aecf155a66df0fcaa38e6fbd4cf5"]
    knowledge_bundle_id: Identifier
    data_kind: Literal["SYNTHETIC", "REAL"]
    scope_id: Identifier
    as_of: Day
    sources: Annotated[list[SourceBinding], Field(min_length=1, max_length=20)]

    @field_validator("as_of")
    @classmethod
    def valid_as_of(cls, value):
        date.fromisoformat(value)
        return value


class C01ServerContext(ServerContext):
    schema_version: Literal["w4-server-context/0.2"]
    company_knowledge: C01Knowledge


class C01Evidence(CompanyEvidence):
    locator: str | None
    provenance: dict


class ConditionAssessment(Record):
    node_id: Identifier
    status: Literal["SUPPORTED", "NOT_SHOWN", "AMBIGUOUS", "CONTRADICTED"]
    evidence_ids: list[Identifier]


class C01Support(CompanySupport):
    requirement_type: Literal["REQUIRED", "PREFERRED", "GENERAL"] | None
    status: Literal["SUPPORTED", "NOT_SHOWN", "AMBIGUOUS", "CONTRADICTED"]
    source_id: UUIDText
    upstream_id: str
    upstream_record: dict
    company_evidence: list[C01Evidence]
    condition_assessment: list[ConditionAssessment]


class C01Candidate(Candidate):
    company_support: list[C01Support]


class C01Summary(CompanyContextSummary):
    dependencies: list[dict]
    upstream_details: list[dict]


class C01ServiceOutput(ServiceOutput):
    schema_version: Literal["w4-detailed-output/0.3"]
    candidates: list[C01Candidate]
    company_context: C01Summary


C01ServiceOutput.model_rebuild(_types_namespace={"Confirmation": Confirmation})
