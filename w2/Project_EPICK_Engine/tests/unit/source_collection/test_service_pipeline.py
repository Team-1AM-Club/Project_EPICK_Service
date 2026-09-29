from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import cast
from uuid import UUID

import pytest

from epick_engine.source_collection import service as service_module
from epick_engine.source_collection.collector import (
    StaticFetchFailureCode,
    StaticFetchRequest,
    StaticFetchResult,
    StaticResponseCandidate,
)
from epick_engine.source_collection.contracts import (
    AccessClass,
    CollectionCommand,
    CollectionResult,
    CollectionStage,
    CompletionKind,
    CoreSourceDecision,
    ExtractionStatus,
    OfficialStatus,
    Permission,
    Policy,
    PostingSectionKind,
    SourceEnvelope,
    SourceEventType,
    SourceType,
)
from epick_engine.source_collection.parsing import (
    PARSER_VERSION,
    StaticParseResult,
    compute_content_hash,
    extract_static_candidate,
    parse_approved_static_posting,
)
from epick_engine.source_collection.persistence import PreparedCollectionCommit
from epick_engine.source_collection.policy import (
    ExecutionLimits,
    Representation,
    UntrustedDocument,
    ValidatedTarget,
)
from epick_engine.source_collection.service import (
    JOB_POSTING_OUTPUT_HASH_PROFILE_VERSION,
    OUTPUT_HASH_PROFILE_VERSION,
    CollectionInputProvider,
    StaticCollectionExecution,
    StaticCollectionInput,
    compute_job_posting_parser_output_hash,
    compute_parser_output_hash,
)
from epick_engine.source_collection.worker import (
    SourceCollectionWorker,
    WorkerExecutionPermit,
)

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
COMPANY_ID = UUID("00000000-0000-0000-0000-000000000001")
SOURCE_ID = UUID("00000000-0000-0000-0000-000000000002")
COMMAND_ID = UUID("00000000-0000-0000-0000-000000000003")
JOB_ID = UUID("00000000-0000-0000-0000-000000000004")
OWNER_ID = UUID("00000000-0000-0000-0000-000000000005")
PURPOSE_ID = UUID("00000000-0000-0000-0000-000000000006")
POLICY_ID = UUID("00000000-0000-0000-0000-000000000007")
ATTEMPT_ID = UUID("00000000-0000-0000-0000-000000000008")
UUIDS = tuple(UUID(f"00000000-0000-0000-0000-{value:012d}") for value in range(101, 121))
RAW_BODY_SENTINEL = "RAW BODY MUST NEVER APPEAR IN A PREPARED COMMIT"
RAW_DOCUMENT = (
    "<html><head><script>"
    + RAW_BODY_SENTINEL
    + "</script></head><body><main><p>Python과 SQL 경험</p>"
    + "<p>문서화 경험</p></main></body></html>"
)
SECTIONED_DOCUMENT = (
    "<html><body><main><h2>자격 요건</h2><p>Python과 SQL 경험</p>"
    "<h2>우대 사항</h2><p>문서화 경험</p></main></body></html>"
)
SEMANTIC_POSTING_DOCUMENT = (
    "<html><body><main><h2>Role</h2><p>Platform Engineer</p>"
    "<h2>Organization</h2><p>Cloud Platform</p>"
    "<h2>Published</h2><p>Published: 2026-09-01</p>"
    "<h2>Deadline</h2><p>Deadline: 2026-09-30</p></main></body></html>"
)
H1_ONLY_JOB_POSTING_DOCUMENT = (
    "<html><body><main><h1>H1 Platform Engineer</h1></main></body></html>"
)
FORGED_SECTION_SENTINEL = "FORGED_SECTION_TEXT_MUST_NOT_LEAK"
OFFICIAL_JSON_DOCUMENT = (
    '{"release":{"title":"신규 플랫폼 채용","location":"서울","published_at":"2026-09-10"}}'
)


class _InputProvider(CollectionInputProvider):
    def __init__(self, value: StaticCollectionInput, events: list[str]) -> None:
        self.value = value
        self.events = events
        self.commands: list[CollectionCommand] = []

    def load(self, command: CollectionCommand) -> StaticCollectionInput:
        self.events.append("policy")
        self.commands.append(command)
        return self.value


class _Collector:
    def __init__(self, result: StaticFetchResult, events: list[str]) -> None:
        self.result = result
        self.events = events
        self.requests: list[StaticFetchRequest] = []

    def fetch(self, request: StaticFetchRequest) -> StaticFetchResult:
        self.events.append("fetch")
        self.requests.append(request)
        return self.result


class _Parser:
    def __init__(self, result: StaticParseResult, events: list[str]) -> None:
        self.result = result
        self.events = events
        self.candidates: list[StaticResponseCandidate] = []

    def __call__(self, candidate: StaticResponseCandidate) -> StaticParseResult:
        self.events.append("parse")
        self.candidates.append(candidate)
        return self.result


class _Context:
    def __init__(self, command: CollectionCommand) -> None:
        self.command = command
        self.entered: list[tuple[CollectionStage, int | None]] = []

    def enter_stage(
        self,
        stage: CollectionStage,
        *,
        policy_revision: int | None = None,
    ) -> None:
        self.entered.append((stage, policy_revision))


