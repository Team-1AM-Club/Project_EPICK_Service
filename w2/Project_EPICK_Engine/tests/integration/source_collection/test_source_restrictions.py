"""T062 source-restriction contracts against approved PostgreSQL.

The historical collection path deliberately reuses the T045 in-process W1
harness.  It exercises the production worker, parser, and persistence path,
but its transport never opens a socket.  Restriction persistence/service/outbox
assertions are intentionally RED until T064--T066 provide those seams.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from tests.integration.source_collection.test_static_posting_slice import (
    COMPANY_ID,
    NOW,
    OWNER_ID,
    PROJECT_ID,
    SOURCE_URL,
    _InputProvider,
    _limits,
    _StaticPostingSlice,
    _W1Harness,
    commit_prepared_collection,
    replay_committed_collection,
)

from epick_engine.source_collection.collector import (
    StaticFetchFailureCode,
    StaticFetchRequest,
    StaticFetchResult,
)
from epick_engine.source_collection.contracts import (
    AccuracyStatus,
    CollectionCommand,
    CollectionResult,
    CollectionStage,
    CoreSourceDecision,
    Permission,
    RestrictionStatus,
    SourceEvent,
    SourceEventType,
    SourceRestrictionSnapshot,
    SourceType,
)
from epick_engine.source_collection.parsing import extract_static_candidate
from epick_engine.source_collection.persistence import (
    Base,
    Evidence,
    OutboxEvent,
    Source,
    SourceVersion,
    get_current_source_restriction,
    list_source_restrictions,
    record_source_restriction,
)
from epick_engine.source_collection.restriction_service import (
    RestrictionMutation,
    SourceRestrictionService,
)
from epick_engine.source_collection.service import (
    StaticCollectionExecution,
    StaticCollectionInput,
)
from epick_engine.source_collection.worker import (
    SourceCollectionWorker,
    SourceEventAcknowledgement,
    SourceOutboxDeliveryWorker,
    SqlAlchemyOutboxDeliveryStore,
    WorkerExecutionPermit,
)

pytestmark = pytest.mark.approved_postgres

_RESTRICTION_EVENT_PRIVATE_FIELDS = frozenset(
    {
        "authenticated_owner_ref",
        "body_text",
        "evidence_refs",
        "execution_fence",
        "job_id",
        "owner_user_id",
        "owner_deletion_epoch",
        "project_id",
        "project_ref",
        "purpose_ref",
        "result_payload",
    }
)


@pytest.fixture
def database_engine(approved_postgres_url: URL) -> Iterator[Engine]:
    """Create one isolated real PostgreSQL schema for each test."""
    admin_engine = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema = f"epick_source_restrictions_{uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = admin_engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture
def session_factory(database_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(database_engine, expire_on_commit=False)


@dataclass(frozen=True)
class _HistoricalSource:
    source_id: UUID
    source_version_id: UUID
    evidence_ids: tuple[UUID, ...]
    current_source_version_id: UUID
    policy: Any


class _FailureCollector:
    """A test-local transport seam for one authorized collection failure."""

    def __init__(self, failure_code: StaticFetchFailureCode) -> None:
        self.failure_code = failure_code
        self.requests: list[StaticFetchRequest] = []

    def fetch(self, request: StaticFetchRequest) -> StaticFetchResult:
        self.requests.append(request)
        return StaticFetchResult(
            command_id=request.command_id,
            candidate=None,
            failure_code=self.failure_code,
        )

    def close(self) -> None:
        pass


@dataclass
class _RecordingPublisher:
    events: list[SourceEvent]

    def publish(self, source_event: SourceEvent) -> SourceEventAcknowledgement:
        self.events.append(source_event)
        return SourceEventAcknowledgement(event_id=source_event.event_id)

    def publish_private_result(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("public restriction events must not use a private channel")


def _collect_historical_source(session_factory: sessionmaker[Session]) -> _HistoricalSource:
    harness = _StaticPostingSlice(session_factory)
    accepted = harness.import_job_posting(
        owner_user_id=OWNER_ID,
        company_id=COMPANY_ID,
        url=SOURCE_URL,
        analysis_request_id=None,
        idempotency_key=f"restriction-history-{uuid4()}",
    )
    assert harness._policy is not None
    with session_factory() as session:
        source = session.get(Source, accepted.source_id)
        versions = session.scalars(
            select(SourceVersion)
            .where(SourceVersion.source_id == accepted.source_id)
            .order_by(SourceVersion.collected_at, SourceVersion.source_version_id)
        ).all()
        evidence_ids = tuple(
            session.scalars(
                select(Evidence.evidence_id)
                .where(Evidence.source_version_id == versions[0].source_version_id)
                .order_by(Evidence.chunk_order, Evidence.evidence_id)
            ).all()
        )
    assert source is not None
    assert len(versions) == 1
    assert source.current_source_version_id == versions[0].source_version_id
    assert evidence_ids
    return _HistoricalSource(
        source_id=accepted.source_id,
        source_version_id=versions[0].source_version_id,
        evidence_ids=evidence_ids,
        current_source_version_id=source.current_source_version_id,
        policy=harness._policy,
    )


def _history_snapshot(
    session_factory: sessionmaker[Session], source_id: UUID
) -> tuple[object, ...]:
    with session_factory() as session:
        source = session.get(Source, source_id)
        versions = tuple(
            session.scalars(
                select(SourceVersion)
                .where(SourceVersion.source_id == source_id)
                .order_by(SourceVersion.collected_at, SourceVersion.source_version_id)
            ).all()
        )
        version_snapshot = tuple(
            (
                row.source_version_id,
                row.source_id,
                row.company_id,
                row.title,
                row.source_type,
                row.canonical_url,
                row.content_hash,
                row.hash_profile_version,
                row.representation,
                row.first_parser_version,
                row.collected_at,
                row.published_at,
                row.valid_from,
                row.valid_to,
                row.language,
                row.policy_decision_id,
            )
            for row in versions
        )
        evidence_snapshot = tuple(
            session.scalars(
                select(Evidence)
                .join(SourceVersion, Evidence.source_version_id == SourceVersion.source_version_id)
                .where(SourceVersion.source_id == source_id)
                .order_by(Evidence.chunk_order, Evidence.evidence_id)
            ).all()
        )
        evidence_snapshot = tuple(
            (
                row.evidence_id,
                row.source_version_id,
                row.evidence_key,
                row.section_title,
                row.text_excerpt,
                row.locator,
                row.chunk_order,
                row.origin_kind,
            )
            for row in evidence_snapshot
        )
    assert source is not None
    return source.current_source_version_id, version_snapshot, evidence_snapshot


def _observe_authorized_failure(
    *,
    session_factory: sessionmaker[Session],
    history: _HistoricalSource,
    failure_code: StaticFetchFailureCode,
    input_version: int,
) -> tuple[CollectionResult, _FailureCollector]:
    command = CollectionCommand(
        schema_version="w2.collection.v1",
        command_id=uuid4(),
        job_id=uuid4(),
        authenticated_owner_ref=OWNER_ID,
        project_ref=str(PROJECT_ID),
        company_id=COMPANY_ID,
        source_id=history.source_id,
        input_version=input_version,
        execution_fence=f"restriction-failure-{failure_code.value}-{uuid4()}",
        purpose_ref=uuid4(),
        core_source_decision=CoreSourceDecision(
            is_core=True,
            decided_by="restriction-integration-test",
            rationale="authorized synthetic status observation",
            decision_revision=1,
            analysis_input_version=input_version,
        ),
        resume_stage=CollectionStage.POLICY,
        policy_revision=None,
        owner_deletion_epoch=0,
    )
    input_value = StaticCollectionInput(
        source_id=history.source_id,
        company_id=COMPANY_ID,
        source_url=SOURCE_URL,
        title="Synthetic static posting",
        source_type=SourceType.JOB_POSTING,
        policy=history.policy,
        policy_revision=1,
        robots_permission=Permission.ALLOWED,
        limits=_limits(),
        result_version=input_version,
        aggregate_revision=input_version,
        language="en",
    )
    permit = WorkerExecutionPermit(
        attempt_id=uuid4(),
        command=command,
        checkpoint_ref=None,
        all_core_decisions_received=True,
        slot_acquired=True,
        retry_not_before=None,
    )
    control = _W1Harness(permit)
    collector = _FailureCollector(failure_code)
    worker = SourceCollectionWorker(
        control=control,
        execution_factory=lambda issued: StaticCollectionExecution(
            attempt_id=issued.attempt_id,
            input_provider=_InputProvider(input_value),
            collector=collector,
            parser=extract_static_candidate,
            clock=lambda: NOW,
            uuid_factory=uuid4,
        ),
        session_factory=session_factory,
        lock_authority=_StaticPostingSlice._lock_authority,
        committer=commit_prepared_collection,
        replayer=replay_committed_collection,
        clock=lambda: NOW,
    )
    result = worker.handle(command.model_dump(mode="json"))
    assert control.finalized is result
    assert len(collector.requests) == 1
    request = collector.requests[0]
    assert request.command_id == command.command_id
    assert request.source_url == SOURCE_URL
    assert request.robots_permission is Permission.ALLOWED
    assert request.redirect_robots_permissions == ()
    return result, collector


def _observe_authorized_http_failure(
    *,
    session_factory: sessionmaker[Session],
    history: _HistoricalSource,
    status_code: int,
) -> tuple[CollectionResult, _FailureCollector]:
    return _observe_authorized_failure(
        session_factory=session_factory,
        history=history,
        failure_code={
            403: StaticFetchFailureCode.ACCESS_DENIED,
            404: StaticFetchFailureCode.NOT_FOUND,
        }[status_code],
        input_version=status_code,
    )


class _FixtureRestrictionAuthority:
    def authorize(self, *, request: RestrictionMutation) -> str:
        del request
        return "fixture:legacy-restriction"


def _restriction_service(
    session_factory: sessionmaker[Session],
) -> SourceRestrictionService:
    return SourceRestrictionService(
        session_factory=session_factory,
        authority=_FixtureRestrictionAuthority(),
    )


def _create_restriction_request(
    history: _HistoricalSource,
    *,
    changed_at: datetime,
    reason_code: str,
    replacement_ref: UUID | None = None,
) -> RestrictionMutation:
    return RestrictionMutation(
        request_id=uuid4(),
        kind="create",
        source_id=history.source_id,
        source_version_id=history.source_version_id,
        restriction_id=None,
        accuracy_status=AccuracyStatus.ERROR_CONFIRMED,
        reason_code=reason_code,
        evidence_refs=(f"fixture:{reason_code.lower()}",),
        changed_at=changed_at,
        replacement_ref=replacement_ref,
    )


def _register_replacement_source(session_factory: sessionmaker[Session]) -> UUID:
    replacement_source_id = uuid4()
    with session_factory.begin() as session:
        session.add(
            Source(
                source_id=replacement_source_id,
                company_id=COMPANY_ID,
                source_type=SourceType.JOB_POSTING.value,
                canonical_url=f"https://replacement.example.test/{replacement_source_id}",
                title="Registered replacement source",
            )
        )
    return replacement_source_id


def _restriction_snapshot(
    history: _HistoricalSource,
    *,
    restriction_id: UUID,
    restriction_revision: int,
    restriction_status: RestrictionStatus,
    accuracy_status: AccuracyStatus,
    changed_at: datetime,
    replacement_ref: UUID | None = None,
) -> SourceRestrictionSnapshot:
    return SourceRestrictionSnapshot(
        restriction_id=restriction_id,
        source_id=history.source_id,
        source_version_id=history.source_version_id,
        restriction_revision=restriction_revision,
        restriction_status=restriction_status,
        accuracy_status=accuracy_status,
        reason_code="WRONG_COMPANY",
        changed_at=changed_at,
        replacement_ref=replacement_ref,
    )


def test_authorized_404_preserves_historical_version_evidence_and_current_pointer(
    session_factory: sessionmaker[Session],
) -> None:
    history = _collect_historical_source(session_factory)
    before = _history_snapshot(session_factory, history.source_id)

    result, collector = _observe_authorized_http_failure(
        session_factory=session_factory,
        history=history,
        status_code=404,
    )

    assert result.failures
    assert result.failures[0].code == StaticFetchFailureCode.NOT_FOUND.value
    assert collector.requests[0].source_url == SOURCE_URL
    assert _history_snapshot(session_factory, history.source_id) == before


def test_explicit_active_restriction_persists_separately_from_historical_snapshot(
    session_factory: sessionmaker[Session],
) -> None:
    history = _collect_historical_source(session_factory)
    before = _history_snapshot(session_factory, history.source_id)
    result, _collector = _observe_authorized_http_failure(
        session_factory=session_factory,
        history=history,
        status_code=404,
    )
    assert result.failures[0].code == StaticFetchFailureCode.NOT_FOUND.value
    assert _history_snapshot(session_factory, history.source_id) == before

    active = _restriction_service(session_factory).apply(
        _create_restriction_request(
            history,
            changed_at=NOW,
            reason_code="HTTP_404_CORRECTION",
        )
    )

    assert _history_snapshot(session_factory, history.source_id) == before
    with session_factory() as session:
        current = get_current_source_restriction(
            session,
            source_id=history.source_id,
            restriction_id=active.restriction_id,
        )
    assert active.restriction_status is RestrictionStatus.ACTIVE
    assert active.accuracy_status is AccuracyStatus.ERROR_CONFIRMED
    assert current == active


def test_explicit_403_correction_clear_and_reactivate_reuse_stable_identity(
    session_factory: sessionmaker[Session],
) -> None:
    history = _collect_historical_source(session_factory)
    before = _history_snapshot(session_factory, history.source_id)
    result, _collector = _observe_authorized_http_failure(
        session_factory=session_factory,
        history=history,
        status_code=403,
    )
    assert result.failures[0].code == StaticFetchFailureCode.ACCESS_DENIED.value
    assert _history_snapshot(session_factory, history.source_id) == before

    replacement_source_id = _register_replacement_source(session_factory)
    assert replacement_source_id != history.source_id
    service = _restriction_service(session_factory)
    create_request = _create_restriction_request(
        history,
        changed_at=NOW,
        reason_code=StaticFetchFailureCode.ACCESS_DENIED.value,
        replacement_ref=replacement_source_id,
    )
    active = service.apply(create_request)
    cleared_request = create_request.model_copy(
        update={
            "request_id": uuid4(),
            "kind": "clear",
            "restriction_id": active.restriction_id,
            "accuracy_status": AccuracyStatus.VERIFIED_IN_SCOPE,
            "reason_code": "CORRECTION_CONFIRMED",
            "evidence_refs": ("fixture:confirmed-correction",),
            "changed_at": NOW + timedelta(minutes=1),
            "replacement_ref": None,
        }
    )
    cleared = service.apply(cleared_request)
    reactivated_request = cleared_request.model_copy(
        update={
            "request_id": uuid4(),
            "kind": "reactivate",
            "restriction_id": active.restriction_id,
            "accuracy_status": AccuracyStatus.ERROR_CONFIRMED,
            "reason_code": "REACTIVATED_AFTER_REVIEW",
            "evidence_refs": ("fixture:reactivated-after-review",),
            "changed_at": NOW + timedelta(minutes=2),
        }
    )
    reactivated = service.apply(reactivated_request)
    with session_factory() as session:
        current = get_current_source_restriction(
            session,
            source_id=history.source_id,
            restriction_id=active.restriction_id,
        )

    assert active.restriction_status == RestrictionStatus.ACTIVE
    assert active.accuracy_status == AccuracyStatus.ERROR_CONFIRMED
    assert active.replacement_ref == replacement_source_id
    assert cleared.restriction_id == active.restriction_id
    assert cleared.restriction_revision == active.restriction_revision + 1
    assert cleared.restriction_status == RestrictionStatus.CLEARED
    assert cleared.accuracy_status == AccuracyStatus.VERIFIED_IN_SCOPE
    assert reactivated.restriction_id == active.restriction_id
    assert reactivated.restriction_revision == cleared.restriction_revision + 1
    assert reactivated.restriction_status == RestrictionStatus.ACTIVE
    assert current == reactivated
    assert _history_snapshot(session_factory, history.source_id) == before


def test_explicit_non_http_correction_clear_keeps_historical_snapshot(
    session_factory: sessionmaker[Session],
) -> None:
    history = _collect_historical_source(session_factory)
    before = _history_snapshot(session_factory, history.source_id)
    result, _collector = _observe_authorized_failure(
        session_factory=session_factory,
        history=history,
        failure_code=StaticFetchFailureCode.FETCH_TIMEOUT,
        input_version=1,
    )
    assert result.failures[0].code == StaticFetchFailureCode.FETCH_TIMEOUT.value
    assert _history_snapshot(session_factory, history.source_id) == before

    service = _restriction_service(session_factory)
    create_request = _create_restriction_request(
        history,
        changed_at=NOW,
        reason_code=StaticFetchFailureCode.FETCH_TIMEOUT.value,
    )
    active = service.apply(create_request)
    cleared = service.apply(
        create_request.model_copy(
            update={
                "request_id": uuid4(),
                "kind": "clear",
                "restriction_id": active.restriction_id,
                "accuracy_status": AccuracyStatus.VERIFIED_IN_SCOPE,
                "reason_code": "RETRY_CONFIRMED",
                "evidence_refs": ("fixture:retry-confirmed",),
                "changed_at": NOW + timedelta(minutes=1),
            }
        )
    )
    with session_factory() as session:
        current = get_current_source_restriction(
            session,
            source_id=history.source_id,
            restriction_id=active.restriction_id,
        )

    assert active.restriction_status == RestrictionStatus.ACTIVE
    assert active.accuracy_status == AccuracyStatus.ERROR_CONFIRMED
    assert cleared.restriction_id == active.restriction_id
    assert cleared.restriction_revision == active.restriction_revision + 1
    assert cleared.restriction_status == RestrictionStatus.CLEARED
    assert cleared.accuracy_status == AccuracyStatus.VERIFIED_IN_SCOPE
    assert current == cleared
    assert _history_snapshot(session_factory, history.source_id) == before


def test_restriction_events_are_immutable_public_snapshots_and_do_not_rewind_overlay(
    session_factory: sessionmaker[Session],
) -> None:
    history = _collect_historical_source(session_factory)
    with session_factory() as session:
        prior_aggregate_revision = session.scalar(
            select(func.coalesce(func.max(OutboxEvent.aggregate_revision), 0)).where(
                OutboxEvent.aggregate_id == history.source_id
            )
        )
    assert prior_aggregate_revision is not None
    initial_worker = SourceOutboxDeliveryWorker(
        publisher=_RecordingPublisher(events=[]),
        store=SqlAlchemyOutboxDeliveryStore(session_factory=session_factory),
    )
    assert initial_worker.deliver_pending(limit=10) == 1
    restriction_id = uuid4()
    revision_one = _restriction_snapshot(
        history,
        restriction_id=restriction_id,
        restriction_revision=1,
        restriction_status=RestrictionStatus.ACTIVE,
        accuracy_status=AccuracyStatus.ERROR_CONFIRMED,
        changed_at=NOW - timedelta(minutes=2),
    )
    revision_two = _restriction_snapshot(
        history,
        restriction_id=restriction_id,
        restriction_revision=2,
        restriction_status=RestrictionStatus.CLEARED,
        accuracy_status=AccuracyStatus.VERIFIED_IN_SCOPE,
        changed_at=NOW - timedelta(minutes=1),
    )
    revision_three = _restriction_snapshot(
        history,
        restriction_id=restriction_id,
        restriction_revision=3,
        restriction_status=RestrictionStatus.ACTIVE,
        accuracy_status=AccuracyStatus.ERROR_CONFIRMED,
        changed_at=NOW,
    )
    expected_public_records = [
        (
            SourceEventType.RESTRICTION_CHANGED.value,
            history.source_id,
            prior_aggregate_revision + offset,
            "w2.source.v1",
            snapshot.model_dump(mode="json"),
        )
        for offset, snapshot in enumerate((revision_one, revision_two, revision_three), start=1)
    ]

    with session_factory.begin() as session:
        record_source_restriction(
            session,
            snapshot=revision_one,
            evidence_refs=("fixture:revision-1",),
        )
        record_source_restriction(
            session,
            snapshot=revision_two,
            evidence_refs=("fixture:revision-2",),
        )
        record_source_restriction(
            session,
            snapshot=revision_three,
            evidence_refs=("fixture:revision-3",),
        )

    with session_factory() as session:
        current = get_current_source_restriction(
            session,
            source_id=history.source_id,
            restriction_id=restriction_id,
        )
        history_rows = list_source_restrictions(session, source_id=history.source_id)
        outbox_rows = session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.event_type == SourceEventType.RESTRICTION_CHANGED.value)
            .order_by(OutboxEvent.aggregate_revision, OutboxEvent.event_id)
        ).all()
    assert current == revision_three
    assert [row.restriction_revision for row in history_rows] == [1, 2, 3]
    assert [
        (
            row.event_type,
            row.aggregate_id,
            row.aggregate_revision,
            row.schema_version,
            row.payload,
        )
        for row in outbox_rows
    ] == expected_public_records
    assert all(
        _RESTRICTION_EVENT_PRIVATE_FIELDS.isdisjoint(_deep_keys(row.payload)) for row in outbox_rows
    )
    outbox_event_ids = [row.event_id for row in outbox_rows]
    assert (
        _history_snapshot(session_factory, history.source_id)[0]
        == history.current_source_version_id
    )

    publisher = _RecordingPublisher(events=[])
    worker = SourceOutboxDeliveryWorker(
        publisher=publisher,
        store=SqlAlchemyOutboxDeliveryStore(session_factory=session_factory),
    )
    assert worker.deliver_pending(limit=10) == 3
    assert [event.event_id for event in publisher.events] == outbox_event_ids
    assert [
        (
            event.event_type.value,
            event.aggregate_id,
            event.aggregate_revision,
            event.schema_version,
            event.model_dump(mode="json")["payload"],
        )
        for event in publisher.events
    ] == expected_public_records


def _deep_keys(value: object) -> set[str]:
    if isinstance(value, Mapping):
        return set(value).union(*(_deep_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_deep_keys(item) for item in value))
    return set()
