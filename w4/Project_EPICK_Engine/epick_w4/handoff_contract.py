"""Local W3/W4 and backend contract drafts; not an adopted upstream schema."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Identifier = Annotated[str, Field(pattern=r"^[^\s{}]{1,200}$")]
Text = Annotated[str, Field(min_length=1, max_length=6000, pattern=r"\S")]
Day = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Question(Record):
    scope_id: Identifier
    question_id: Literal["expertise", "teamwork", "challenge", "self_description", "job_experience"]
    user_theme: Annotated[str, Field(min_length=1, max_length=200)] | None = None


class ServiceRequest(Record):
    schema_version: Literal["w4-service-input/0.1"]
    request_id: Identifier
    project_id: Identifier
    question: Question
    top_k: Annotated[int, Field(ge=1, le=20)] = 3


class SourceVersion(Record):
    source_version_id: Identifier
    source_id: Identifier
    scope_id: Identifier
    content_sha256: Digest
    published_at: Day | None
    parse_status: Literal["PARSED", "UNPARSED", "PARTIAL"]


class CompanyEvidence(Record):
    evidence_id: Identifier
    source_version_id: Identifier
    exact_quote: Text
    locator: Text


class Attestation(Record):
    statement: Text
    scope_id: Identifier
    source_version_id: Identifier
    evidence_ids: Annotated[list[Identifier], Field(min_length=1, max_length=20)]
    verification_status: Literal["VERIFIED", "PENDING", "REJECTED"]
    usage_status: Literal["USABLE", "RESTRICTED", "WITHDRAWN"]
    published_at: Day | None
    valid_from: Day | None
    valid_to: Day | None


class Claim(Attestation):
    claim_id: Identifier


class Requirement(Attestation):
    requirement_id: Identifier
    requirement_type: Literal["REQUIRED", "PREFERRED"]


class SourceReview(Record):
    review_id: Identifier
    source_version_id: Identifier | None
    required: bool
    status: Literal["USABLE", "LIMITED", "UNAVAILABLE"]
    codes: Annotated[list[Identifier], Field(max_length=30)]
    limitations: Annotated[list[Text], Field(max_length=30)]


class CompanyVersions(Record):
    source_version_ids: Annotated[list[Identifier], Field(max_length=50)]


class KnowledgeBundle(Record):
    schema_version: Literal["w4-knowledge-bundle/0.1"]
    knowledge_bundle_id: Identifier
    contract_version: Literal["w3-w4-projection/0.1-draft"]
    scope_id: Identifier
    data_kind: Literal["SYNTHETIC", "REAL"]
    as_of: Day
    input_version_refs: CompanyVersions
    source_versions: Annotated[list[SourceVersion], Field(max_length=50)]
    evidence: Annotated[list[CompanyEvidence], Field(max_length=100)]
    claims: Annotated[list[Claim], Field(max_length=20)]
    explicit_requirements: Annotated[list[Requirement], Field(max_length=20)]
    source_reviews: Annotated[list[SourceReview], Field(max_length=50)]


class Project(Record):
    project_id: Identifier
    owner_id: Identifier


class Snapshot(Record):
    snapshot_id: Identifier
    episode_versions: dict[Identifier, Annotated[int, Field(ge=1)]]


class Episode(Record):
    episode_id: Identifier
    version: Annotated[int, Field(ge=1)]
    owner_id: Identifier
    activity_id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=500)]
    raw_text: Annotated[str, Field(min_length=1, max_length=12000)]


class UnavailableKnowledge(Record):
    """Server projection of a rejected diagnostic handoff; no manual examples."""
    schema_version: Literal["w4-knowledge-unavailable/0.1"]
    scope_id: Identifier
    reason: Literal["NOT_PROVIDED", "DIAGNOSTIC_SAMPLE_REJECTED"]
    source_reviews: Annotated[list[SourceReview], Field(max_length=50)]


class ServerContext(Record):
    schema_version: Literal["w4-server-context/0.1"]
    context_version: Identifier
    data_kind: Literal["SYNTHETIC", "REAL"]
    project: Project
    snapshot: Snapshot
    episodes: Annotated[list[Episode], Field(max_length=20)]
    excluded_episode_ids: Annotated[list[Identifier], Field(max_length=100)]
    question_scope_id: Identifier
    company_knowledge: KnowledgeBundle | UnavailableKnowledge


class CompanyRefs(Record):
    claim_ids: list[Identifier]
    requirement_ids: list[Identifier]
    source_version_ids: list[Identifier]
    evidence_ids: list[Identifier]


class EpisodeEvidence(Record):
    evidence_id: Identifier
    episode_id: Identifier
    episode_version: Annotated[int, Field(ge=1)]
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(ge=0)]
    exact_quote: Text
    kinds: list[str]
    subject: str
    assertion: str
    issue: str | None


class ContentCheck(Record):
    check_id: Identifier
    description: Text
    status: Literal["SUPPORTED", "NOT_SHOWN", "AMBIGUOUS", "CONTRADICTED"]
    model_status: Literal["SUPPORTED", "NOT_SHOWN", "AMBIGUOUS", "CONTRADICTED"]
    validation_issue: str | None
    evidence: list[EpisodeEvidence]
    follow_up_question: str | None


class CompanySupport(Record):
    kind: Literal["CLAIM", "REQUIREMENT"]
    id: Identifier
    statement: Text
    requirement_type: Literal["REQUIRED", "PREFERRED"] | None
    status: Literal["SUPPORTED", "AMBIGUOUS"]
    validation_issue: str | None
    source_version_id: Identifier
    company_evidence: list[CompanyEvidence]
    episode_evidence: list[EpisodeEvidence]


class Candidate(Record):
    episode_id: Identifier
    episode_version: Annotated[int, Field(ge=1)]
    title: Text
    status: Literal["DIRECT_MATCH", "PARTIAL_MATCH", "NEEDS_CONFIRMATION"]
    content_checks: list[ContentCheck]
    reasons: list[str]
    confirmation_items: list["Confirmation"]
    ranking_evidence_kind_count: int
    rank: int
    tied_on_quality_keys: bool
    company_support: list[CompanySupport]
    used_company_refs: CompanyRefs


class CompanyContextSummary(Record):
    status: Literal["AVAILABLE", "LIMITED", "UNAVAILABLE"]
    knowledge_bundle_id: Identifier | None
    contract_version: str | None
    input_version_refs: CompanyVersions
    accepted_refs: CompanyRefs
    diagnostics: list[str]
    source_reviews: list[SourceReview]


class Confirmation(Record):
    check_id: Identifier
    status: Literal["NOT_SHOWN", "AMBIGUOUS", "CONTRADICTED"]
    question: Text


class FormConstraints(Record):
    max_characters: None
    required: None
    max_hashtags: None


class QuestionScope(Record):
    scope_id: Identifier
    question_id: str
    user_theme: str | None
    catalog_sha256: Digest
    status: Literal["DRAFT_FOR_LOCAL_REVIEW"]
    official_text: None
    is_official_rubric: Literal[False]
    form_constraints: FormConstraints
    enforced_form_constraints: Annotated[list, Field(max_length=0)]


class ExtractedSnapshot(Record):
    snapshot_id: Identifier
    extracted_episode_versions: dict[Identifier, Annotated[int, Field(ge=1)]]


class Diagnostic(Record):
    code: Identifier


class Eligibility(Record):
    status: Literal["NOT_ASSESSED"]


class ServiceOutput(Record):
    schema_version: Literal["w4-detailed-output/0.2"]
    request_id: Identifier
    project_id: Identifier
    data_kind: Literal["SYNTHETIC"]
    processing_status: Literal["NEEDS_INPUT", "NO_CANDIDATES", "COMPLETED_WITH_LIMITATIONS", "COMPLETED"]
    question_scope: QuestionScope
    candidates: list[Candidate]
    snapshot: ExtractedSnapshot
    diagnostics: list[Diagnostic]
    follow_up_questions: list[str]
    ranking_policy: list[str]
    subcheck_counts_used_as_ranking_points: Literal[False]
    inference: dict
    eligibility_assessment: Eligibility
    limitations: list[str]
    company_context: CompanyContextSummary
    server_context_version: Identifier
    ranking_policy_approval: Literal["PENDING_PRODUCT_REVIEW"]
    job_state_mapping: Literal["OWNED_BY_W1_PENDING_AGREEMENT"]