class _WorkerControl:
    def __init__(
        self,
        permit: WorkerExecutionPermit,
        *,
        policy_revision_on_policy: int | None = None,
    ) -> None:
        self.permit = permit
        self.policy_revision_on_policy = policy_revision_on_policy
        self.stages: list[CollectionStage] = []
        self.finalized = False

    def authorize_execution(self, command: CollectionCommand) -> WorkerExecutionPermit:
        assert command == self.permit.command
        return self.permit

    def authorize_and_record_stage(
        self,
        permit: WorkerExecutionPermit,
        stage: CollectionStage,
        *,
        policy_revision: int | None = None,
    ) -> WorkerExecutionPermit:
        self.stages.append(stage)
        if (
            policy_revision is None
            and stage is CollectionStage.POLICY
            and self.policy_revision_on_policy is not None
        ):
            policy_revision = self.policy_revision_on_policy
        if policy_revision is None:
            return permit
        self.permit = replace(
            permit,
            command=permit.command.model_copy(update={"policy_revision": policy_revision}),
        )
        return self.permit

    def finalize_execution(
        self,
        permit: WorkerExecutionPermit,
        result: CollectionResult,
        *,
        resources_closed: bool = True,
    ) -> None:
        assert permit.attempt_id == ATTEMPT_ID
        assert resources_closed is True
        assert result.command_id == permit.command.command_id
        self.finalized = True

    def record_execution_stopped(
        self,
        permit: WorkerExecutionPermit,
        *,
        resources_closed: bool,
        error_code: str,
    ) -> None:
        raise AssertionError(f"unexpected worker stop: {permit=} {resources_closed=} {error_code=}")


def _command() -> CollectionCommand:
    return CollectionCommand(
        schema_version="w2.collection.v1",
        command_id=COMMAND_ID,
        job_id=JOB_ID,
        authenticated_owner_ref=OWNER_ID,
        project_ref="project-epick",
        company_id=COMPANY_ID,
        source_id=SOURCE_ID,
        input_version=1,
        execution_fence="fence-1",
        purpose_ref=PURPOSE_ID,
        core_source_decision=CoreSourceDecision(
            is_core=True,
            decided_by="core-source-policy",
            rationale="official career source",
            decision_revision=3,
            analysis_input_version=1,
        ),
        resume_stage=CollectionStage.POLICY,
        policy_revision=None,
        owner_deletion_epoch=0,
    )


def _policy(
    *,
    collection_permission: Permission = Permission.ALLOWED,
    excerpt_storage_permission: Permission = Permission.ALLOWED,
) -> Policy:
    return Policy(
        policy_decision_id=POLICY_ID,
        official_status=OfficialStatus.VERIFIED,
        access_class=AccessClass.PUBLIC,
        collection_permission=collection_permission,
        excerpt_storage_permission=excerpt_storage_permission,
        body_storage_permission=Permission.DENIED,
        redistribution_permission=Permission.DENIED,
        checked_at=NOW,
        policy_version="policy.v1",
    )


def _limits() -> ExecutionLimits:
    return ExecutionLimits(
        site_concurrency=1,
        global_concurrency=4,
        source_ttl_seconds=3600,
        max_response_bytes=1_000_000,
        max_decompressed_bytes=2_000_000,
        connect_timeout_seconds=2.0,
        read_timeout_seconds=5.0,
        max_redirects=3,
        general_retry_limit=1,
        retention_days=30,
    )


def _input(
    *,
    policy: Policy | None = None,
    redirect_robots_permissions: tuple[tuple[str, Permission], ...] = (),
) -> StaticCollectionInput:
    return StaticCollectionInput(
        source_id=SOURCE_ID,
        company_id=COMPANY_ID,
        source_url="https://careers.example.test/openings",
        title="채용 공고",
        source_type=SourceType.JOB_POSTING,
        policy=_policy() if policy is None else policy,
        policy_revision=7,
        robots_permission=Permission.ALLOWED,
        limits=_limits(),
        result_version=2,
        aggregate_revision=4,
        language="ko",
        redirect_robots_permissions=redirect_robots_permissions,
    )


def _candidate() -> StaticResponseCandidate:
    return StaticResponseCandidate(
        final_target=ValidatedTarget(
            url="https://careers.example.test/openings",
            hostname="careers.example.test",
            port=443,
            resolved_addresses=frozenset({"198.51.100.15"}),
        ),
        representation=Representation.HTML,
        document=UntrustedDocument(text=RAW_DOCUMENT),
        http_status=200,
        raw_size=len(RAW_DOCUMENT.encode()),
        decompressed_size=len(RAW_DOCUMENT.encode()),
    )


def _official_json_candidate() -> StaticResponseCandidate:
    return StaticResponseCandidate(
        final_target=ValidatedTarget(
            url="https://api.example.test/v1/releases/2026-09-10",
            hostname="api.example.test",
            port=443,
            resolved_addresses=frozenset({"198.51.100.16"}),
        ),
        representation=Representation.JSON,
        document=UntrustedDocument(text=OFFICIAL_JSON_DOCUMENT),
        http_status=200,
        raw_size=len(OFFICIAL_JSON_DOCUMENT.encode()),
        decompressed_size=len(OFFICIAL_JSON_DOCUMENT.encode()),
    )


def _sectioned_candidate() -> StaticResponseCandidate:
    return replace(
        _candidate(),
        document=UntrustedDocument(text=SECTIONED_DOCUMENT),
        raw_size=len(SECTIONED_DOCUMENT.encode()),
        decompressed_size=len(SECTIONED_DOCUMENT.encode()),
    )


def _semantic_posting_candidate() -> StaticResponseCandidate:
    return replace(
        _candidate(),
        document=UntrustedDocument(text=SEMANTIC_POSTING_DOCUMENT),
        raw_size=len(SEMANTIC_POSTING_DOCUMENT.encode()),
        decompressed_size=len(SEMANTIC_POSTING_DOCUMENT.encode()),
    )


def _h1_only_job_posting_candidate() -> StaticResponseCandidate:
    return replace(
        _candidate(),
        document=UntrustedDocument(text=H1_ONLY_JOB_POSTING_DOCUMENT),
        raw_size=len(H1_ONLY_JOB_POSTING_DOCUMENT.encode()),
        decompressed_size=len(H1_ONLY_JOB_POSTING_DOCUMENT.encode()),
    )


