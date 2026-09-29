"""Contract coverage for the W2 posting handoff at the W1 worker boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid5

from epick_engine.source_collection.contracts import (
    AccessClass,
    AccuracyStatus,
    AcquisitionStatus,
    CollectionCommand,
    CollectionResult,
    CollectionStage,
    CompletionKind,
    CoreSourceDecision,
    DateStatus,
    DateValue,
    Evidence,
    ExtractionStatus,
    FreshnessStatus,
    Locator,
    LocatorKind,
    OfficialStatus,
    Permission,
    Policy,
    PostingSection,
    PostingSectionKind,
    Representation,
    RetentionScope,
    SourceEnvelope,
    SourceEvent,
    SourceEventType,
    SourceMetadata,
    SourceReference,
    SourceType,
)
from epick_engine.source_collection.persistence import PreparedCollectionCommit
from epick_engine.source_collection.worker import (
    SourceCollectionWorker,
    WorkerExecutionContext,
    WorkerExecutionPermit,
)

NOW = datetime(2026, 9, 11, 9, 0, tzinfo=UTC)


def _uuid(name: str) -> UUID:
    return uuid5(UUID("4af90e43-4da5-45a0-82f8-e3412e2740d2"), name)


def _command() -> CollectionCommand:
    return CollectionCommand(
        schema_version="w2.collection.v1",
        command_id=_uuid("command"),
        job_id=_uuid("job"),
        authenticated_owner_ref=_uuid("owner"),
        project_ref="private-project",
        company_id=_uuid("company"),
        source_id=_uuid("source"),
        input_version=1,
        execution_fence="private-execution-fence",
        purpose_ref=_uuid("purpose"),
        core_source_decision=CoreSourceDecision(
            is_core=False,
            decided_by="source-policy",
            rationale="공식 채용 공고입니다.",
            decision_revision=1,
            analysis_input_version=1,
        ),
        resume_stage=CollectionStage.FETCH,
        policy_revision=7,
        owner_deletion_epoch=0,
    )


def _unknown_date() -> DateValue:
    return DateValue(
        status=DateStatus.UNKNOWN,
        raw_text=None,
        value=None,
        precision=None,
        timezone=None,
    )


def _posting_envelope(command: CollectionCommand) -> SourceEnvelope:
    source_version_id = _uuid("source-version")
    required_evidence_id = _uuid("required-evidence")
    preferred_evidence_id = _uuid("preferred-evidence")
    return SourceEnvelope(
        schema_version="w2.source.v1",
        source_id=command.source_id,
        source_version_id=source_version_id,
        extraction_revision_id=_uuid("extraction-revision"),
        company_id=command.company_id,
        source_type=SourceType.JOB_POSTING,
        url_or_path="https://careers.example.com/jobs/platform-engineer",
        title="Platform Engineer",
        policy=Policy(
            policy_decision_id=_uuid("policy"),
            official_status=OfficialStatus.VERIFIED,
            access_class=AccessClass.PUBLIC,
            collection_permission=Permission.ALLOWED,
            excerpt_storage_permission=Permission.ALLOWED,
            body_storage_permission=Permission.DENIED,
            redistribution_permission=Permission.DENIED,
            checked_at=NOW,
            policy_version="policy.v1",
        ),
        acquisition_status=AcquisitionStatus.PARTIALLY_EXTRACTED,
        extraction_status=ExtractionStatus.PARTIAL,
        accuracy_status=AccuracyStatus.UNVERIFIED,
        freshness_status=FreshnessStatus.CURRENT,
        published_at=_unknown_date(),
        collected_at=NOW,
        checked_at=NOW,
        valid_from=_unknown_date(),
        valid_to=_unknown_date(),
        content_hash="a" * 64,
        hash_profile_version="job-posting.v2",
        parser_version="posting-parser.v1",
        language="ko",
        evidence_spans=[
            Evidence(
                evidence_id=required_evidence_id,
                source_version_id=source_version_id,
                section_title="자격 요건",
                text_excerpt="Python 또는 Java 서비스 개발 경험",
                locator=Locator(
                    kind=LocatorKind.XPATH,
                    value="//section[@id='requirements']/p[1]",
                    normalization_version=None,
                    start=None,
                    end=None,
                ),
                chunk_order=0,
            ),
            Evidence(
                evidence_id=preferred_evidence_id,
                source_version_id=source_version_id,
                section_title="우대 사항",
                text_excerpt="분산 시스템 운영 경험",
                locator=Locator(
                    kind=LocatorKind.CSS,
                    value="#preferred li:nth-child(1)",
                    normalization_version=None,
                    start=None,
                    end=None,
                ),
                chunk_order=1,
            ),
        ],
        posting_sections=[
            PostingSection(
                section_key="required-skills",
                kind=PostingSectionKind.REQUIRED,
                heading_raw="자격 요건",
                text_raw="Python 또는 Java 서비스 개발 경험",
                evidence_ids=[required_evidence_id],
                order=0,
                relation_text=None,
            ),
            PostingSection(
                section_key="preferred-skills",
                kind=PostingSectionKind.PREFERRED,
                heading_raw="우대 사항",
                text_raw="분산 시스템 운영 경험",
                evidence_ids=[preferred_evidence_id],
                order=1,
                relation_text=None,
            ),
        ],
        retention_scope=RetentionScope.EXCERPTS_ONLY,
        normalized_body_ref=None,
        limitations=["공고의 마감일은 원문에서 확인되지 않았습니다."],
        metadata=SourceMetadata(
            representation=Representation.STATIC_HTML,
            content_type="text/html",
            normalization_version="html.v1",
        ),
    )


def _result(command: CollectionCommand, envelope: SourceEnvelope) -> CollectionResult:
    return CollectionResult(
        schema_version="w2.collection.v1",
        command_id=command.command_id,
        job_id=command.job_id,
        input_version=command.input_version,
        result_version=1,
        successful_source_refs=[
            SourceReference(
                source_id=envelope.source_id,
                source_version_id=envelope.source_version_id,
                extraction_revision_id=envelope.extraction_revision_id,
            )
        ],
        failures=[],
        completion_kind=CompletionKind.COMPLETE,
        resume_stage=None,
        checkpoint_ref=None,
        retry_not_before=None,
        message_ko="공고 수집을 완료했습니다.",
        required_actions=[],
        source_id=command.source_id,
        policy_revision=command.policy_revision,
    )


def _prepared_posting_commit(
    command: CollectionCommand,
    permit: WorkerExecutionPermit,
) -> PreparedCollectionCommit:
    envelope = _posting_envelope(command)
    event = SourceEvent(
        event_id=_uuid("source-event"),
        event_type=SourceEventType.VERSION_AVAILABLE,
        schema_version="w2.source.v1",
        aggregate_id=envelope.source_id,
        aggregate_revision=1,
        occurred_at=NOW,
        payload=envelope,
    )
    return PreparedCollectionCommit(
        attempt_id=permit.attempt_id,
        result=_result(command, envelope),
        finalized_at=NOW,
        events=(event,),
    )


class _W1Control:
    def __init__(self, permit: WorkerExecutionPermit) -> None:
        self.permit = permit
        self.stages: list[CollectionStage] = []
        self.finalized: list[tuple[WorkerExecutionPermit, CollectionResult]] = []

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
        assert policy_revision == permit.command.policy_revision
        self.stages.append(stage)
        return permit

    def finalize_execution(
        self,
        permit: WorkerExecutionPermit,
        result: CollectionResult,
        *,
        resources_closed: bool = True,
    ) -> None:
        assert resources_closed is True
        self.finalized.append((permit, result))

    def record_execution_stopped(
        self,
        permit: WorkerExecutionPermit,
        *,
        resources_closed: bool,
        error_code: str,
    ) -> None:
        raise AssertionError(f"unexpected worker stop: {permit=} {resources_closed=} {error_code=}")


class _PreparedExecution:
    def __init__(self, prepared: PreparedCollectionCommit) -> None:
        self.prepared = prepared
        self.closed = False

    def run_once(self, context: WorkerExecutionContext) -> PreparedCollectionCommit:
        context.enter_stage(
            CollectionStage.PARSE,
            policy_revision=context.command.policy_revision,
        )
        return self.prepared

    def close(self) -> None:
        self.closed = True


class _CapturingCommitter:
    def __init__(self) -> None:
        self.prepared: list[PreparedCollectionCommit] = []

    def __call__(
        self,
        _session_factory: object,
        *,
        command: CollectionCommand,
        prepared: PreparedCollectionCommit,
        lock_authority: object,
    ) -> CollectionResult:
        assert lock_authority is not None
        assert command.command_id == prepared.result.command_id
        self.prepared.append(prepared)
        return prepared.result


def _run_posting_handoff() -> tuple[
    _W1Control, _PreparedExecution, _CapturingCommitter, CollectionResult
]:
    command = _command()
    permit = WorkerExecutionPermit(
        attempt_id=_uuid("attempt"),
        command=command,
        checkpoint_ref=None,
        all_core_decisions_received=True,
        slot_acquired=True,
        retry_not_before=None,
    )
    control = _W1Control(permit)
    execution = _PreparedExecution(_prepared_posting_commit(command, permit))
    committer = _CapturingCommitter()
    worker = SourceCollectionWorker(
        control=control,
        execution_factory=lambda _permit: execution,
        session_factory=lambda: object(),
        lock_authority=object(),
        committer=committer,
        replayer=lambda *_args, **_kwargs: None,
        clock=lambda: NOW,
    )

    return control, execution, committer, worker.handle(command.model_dump(mode="json"))


def _mapping_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_mapping_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_mapping_keys(item) for item in value))
    return set()


def _string_values(value: object) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(_string_values(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_string_values(item) for item in value))
    return {value} if isinstance(value, str) else set()


def test_worker_commit_boundary_preserves_posting_evidence_and_public_scope() -> None:
    control, execution, committer, result = _run_posting_handoff()

    committed = committer.prepared[0]
    event = committed.events[0]
    envelope = event.payload
    public_event = event.model_dump(mode="json")
    public_envelope = public_event["payload"]
    private_command = _command()

    assert execution.closed is True
    assert control.stages == [
        CollectionStage.FETCH,
        CollectionStage.PARSE,
        CollectionStage.PERSIST,
        CollectionStage.DELIVER,
    ]
    assert control.finalized == [(control.permit, result)]
    assert event.event_type is SourceEventType.VERSION_AVAILABLE
    assert envelope.posting_sections == _posting_envelope(private_command).posting_sections
    assert envelope.evidence_spans == _posting_envelope(private_command).evidence_spans
    assert envelope.limitations == _posting_envelope(private_command).limitations
    assert set(public_event) == {
        "event_id",
        "event_type",
        "schema_version",
        "aggregate_id",
        "aggregate_revision",
        "occurred_at",
        "payload",
    }
    assert set(public_envelope) == {
        "schema_version",
        "source_id",
        "source_version_id",
        "extraction_revision_id",
        "company_id",
        "source_type",
        "url_or_path",
        "title",
        "policy",
        "acquisition_status",
        "extraction_status",
        "accuracy_status",
        "freshness_status",
        "published_at",
        "collected_at",
        "checked_at",
        "valid_from",
        "valid_to",
        "content_hash",
        "hash_profile_version",
        "parser_version",
        "language",
        "evidence_spans",
        "posting_sections",
        "retention_scope",
        "normalized_body_ref",
        "limitations",
        "metadata",
    }
    assert _mapping_keys(public_event).isdisjoint(
        {
            "claim",
            "claims",
            "requirement",
            "requirements",
            "requirement_satisfaction",
            "eligibility",
            "importance",
            "ranking",
            "recommendation",
            "recommendations",
            "analysis_completed",
            "knowledge_index_complete",
            "w3_ack",
        }
    )
    assert _string_values(public_event).isdisjoint(
        {
            str(private_command.job_id),
            str(private_command.authenticated_owner_ref),
            private_command.project_ref,
            str(private_command.purpose_ref),
            private_command.execution_fence,
        }
    )


def test_collection_finalization_does_not_announce_w3_analysis_completion() -> None:
    control, _execution, committer, result = _run_posting_handoff()

    public_event = committer.prepared[0].events[0].model_dump(mode="json")

    assert result.completion_kind is CompletionKind.COMPLETE
    assert control.finalized == [(control.permit, result)]
    assert public_event["event_type"] == SourceEventType.VERSION_AVAILABLE.value
    assert _mapping_keys(public_event).isdisjoint(
        {
            "analysis_completed",
            "analysis_completion",
            "knowledge_index_complete",
            "knowledge_index_completed",
            "w3_ack",
            "w3_acknowledged",
        }
    )