def _section_key(
    evidence_keys: tuple[str, ...],
    text_raw: str,
    order: int,
) -> str:
    payload = "\0".join((*evidence_keys, text_raw, str(order))).encode("utf-8")
    return f"section:{sha256(payload).hexdigest()}"


def _partial_parse_result(catalog: StaticParseResult) -> StaticParseResult:
    retained_evidence = catalog.evidence[:-1]
    retained_keys = {evidence.evidence_key for evidence in retained_evidence}
    retained_by_key = {evidence.evidence_key: evidence for evidence in retained_evidence}
    retained_sections = []
    for section in catalog.sections:
        evidence_keys = tuple(key for key in section.evidence_keys if key in retained_keys)
        if not evidence_keys:
            continue
        text_raw = "\n".join(retained_by_key[key].text_excerpt for key in evidence_keys)
        retained_sections.append(
            replace(
                section,
                section_key=_section_key(evidence_keys, text_raw, section.order),
                text_raw=text_raw,
                evidence_keys=evidence_keys,
            )
        )
    return replace(
        catalog,
        extraction_status=ExtractionStatus.PARTIAL,
        evidence=retained_evidence,
        sections=tuple(retained_sections),
        limitations=catalog.limitations + ("partial_fixture",),
    )


def _cross_section_regrouped_partial_result(
    result: StaticParseResult,
) -> StaticParseResult:
    first_section, second_section = result.sections
    first_evidence, *second_evidence = result.evidence
    first_keys = (first_evidence.evidence_key,)
    second_keys = tuple(evidence.evidence_key for evidence in second_evidence)
    first_text = first_evidence.text_excerpt
    second_text = "\n".join(evidence.text_excerpt for evidence in second_evidence)
    return replace(
        result,
        sections=(
            replace(
                first_section,
                section_key=_section_key(first_keys, first_text, first_section.order),
                text_raw=first_text,
                evidence_keys=first_keys,
            ),
            replace(
                second_section,
                section_key=_section_key(second_keys, second_text, second_section.order),
                text_raw=second_text,
                evidence_keys=second_keys,
            ),
        ),
    )


def _parse_result(
    *,
    extraction_status: ExtractionStatus = ExtractionStatus.COMPLETE,
) -> StaticParseResult:
    catalog = extract_static_candidate(_candidate())
    assert catalog.extraction_status is ExtractionStatus.COMPLETE

    if extraction_status is ExtractionStatus.COMPLETE:
        return catalog
    if extraction_status is ExtractionStatus.FAILED:
        return replace(catalog, extraction_status=ExtractionStatus.FAILED, evidence=(), sections=())

    return _partial_parse_result(catalog)


def _uuid_factory(values: tuple[UUID, ...] = UUIDS) -> Callable[[], UUID]:
    iterator: Iterator[UUID] = iter(values)
    return lambda: next(iterator)


def _execution(
    *,
    input_value: StaticCollectionInput,
    fetch_result: StaticFetchResult,
    parse_result: StaticParseResult,
    events: list[str],
) -> tuple[StaticCollectionExecution, _InputProvider, _Collector, _Parser]:
    provider = _InputProvider(input_value, events)
    collector = _Collector(fetch_result, events)
    parser = _Parser(parse_result, events)
    return (
        StaticCollectionExecution(
            attempt_id=ATTEMPT_ID,
            input_provider=provider,
            collector=collector,
            parser=parser,
            clock=lambda: NOW,
            uuid_factory=_uuid_factory(),
        ),
        provider,
        collector,
        parser,
    )


def _fetch_result(candidate: StaticResponseCandidate | None = None) -> StaticFetchResult:
    return StaticFetchResult(
        command_id=COMMAND_ID,
        candidate=_candidate() if candidate is None else candidate,
        failure_code=None,
    )


def _fetch_failure(
    code: StaticFetchFailureCode,
    *,
    retry_after: str | None = None,
) -> StaticFetchResult:
    return StaticFetchResult(
        command_id=COMMAND_ID,
        candidate=None,
        failure_code=code,
        retry_after=retry_after,
    )


def _assert_korean_failure(result: CollectionResult) -> None:
    assert re.search("[가-힣]", result.message_ko)
    assert result.required_actions
    assert all(re.search("[가-힣]", action.label_ko) for action in result.required_actions)


def test_parser_output_hash_uses_the_frozen_v1_canonical_semantic_payload() -> None:
    result = _parse_result()

    assert OUTPUT_HASH_PROFILE_VERSION == "epick-parser-output-sha256-v1"
    assert (
        compute_parser_output_hash(result)
        == "8bcd510c70f257829fd9cfdbd8234aaf35ea32d61d7635d00bf1fee82672d1f7"
    )
    assert compute_parser_output_hash(result) == compute_parser_output_hash(result)

    metadata_only_change = replace(
        result,
        content_hash="f" * 64,
        hash_profile_version="another-content-profile",
        parser_version="another-parser-version",
        representation=Representation.JSON,
    )
    assert compute_parser_output_hash(metadata_only_change) == compute_parser_output_hash(result)


@pytest.mark.parametrize(
    "change",
    (
        lambda result: replace(
            result,
            evidence=(replace(result.evidence[0], text_excerpt="Python 또는 SQL 경험"),),
        ),
        lambda result: replace(
            result,
            sections=(replace(result.sections[0], text_raw="Python 또는 SQL 경험"),),
        ),
        lambda result: replace(
            result,
            published_at=result.published_at.model_copy(
                update={"value": "2026-09-11", "raw_text": "2026-09-11"}
            ),
        ),
        lambda result: replace(result, limitations=("manual_review_required",)),
    ),
)
def test_parser_output_hash_changes_for_material_semantic_output(
    change: Callable[[StaticParseResult], StaticParseResult],
) -> None:
    result = _parse_result()

    assert compute_parser_output_hash(change(result)) != compute_parser_output_hash(result)


def test_static_execution_runs_policy_fetch_parse_and_prepares_excerpts_only_commit() -> None:
    events: list[str] = []
    execution, provider, collector, parser = _execution(
        input_value=_input(),
        fetch_result=_fetch_result(),
        parse_result=_parse_result(),
        events=events,
    )
    context = _Context(_command())

    prepared = execution.run_once(context)

    assert provider.commands == [context.command]
    assert events == ["policy", "fetch", "parse"]
    assert context.entered == [
        (CollectionStage.POLICY, 7),
        (CollectionStage.FETCH, 7),
        (CollectionStage.PARSE, 7),
    ]
    assert collector.requests[0].command_id == COMMAND_ID
    assert collector.requests[0].policy.revision == 7
    assert parser.candidates == [_candidate()]
    assert prepared.attempt_id == ATTEMPT_ID
    assert prepared.finalized_at == NOW

    assert prepared.result.completion_kind is CompletionKind.COMPLETE
    assert prepared.result.policy_revision == 7
    assert prepared.result.result_version == 2
    assert prepared.result.failures == []
    assert len(prepared.result.successful_source_refs) == 1
    assert prepared.source_version is not None
    assert prepared.source_version.policy_decision_id == POLICY_ID
    assert prepared.source_version.content_hash == compute_content_hash(
        Representation.HTML,
        RAW_DOCUMENT,
    )
    assert prepared.source_version.first_parser_version == PARSER_VERSION
    assert len(prepared.evidence) == len(_parse_result().evidence)
    assert prepared.evidence[0].text_excerpt == "Python과 SQL 경험"
    assert prepared.evidence[0].locator.value == "/html/body/main/p[1]"
    assert prepared.extraction_revision is not None
    assert prepared.extraction_revision.output_hash == compute_job_posting_parser_output_hash(
        _parse_result(),
        parse_approved_static_posting(_parse_result()),
    )
    assert prepared.extraction_revision.evidence_ids == tuple(
        evidence.evidence_id for evidence in prepared.evidence
    )
    assert prepared.parser_execution is not None
    assert prepared.parser_execution.status == "succeeded"
    assert prepared.parser_execution.output_hash == prepared.extraction_revision.output_hash

    assert len(prepared.events) == 1
    event = prepared.events[0]
    assert event.event_type is SourceEventType.VERSION_AVAILABLE
    envelope = event.payload
    assert envelope.retention_scope.value == "excerpts_only"
    assert envelope.normalized_body_ref is None
    assert envelope.evidence_spans[0].text_excerpt == "Python과 SQL 경험"
    assert RAW_BODY_SENTINEL not in str(envelope.model_dump(mode="json"))


def test_static_execution_keeps_official_json_in_static_pipeline_without_browser() -> None:
    events: list[str] = []
    candidate = _official_json_candidate()
    parsed = extract_static_candidate(candidate)
    execution, _, collector, parser = _execution(
        input_value=replace(
            _input(),
            source_type=SourceType.OFFICIAL_API,
            source_url=str(candidate.final_target.url),
            title="공식 API 공고",
        ),
        fetch_result=_fetch_result(candidate),
        parse_result=parsed,
        events=events,
    )

    prepared = execution.run_once(_Context(_command()))

    assert events == ["policy", "fetch", "parse"]
    assert collector.requests and parser.candidates == [candidate]
    assert prepared.source_version is not None
    assert prepared.source_version.representation.value == "official_json"
    assert prepared.observation is not None
    observation = prepared.observation.snapshot
    assert observation.representation is not None
    assert observation.representation.value == "official_json"
    envelope = prepared.events[0].payload
    assert isinstance(envelope, SourceEnvelope)
    assert envelope.metadata.representation.value == "official_json"
    assert envelope.metadata.content_type == "application/json"


def test_static_execution_keeps_successful_partial_parse_with_parse_failure() -> None:
    events: list[str] = []
    partial = replace(
        _parse_result(extraction_status=ExtractionStatus.PARTIAL),
        limitations=(
            "external_css_visibility_not_evaluated",
            "one_locator_rejected",
        ),
    )
    execution, _provider, _collector, _parser = _execution(
        input_value=_input(),
        fetch_result=_fetch_result(),
        parse_result=partial,
        events=events,
    )

    prepared = execution.run_once(_Context(_command()))

    assert prepared.result.completion_kind is CompletionKind.PARTIAL
    assert len(prepared.result.successful_source_refs) == 1
    assert len(prepared.result.failures) == 1
    assert prepared.result.failures[0].stage is CollectionStage.PARSE
    assert prepared.source_version is not None
    assert prepared.evidence
    assert prepared.extraction_revision is not None
    assert prepared.parser_execution is not None
    assert prepared.parser_execution.status == "succeeded"
    assert prepared.events[0].event_type is SourceEventType.VERSION_AVAILABLE


def test_static_execution_maps_job_posting_sections_to_prepared_revision_and_envelope() -> None:
    events: list[str] = []
    candidate = _sectioned_candidate()
    parsed = extract_static_candidate(candidate)
    expected_drafts = parse_approved_static_posting(parsed).sections
    execution, _provider, _collector, _parser = _execution(
        input_value=_input(),
        fetch_result=_fetch_result(candidate),
        parse_result=parsed,
        events=events,
    )

    prepared = execution.run_once(_Context(_command()))

    assert prepared.extraction_revision is not None
    evidence_ids_by_key = {
        evidence.evidence_key: evidence.evidence_id for evidence in prepared.evidence
    }
    expected = tuple(
        (
            section.section_key,
            section.kind,
            section.heading_raw,
            section.text_raw,
            tuple(evidence_ids_by_key[key] for key in section.evidence_keys),
            section.order,
            section.relation_text,
        )
        for section in expected_drafts
    )
    revision_sections = tuple(
        (
            section.section_key,
            section.kind,
            section.heading_raw,
            section.text_raw,
            tuple(section.evidence_ids),
            section.order,
            section.relation_text,
        )
        for section in prepared.extraction_revision.posting_sections
    )
    envelope_sections = tuple(
        (
            section.section_key,
            section.kind,
            section.heading_raw,
            section.text_raw,
            tuple(section.evidence_ids),
            section.order,
            section.relation_text,
        )
        for section in prepared.events[0].payload.posting_sections
    )

    assert expected
    assert revision_sections == expected
    assert envelope_sections == expected


def test_static_execution_persists_one_semantic_posting_parse_dates_and_v2_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    candidate = _semantic_posting_candidate()
    parsed = extract_static_candidate(candidate)
    expected_semantic = parse_approved_static_posting(parsed)
    parse_calls: list[StaticParseResult] = []
    original_parse_approved_static_posting = service_module.parse_approved_static_posting

    def record_semantic_parse(value: StaticParseResult):
        parse_calls.append(value)
        return original_parse_approved_static_posting(value)

    monkeypatch.setattr(
        service_module,
        "parse_approved_static_posting",
        record_semantic_parse,
    )
    execution, _provider, _collector, _parser = _execution(
        input_value=_input(),
        fetch_result=_fetch_result(candidate),
        parse_result=parsed,
        events=events,
    )

    prepared = execution.run_once(_Context(_command()))

    assert parse_calls == [parsed]
    assert prepared.source_version is not None
    assert prepared.extraction_revision is not None
    assert JOB_POSTING_OUTPUT_HASH_PROFILE_VERSION.endswith("v2")
    assert prepared.source_version.published_at == expected_semantic.published_at
    assert prepared.extraction_revision.date_values == {
        "published_at": expected_semantic.published_at,
        "valid_from": prepared.source_version.valid_from,
        "valid_to": prepared.source_version.valid_to,
        "deadline": expected_semantic.deadline,
    }
    assert prepared.events[0].payload.published_at == expected_semantic.published_at
    assert prepared.extraction_revision.output_hash == compute_job_posting_parser_output_hash(
        parsed,
        expected_semantic,
    )
    assert prepared.extraction_revision.output_hash != compute_parser_output_hash(parsed)
    assert expected_semantic.job_title.value == "Platform Engineer"
    assert expected_semantic.organization.value == "Cloud Platform"
    assert {section.kind for section in prepared.extraction_revision.posting_sections} >= {
        PostingSectionKind.ROLE,
        PostingSectionKind.ORGANIZATION,
    }
    changed_semantic = replace(
        expected_semantic,
        sections=(
            replace(expected_semantic.sections[0], text_raw="Changed platform role"),
            *expected_semantic.sections[1:],
        ),
    )
    assert compute_job_posting_parser_output_hash(
        parsed,
        changed_semantic,
    ) != compute_job_posting_parser_output_hash(parsed, expected_semantic)
    changed_deadline = replace(
        expected_semantic,
        deadline=expected_semantic.deadline.model_copy(
            update={"value": "2026-10-01", "raw_text": "Deadline: 2026-10-01"}
        ),
    )
    assert compute_job_posting_parser_output_hash(
        parsed,
        changed_deadline,
    ) != compute_job_posting_parser_output_hash(parsed, expected_semantic)


def test_static_execution_uses_approved_parser_for_authorized_h1_only_job_posting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    candidate = _h1_only_job_posting_candidate()
    parsed = extract_static_candidate(candidate)
    parse_calls: list[StaticParseResult] = []
    original_parse_approved_static_posting = service_module.parse_approved_static_posting

    def record_approved_parse(value: StaticParseResult):
        result = original_parse_approved_static_posting(value)
        parse_calls.append(value)
        assert result.job_title.value == "H1 Platform Engineer"
        return result

    monkeypatch.setattr(
        service_module,
        "parse_approved_static_posting",
        record_approved_parse,
    )
    execution, _provider, _collector, _parser = _execution(
        input_value=_input(),
        fetch_result=_fetch_result(candidate),
        parse_result=parsed,
        events=events,
    )

    execution.run_once(_Context(_command()))

    assert parse_calls == [parsed]


def test_static_execution_keeps_partial_job_posting_sections_and_limitations() -> None:
    events: list[str] = []
    candidate = _sectioned_candidate()
    partial = _partial_parse_result(extract_static_candidate(candidate))
    expected_drafts = parse_approved_static_posting(partial).sections
    execution, _provider, _collector, _parser = _execution(
        input_value=_input(),
        fetch_result=_fetch_result(candidate),
        parse_result=partial,
        events=events,
    )

    prepared = execution.run_once(_Context(_command()))

    assert prepared.result.completion_kind is CompletionKind.PARTIAL
    assert prepared.extraction_revision is not None
    assert prepared.extraction_revision.extraction_status is ExtractionStatus.PARTIAL
    assert prepared.extraction_revision.limitations == partial.limitations
    assert len(prepared.extraction_revision.posting_sections) == len(expected_drafts)
    assert prepared.events[0].payload.extraction_status is ExtractionStatus.PARTIAL
    assert prepared.events[0].payload.limitations == list(partial.limitations)
    assert len(prepared.events[0].payload.posting_sections) == len(expected_drafts)


def test_static_execution_leaves_non_job_posting_sections_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    candidate = _sectioned_candidate()
    parsed = extract_static_candidate(candidate)
    execution, _provider, _collector, _parser = _execution(
        input_value=replace(_input(), source_type=SourceType.COMPANY_WEBSITE),
        fetch_result=_fetch_result(candidate),
        parse_result=parsed,
        events=events,
    )

    def unexpected_semantic_parse(_parsed: StaticParseResult) -> None:
        raise AssertionError("non-job source must not use posting semantic parsing")

    monkeypatch.setattr(
        service_module,
        "parse_approved_static_posting",
        unexpected_semantic_parse,
    )

    prepared = execution.run_once(_Context(_command()))

    assert prepared.extraction_revision is not None
    assert prepared.extraction_revision.posting_sections == ()
    assert prepared.events[0].payload.posting_sections == []
    assert prepared.source_version is not None
    assert prepared.source_version.published_at == parsed.published_at
    assert "deadline" not in prepared.extraction_revision.date_values


def test_static_execution_fetch_failure_skips_parser_and_prepares_observation() -> None:
    events: list[str] = []
    execution, _provider, collector, parser = _execution(
        input_value=_input(),
        fetch_result=_fetch_failure(StaticFetchFailureCode.FETCH_TIMEOUT),
        parse_result=_parse_result(),
        events=events,
    )
    context = _Context(_command())

    prepared = execution.run_once(context)

    assert events == ["policy", "fetch"]
    assert collector.requests
    assert parser.candidates == []
    assert context.entered == [
        (CollectionStage.POLICY, 7),
        (CollectionStage.FETCH, 7),
    ]
    assert prepared.result.completion_kind is CompletionKind.NONE
    assert prepared.result.successful_source_refs == []
    assert prepared.result.failures[0].stage is CollectionStage.FETCH
    _assert_korean_failure(prepared.result)
    assert prepared.source_version is None
    assert prepared.evidence == ()
    assert prepared.extraction_revision is None
    assert prepared.parser_execution is None
    assert prepared.observation is not None
    assert prepared.observation.snapshot.error_code == StaticFetchFailureCode.FETCH_TIMEOUT
    assert len(prepared.events) == 1
    assert prepared.events[0].event_type is SourceEventType.OBSERVATION_CHANGED


@pytest.mark.parametrize(
    "policy",
    (
        _policy(collection_permission=Permission.DENIED),
        _policy(excerpt_storage_permission=Permission.DENIED),
    ),
)
def test_static_execution_denied_policy_never_fetches_or_stores_a_success(
    policy: Policy,
) -> None:
    events: list[str] = []
    execution, _provider, collector, parser = _execution(
        input_value=_input(policy=policy),
        fetch_result=_fetch_result(),
        parse_result=_parse_result(),
        events=events,
    )
    context = _Context(_command())

    prepared = execution.run_once(context)

    assert events == ["policy"]
    assert context.entered == [(CollectionStage.POLICY, 7)]
    assert collector.requests == []
    assert parser.candidates == []
    assert prepared.result.completion_kind is CompletionKind.NONE
    assert prepared.result.successful_source_refs == []
    _assert_korean_failure(prepared.result)
    assert prepared.source_version is None
    assert prepared.evidence == ()
    assert prepared.extraction_revision is None
    assert prepared.parser_execution is None
    assert prepared.observation is not None
    assert prepared.events[0].event_type is SourceEventType.OBSERVATION_CHANGED


def test_static_execution_rejects_failed_parse_as_an_empty_success() -> None:
    events: list[str] = []
    execution, _provider, _collector, _parser = _execution(
        input_value=_input(),
        fetch_result=_fetch_result(),
        parse_result=_parse_result(extraction_status=ExtractionStatus.FAILED),
        events=events,
    )

    prepared = execution.run_once(_Context(_command()))

    assert prepared.result.completion_kind is CompletionKind.NONE
    assert prepared.result.successful_source_refs == []
    assert prepared.result.failures[0].stage is CollectionStage.PARSE
    _assert_korean_failure(prepared.result)
    assert prepared.source_version is None
    assert prepared.evidence == ()
    assert prepared.extraction_revision is None
    assert prepared.parser_execution is not None
    assert prepared.parser_execution.status == "failed"
    assert prepared.parser_execution.output_hash is None
    assert prepared.events[0].event_type is SourceEventType.OBSERVATION_CHANGED


@pytest.mark.parametrize(
    ("resume_stage", "policy_revision", "expected_stages"),
    (
        (
            CollectionStage.POLICY,
            None,
            [
                CollectionStage.POLICY,
                CollectionStage.FETCH,
                CollectionStage.PARSE,
                CollectionStage.PERSIST,
                CollectionStage.DELIVER,
            ],
        ),
        (
            CollectionStage.FETCH,
            7,
            [
                CollectionStage.FETCH,
                CollectionStage.PARSE,
                CollectionStage.PERSIST,
                CollectionStage.DELIVER,
            ],
        ),
    ),
)
def test_static_execution_does_not_repeat_worker_entered_resume_stage(
    resume_stage: CollectionStage,
    policy_revision: int | None,
    expected_stages: list[CollectionStage],
) -> None:
    command = _command().model_copy(
        update={
            "resume_stage": resume_stage,
            "policy_revision": policy_revision,
        }
    )
    events: list[str] = []
    execution, _provider, _collector, _parser = _execution(
        input_value=_input(),
        fetch_result=_fetch_result(),
        parse_result=_parse_result(),
        events=events,
    )
    permit = WorkerExecutionPermit(
        attempt_id=ATTEMPT_ID,
        command=command,
        checkpoint_ref=None,
        all_core_decisions_received=True,
        slot_acquired=True,
        retry_not_before=None,
    )
    control = _WorkerControl(permit)

    def execution_factory(_permit: WorkerExecutionPermit) -> StaticCollectionExecution:
        return execution

    def committer(
        _session_factory: object,
        *,
        command: CollectionCommand,
        prepared: object,
        lock_authority: object,
    ) -> CollectionResult:
        del _session_factory, lock_authority
        assert command == control.permit.command
        return prepared.result

    worker = SourceCollectionWorker(
        control=control,
        execution_factory=execution_factory,
        session_factory=lambda: None,
        lock_authority=lambda *_args, **_kwargs: None,
        committer=committer,
        replayer=lambda *_args, **_kwargs: None,
        clock=lambda: NOW,
    )

    result = worker.handle(command.model_dump(mode="json"))

    assert result.completion_kind is CompletionKind.COMPLETE
    assert control.stages == expected_stages
    assert events == ["policy", "fetch", "parse"]
    assert control.finalized is True


def test_static_execution_keeps_registered_and_final_redirect_urls_separate() -> None:
    redirected = replace(
        _candidate(),
        final_target=ValidatedTarget(
            url="https://jobs.example.test/redirected-opening",
            hostname="jobs.example.test",
            port=443,
            resolved_addresses=frozenset({"198.51.100.28"}),
        ),
    )
    success_events: list[str] = []
    execution, _provider, collector, _parser = _execution(
        input_value=_input(
            redirect_robots_permissions=((redirected.final_target.url, Permission.ALLOWED),),
        ),
        fetch_result=_fetch_result(redirected),
        parse_result=_parse_result(),
        events=success_events,
    )

    successful = execution.run_once(_Context(_command()))

    assert successful.source_version is not None
    assert successful.source_version.canonical_url == _input().source_url
    assert len(successful.events) == 1
    assert str(successful.events[0].payload.url_or_path) == redirected.final_target.url
    assert collector.requests[0].redirect_robots_permissions == (
        (redirected.final_target.url, Permission.ALLOWED),
    )

    failed_events: list[str] = []
    failure_execution, _provider, _collector, _parser = _execution(
        input_value=_input(),
        fetch_result=_fetch_result(redirected),
        parse_result=_parse_result(extraction_status=ExtractionStatus.FAILED),
        events=failed_events,
    )

    failed = failure_execution.run_once(_Context(_command()))

    assert failed.observation is not None
    assert str(failed.observation.snapshot.checked_url) == redirected.final_target.url


@pytest.mark.parametrize("content_hash", (None, "not-a-sha256", "z" * 64))
def test_static_execution_does_not_create_parser_execution_for_invalid_content_hash(
    content_hash: str | None,
) -> None:
    events: list[str] = []
    execution, _provider, _collector, _parser = _execution(
        input_value=_input(),
        fetch_result=_fetch_result(),
        parse_result=replace(_parse_result(), content_hash=content_hash),
        events=events,
    )

    prepared = execution.run_once(_Context(_command()))

    assert prepared.result.completion_kind is CompletionKind.NONE
    assert prepared.result.failures[0].stage is CollectionStage.PARSE
    assert prepared.parser_execution is None


def test_static_execution_rejects_forged_content_hash_that_does_not_bind_body() -> None:
    events: list[str] = []
    execution, _provider, _collector, _parser = _execution(
        input_value=_input(),
        fetch_result=_fetch_result(),
        parse_result=replace(_parse_result(), content_hash="f" * 64),
        events=events,
    )

    prepared = execution.run_once(_Context(_command()))

    assert prepared.result.completion_kind is CompletionKind.NONE
    assert prepared.result.failures[0].stage is CollectionStage.PARSE
    assert prepared.source_version is None
    assert prepared.evidence == ()
    assert prepared.extraction_revision is None
    assert len(prepared.events) == 1
    assert RAW_BODY_SENTINEL not in str(prepared.events[0].model_dump(mode="json"))


@pytest.mark.parametrize(
    "malicious_parse",
    (
        lambda result: replace(
            result,
            evidence=(replace(result.evidence[0], text_excerpt=RAW_BODY_SENTINEL),),
        ),
        lambda result: replace(
            result,
            sections=(
                replace(
                    result.sections[0],
                    text_raw="evidence join does not match this section",
                ),
            ),
        ),
    ),
)
def test_static_execution_fails_closed_for_unverified_evidence_or_section_join(
    malicious_parse: Callable[[StaticParseResult], StaticParseResult],
) -> None:
    events: list[str] = []
    execution, _provider, _collector, _parser = _execution(
        input_value=_input(),
        fetch_result=_fetch_result(),
        parse_result=malicious_parse(_parse_result()),
        events=events,
    )

    prepared = execution.run_once(_Context(_command()))

    assert prepared.result.completion_kind is CompletionKind.NONE
    assert prepared.result.failures[0].stage is CollectionStage.PARSE
    assert prepared.source_version is None
    assert prepared.evidence == ()
    assert prepared.extraction_revision is None
    assert len(prepared.events) == 1
    assert RAW_BODY_SENTINEL not in str(prepared.events[0].model_dump(mode="json"))


@pytest.mark.parametrize(
    "malicious_parse",
    (
        _cross_section_regrouped_partial_result,
        lambda result: replace(
            result,
            sections=(
                replace(
                    result.sections[0],
                    heading_raw=FORGED_SECTION_SENTINEL,
                ),
                *result.sections[1:],
            ),
        ),
        lambda result: replace(
            result,
            sections=(
                replace(
                    result.sections[0],
                    order=99,
                    section_key=_section_key(
                        result.sections[0].evidence_keys,
                        result.sections[0].text_raw,
                        99,
                    ),
                ),
                *result.sections[1:],
            ),
        ),
        lambda result: replace(
            result,
            sections=(
                replace(
                    result.sections[0],
                    section_key=f"section:{FORGED_SECTION_SENTINEL}",
                ),
                *result.sections[1:],
            ),
        ),
    ),
    ids=("cross_section_regroup", "heading", "order", "section_key"),
)
def test_static_execution_fails_closed_for_partial_section_integrity_forgery(
    malicious_parse: Callable[[StaticParseResult], StaticParseResult],
) -> None:
    catalog = extract_static_candidate(_sectioned_candidate())
    assert catalog.extraction_status is ExtractionStatus.COMPLETE
    assert [section.heading_raw for section in catalog.sections] == ["자격 요건", "우대 사항"]
    partial = _partial_parse_result(catalog)

    events: list[str] = []
    execution, _provider, _collector, _parser = _execution(
        input_value=_input(),
        fetch_result=_fetch_result(),
        parse_result=malicious_parse(partial),
        events=events,
    )

    prepared = execution.run_once(_Context(_command()))

    assert prepared.result.completion_kind is CompletionKind.NONE
    assert prepared.result.successful_source_refs == []
    assert [failure.stage for failure in prepared.result.failures] == [CollectionStage.PARSE]
    assert prepared.source_version is None
    assert prepared.evidence == ()
    assert prepared.extraction_revision is None
    assert len(prepared.events) == 1
    assert FORGED_SECTION_SENTINEL not in str(prepared.events[0].model_dump(mode="json"))


@pytest.mark.parametrize(
    ("retry_after", "expected_retry_not_before"),
    (
        ("120", NOW + timedelta(seconds=120)),
        ("Thu, 10 Sep 2026 12:05:00 GMT", datetime(2026, 9, 10, 12, 5, tzinfo=UTC)),
        ("not-a-valid-retry-after", None),
    ),
)
def test_static_execution_rate_limit_binds_retry_after_to_result_and_action(
    retry_after: str,
    expected_retry_not_before: datetime | None,
) -> None:
    events: list[str] = []
    execution, _provider, _collector, _parser = _execution(
        input_value=_input(),
        fetch_result=_fetch_failure(
            StaticFetchFailureCode.RATE_LIMITED,
            retry_after=retry_after,
        ),
        parse_result=_parse_result(),
        events=events,
    )

    prepared = execution.run_once(_Context(_command()))

    retry_action = next(
        action for action in prepared.result.required_actions if action.code == "user_retry"
    )
    assert prepared.result.completion_kind is CompletionKind.NONE
    assert prepared.result.resume_stage is CollectionStage.FETCH
    assert prepared.result.retry_not_before == expected_retry_not_before
    assert retry_action.context.resume_stage is CollectionStage.FETCH
    assert retry_action.context.retry_not_before == expected_retry_not_before


def test_worker_policy_denied_commits_observation_at_actual_policy_revision() -> None:
    command = _command()
    events: list[str] = []
    execution, _provider, collector, parser = _execution(
        input_value=_input(policy=_policy(collection_permission=Permission.DENIED)),
        fetch_result=_fetch_result(),
        parse_result=_parse_result(),
        events=events,
    )
    permit = WorkerExecutionPermit(
        attempt_id=ATTEMPT_ID,
        command=command,
        checkpoint_ref=None,
        all_core_decisions_received=True,
        slot_acquired=True,
        retry_not_before=None,
    )
    control = _WorkerControl(permit, policy_revision_on_policy=7)
    committed: list[PreparedCollectionCommit] = []

    def execution_factory(_permit: WorkerExecutionPermit) -> StaticCollectionExecution:
        return execution

    def committer(
        _session_factory: object,
        *,
        command: CollectionCommand,
        prepared: PreparedCollectionCommit,
        lock_authority: object,
    ) -> CollectionResult:
        del _session_factory, lock_authority
        assert command == control.permit.command
        committed.append(prepared)
        return prepared.result

    worker = SourceCollectionWorker(
        control=control,
        execution_factory=execution_factory,
        session_factory=lambda: None,
        lock_authority=lambda *_args, **_kwargs: None,
        committer=committer,
        replayer=lambda *_args, **_kwargs: None,
        clock=lambda: NOW,
    )

    result = worker.handle(command.model_dump(mode="json"))

    assert events == ["policy"]
    assert collector.requests == []
    assert parser.candidates == []
    assert result.completion_kind is CompletionKind.NONE
    assert result.policy_revision == 7
    assert control.stages == [
        CollectionStage.POLICY,
        CollectionStage.PERSIST,
        CollectionStage.DELIVER,
    ]
    assert len(committed) == 1
    assert committed[0].observation is not None
    assert committed[0].events[0].event_type is SourceEventType.OBSERVATION_CHANGED
    assert control.finalized is True


@pytest.mark.parametrize(
    "limitations",
    (
        tuple(f"internal-limit-{index}" for index in range(129)),
        ("internal-limit-that-must-not-be-public",),
    ),
)
def test_static_execution_fails_closed_before_public_event_for_excessive_limitations(
    limitations: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if len(limitations) == 1:
        assert service_module._MAX_PREPARED_OUTPUT_BYTES == 8 * 1024 * 1024
        monkeypatch.setattr(service_module, "_MAX_PREPARED_OUTPUT_BYTES", 64)
        limitations = (limitations[0] * 2,)

    events: list[str] = []
    execution, _provider, _collector, _parser = _execution(
        input_value=_input(),
        fetch_result=_fetch_result(),
        parse_result=replace(_parse_result(), limitations=limitations),
        events=events,
    )

    prepared = execution.run_once(_Context(_command()))

    assert prepared.result.completion_kind is CompletionKind.NONE
    assert prepared.source_version is None
    assert prepared.evidence == ()
    assert prepared.extraction_revision is None
    assert len(prepared.events) == 1
    assert "internal-limit" not in str(prepared.events[0].model_dump(mode="json"))


def test_static_execution_ignores_non_string_rate_limit_retry_after() -> None:
    events: list[str] = []
    execution, _provider, _collector, _parser = _execution(
        input_value=_input(),
        fetch_result=_fetch_failure(
            StaticFetchFailureCode.RATE_LIMITED,
            retry_after=cast(str | None, 120),
        ),
        parse_result=_parse_result(),
        events=events,
    )

    prepared = execution.run_once(_Context(_command()))

    retry_action = next(
        action for action in prepared.result.required_actions if action.code == "user_retry"
    )
    assert prepared.result.completion_kind is CompletionKind.NONE
    assert prepared.result.retry_not_before is None
    assert retry_action.context.retry_not_before is None
