"""PostgreSQL contract tests for W1-scoped private collection deletion.

The W1 deletion marker and the T067 worker seam do not exist yet.  The RED
tests below deliberately name that seam instead of simulating deletion in the
test: ``PrivateDeletionCommand`` plus ``process_private_deletion``.  Public
source history is intentionally not part of the command's deletion scope.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from threading import Event, Thread, current_thread
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, event, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from tests.integration.source_collection.test_atomic_persistence import (
    _command as _collection_command,
)
from tests.integration.source_collection.test_atomic_persistence import (
    _complete_prepared,
)
from tests.unit.source_collection.test_worker import _Control, _Execution, _Factory, _permit

from epick_engine.source_collection.contracts import CollectionCommand, CollectionStage
from epick_engine.source_collection.persistence import (
    Base,
    CollectionAttempt,
    Company,
    Evidence,
    ExecutionAuthorityGrant,
    OutboxEvent,
    PersistenceConflict,
    PrivateDeletionOwnerState,
    PrivateDeletionReceipt,
    RequestDeduplication,
    RetainedBody,
    Source,
    SourcePolicyDecision,
    SourceVersion,
    StaleExecution,
    apply_private_deletion,
    assert_current_attempt,
)
from epick_engine.source_collection.persistence import (
    commit_prepared_collection as _commit_prepared_collection,
)
from epick_engine.source_collection.persistence import (
    replay_committed_collection as _replay_committed_collection,
)
from epick_engine.source_collection.private_deletion_v2 import PrivateDeletionScope
from epick_engine.source_collection.private_scope import (
    PrivateScopeRejected,
    PrivateWriteAuthorityDecision,
    PrivateWriteScope,
)
from epick_engine.source_collection.worker import (
    PrivateDeletionAcknowledgement,
    PrivateDeletionCommand,
    SourceCollectionWorker,
)

NOW = datetime(2031, 6, 1, 9, 0, tzinfo=UTC)
OWNER_A = UUID("00000000-0000-4000-8000-000000006301")
OWNER_B = UUID("00000000-0000-4000-8000-000000006302")
PROJECT_A_1 = UUID("00000000-0000-4000-8000-000000006311")
PROJECT_A_2 = UUID("00000000-0000-4000-8000-000000006312")
PROJECT_B_1 = UUID("00000000-0000-4000-8000-000000006321")


def _worker_scope(command: CollectionCommand) -> PrivateWriteScope:
    return PrivateWriteScope(
        PrivateWriteAuthorityDecision(
            owner_user_id=command.authenticated_owner_ref,
            owner_deletion_epoch=command.owner_deletion_epoch,
            scope=PrivateDeletionScope(kind="PROJECT", project_id=PROJECT_A_1),
            authority_ref="w1:test-private-deletion-authority",
            command_id=command.command_id,
            job_id=command.job_id,
        )
    )


def _commit_worker_result(session_factory, *, command, **kwargs):
    kwargs.setdefault("private_scope", _worker_scope(command))
    return _commit_prepared_collection(session_factory, command=command, **kwargs)


def _replay_worker_result(session_factory, *, command, **kwargs):
    kwargs.setdefault("private_scope", _worker_scope(command))
    return _replay_committed_collection(session_factory, command=command, **kwargs)


@pytest.fixture
def database_engine(approved_postgres_url) -> Iterator[Engine]:
    """Create one synthetic schema; no production or user rows are touched."""

    admin_engine = create_engine(approved_postgres_url, pool_pre_ping=True)

    @event.listens_for(admin_engine, "connect")
    def set_isolated_test_timeouts(dbapi_connection, _) -> None:
        """Bound a failed race so its PostgreSQL lock cannot outlive this fixture."""

        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET lock_timeout = '1500ms'")
            cursor.execute("SET statement_timeout = '5000ms'")
        dbapi_connection.commit()

    schema = f"epick_private_deletion_{uuid4().hex}"
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
class _SeededState:
    source_id: UUID
    source_version_id: UUID
    evidence_id: UUID
    retained_body_id: UUID
    public_event_id: UUID
    attempt_a_project_1: UUID
    attempt_a_project_2: UUID
    attempt_b_project_1: UUID
    dedup_a_project_1: UUID
    dedup_a_project_2: UUID
    dedup_b_project_1: UUID


@dataclass
class _PrivateDeletionSideEffects:
    """W1-owned private adapters supplied to the future T067 worker seam."""

    events: list[tuple[str, object]] = field(default_factory=list)
    fail_purge: bool = False

    def purge_private_references(
        self,
        *,
        owner_user_id: UUID,
        private_reference_keys: frozenset[str],
    ) -> None:
        """Record the W1 private-outbox/checkpoint/cache cleanup port call."""

        self.events.append(("purge", (owner_user_id, private_reference_keys)))
        if self.fail_purge:
            raise RuntimeError("synthetic private purge failure")

    def acknowledge(self, *, deletion_id: UUID, deletion_epoch: int) -> None:
        self.events.append(("acknowledge", (deletion_id, deletion_epoch)))


@dataclass
class _DeletionEpochLocker:
    """Synthetic W1 marker authority used by the real W2 worker commit path."""

    latest_epoch_by_owner: dict[UUID, int]

    def __call__(
        self,
        _: Session,
        *,
        command: CollectionCommand,
        attempt_id: UUID,
    ) -> ExecutionAuthorityGrant:
        if (
            command.owner_deletion_epoch
            < self.latest_epoch_by_owner[command.authenticated_owner_ref]
        ):
            raise StaleExecution("W1 deletion marker has advanced")
        return ExecutionAuthorityGrant(
            attempt_id=attempt_id,
            owner_user_id=command.authenticated_owner_ref,
            job_id=command.job_id,
            command_id=command.command_id,
            company_id=command.company_id,
            source_id=command.source_id,
            input_version=command.input_version,
            execution_fence=command.execution_fence,
            owner_deletion_epoch=command.owner_deletion_epoch,
            pointer_eligible=True,
            private_scope=_worker_scope(command),
        )


def _attempt(
    *,
    owner_user_id: UUID,
    project_id: UUID | None,
    source_id: UUID,
    deletion_epoch: int,
    label: str,
) -> CollectionAttempt:
    return CollectionAttempt(
        attempt_id=uuid4(),
        owner_user_id=owner_user_id,
        job_id=uuid4(),
        project_id=project_id,
        command_id=uuid4(),
        input_version=1,
        target_ref=str(source_id),
        purpose_ref=f"synthetic-private-purpose:{label}",
        core_source_decision={
            "is_core": True,
            "decided_by": "private-deletion-test",
            "rationale": "shared public source must survive private deletion",
            "decision_revision": 1,
            "analysis_input_version": 1,
        },
        resume_stage="policy",
        policy_revision=None,
        result_version=1,
        execution_fence=f"private-deletion-fence:{label}",
        owner_deletion_epoch=deletion_epoch,
        parser_execution_id=None,
        checkpoint_ref=f"checkpoint://private/{label}",
        result_refs=[{"source_id": str(source_id), "private_result_ref": label}],
        failures=[],
        required_actions=[],
        result_payload={"private_result_ref": label, "source_id": str(source_id)},
        finalized_at=NOW,
    )


def _deduplication(
    *,
    owner_user_id: UUID,
    source_id: UUID,
    label: str,
) -> RequestDeduplication:
    return RequestDeduplication(
        request_deduplication_id=uuid4(),
        owner_user_id=owner_user_id,
        operation="source_collection",
        idempotency_key=f"private-deletion:{label}",
        request_hash="a" * 64,
        accepted_resource_ref=f"source:{source_id}",
        input_version=1,
        created_at=NOW,
    )


def _seed_shared_public_source(
    session_factory: sessionmaker[Session],
) -> _SeededState:
    """Seed A and B private rows around one immutable, public source history."""

    with session_factory() as session:
        company = Company(
            company_id=uuid4(),
            legal_name="Private deletion synthetic company",
            aliases=["Deletion synthetic"],
            official_domains=["deletion.example.test"],
            legal_identifiers={"registration": "DELETE-630"},
            identity_status="verified",
            identity_evidence=["synthetic://private-deletion/company"],
        )
        source = Source(
            source_id=uuid4(),
            company_id=company.company_id,
            source_type="job_posting",
            canonical_url="https://deletion.example.test/jobs/630",
            title="Synthetic private deletion engineer",
        )
        policy = SourcePolicyDecision(
            policy_decision_id=uuid4(),
            source_id=source.source_id,
            revision=1,
            official_status="verified",
            access_class="public",
            collection_permission="allowed",
            excerpt_storage_permission="allowed",
            body_storage_permission="allowed",
            redistribution_permission="unknown",
            evidence_refs=["synthetic://private-deletion/policy"],
            checked_at=NOW,
            policy_version="private-deletion-policy-v1",
        )
        version = SourceVersion(
            source_version_id=uuid4(),
            source_id=source.source_id,
            company_id=company.company_id,
            title=source.title,
            source_type=source.source_type,
            canonical_url=source.canonical_url,
            content_hash="b" * 64,
            hash_profile_version="html-v1",
            representation="html",
            first_parser_version="job-posting-v1",
            collected_at=NOW,
            published_at={"status": "unknown", "raw_text": None, "value": None},
            valid_from={"status": "unknown", "raw_text": None, "value": None},
            valid_to={"status": "unknown", "raw_text": None, "value": None},
            language="ko",
            policy_decision_id=policy.policy_decision_id,
        )
        evidence = Evidence(
            evidence_id=uuid4(),
            source_version_id=version.source_version_id,
            evidence_key="required:0",
            section_title="필수요건",
            text_excerpt="합성 AWS 운영 경험",
            locator={"kind": "css", "selector": "#requirements", "start": 0, "end": 12},
            chunk_order=0,
            origin_kind="direct",
        )
        retained_body = RetainedBody(
            body_id=uuid4(),
            source_version_id=version.source_version_id,
            source_id=source.source_id,
            normalization_version="html-v1",
            body_text="합성 공개 보관 원문",
            necessity_reason="public source history retention contract",
            policy_decision_id=policy.policy_decision_id,
            retained_at=NOW,
            retention_policy_version="private-deletion-retention-v1",
            retention_limit_bytes=1024,
        )
        public_event = OutboxEvent(
            event_id=uuid4(),
            aggregate_id=source.source_id,
            aggregate_revision=1,
            event_type="source.version.available",
            schema_version="w2.source-event.v1",
            payload={
                "source_id": str(source.source_id),
                "source_version_id": str(version.source_version_id),
                "evidence_id": str(evidence.evidence_id),
            },
            occurred_at=NOW,
            delivery_state="pending",
        )
        attempt_a_project_1 = _attempt(
            owner_user_id=OWNER_A,
            project_id=PROJECT_A_1,
            source_id=source.source_id,
            deletion_epoch=4,
            label="a-project-1",
        )
        attempt_a_project_2 = _attempt(
            owner_user_id=OWNER_A,
            project_id=PROJECT_A_2,
            source_id=source.source_id,
            deletion_epoch=4,
            label="a-project-2",
        )
        attempt_b_project_1 = _attempt(
            owner_user_id=OWNER_B,
            project_id=PROJECT_B_1,
            source_id=source.source_id,
            deletion_epoch=4,
            label="b-project-1",
        )
        dedup_a_project_1 = _deduplication(
            owner_user_id=OWNER_A,
            source_id=source.source_id,
            label="a-project-1",
        )
        dedup_a_project_2 = _deduplication(
            owner_user_id=OWNER_A,
            source_id=source.source_id,
            label="a-project-2",
        )
        dedup_b_project_1 = _deduplication(
            owner_user_id=OWNER_B,
            source_id=source.source_id,
            label="b-project-1",
        )
        session.add_all([company, source, policy])
        session.flush()
        session.add(version)
        session.flush()
        session.add_all(
            [
                evidence,
                retained_body,
                public_event,
                attempt_a_project_1,
                attempt_a_project_2,
                attempt_b_project_1,
                dedup_a_project_1,
                dedup_a_project_2,
                dedup_b_project_1,
            ]
        )
        session.commit()

        return _SeededState(
            source_id=source.source_id,
            source_version_id=version.source_version_id,
            evidence_id=evidence.evidence_id,
            retained_body_id=retained_body.body_id,
            public_event_id=public_event.event_id,
            attempt_a_project_1=attempt_a_project_1.attempt_id,
            attempt_a_project_2=attempt_a_project_2.attempt_id,
            attempt_b_project_1=attempt_b_project_1.attempt_id,
            dedup_a_project_1=dedup_a_project_1.request_deduplication_id,
            dedup_a_project_2=dedup_a_project_2.request_deduplication_id,
            dedup_b_project_1=dedup_b_project_1.request_deduplication_id,
        )


def _process_private_deletion(
    *,
    session_factory: sessionmaker[Session],
    owner_user_id: UUID,
    deletion_id: UUID,
    deletion_epoch: int,
    attempt_ids: frozenset[UUID],
    request_deduplication_ids: frozenset[UUID],
    private_reference_keys: frozenset[str],
    side_effects: _PrivateDeletionSideEffects,
) -> PrivateDeletionAcknowledgement:
    """Invoke the concrete T067 worker contract; intentionally unavailable today."""

    from epick_engine.source_collection.worker import (  # noqa: PLC0415
        PrivateDeletionCommand,
        process_private_deletion,
    )

    return process_private_deletion(
        session_factory=session_factory,
        command=PrivateDeletionCommand(
            deletion_id=deletion_id,
            owner_user_id=owner_user_id,
            deletion_epoch=deletion_epoch,
            attempt_ids=attempt_ids,
            request_deduplication_ids=request_deduplication_ids,
            private_reference_keys=private_reference_keys,
        ),
        side_effects=side_effects,
    )


def _attempt_ids_for_owner(
    session_factory: sessionmaker[Session],
    owner_user_id: UUID,
) -> set[UUID]:
    with session_factory() as session:
        return set(
            session.scalars(
                select(CollectionAttempt.attempt_id).where(
                    CollectionAttempt.owner_user_id == owner_user_id
                )
            )
        )


def _dedup_ids_for_owner(
    session_factory: sessionmaker[Session],
    owner_user_id: UUID,
) -> set[UUID]:
    with session_factory() as session:
        return set(
            session.scalars(
                select(RequestDeduplication.request_deduplication_id).where(
                    RequestDeduplication.owner_user_id == owner_user_id
                )
            )
        )


def _private_result_reference(
    session_factory: sessionmaker[Session],
    attempt_id: UUID,
) -> str:
    with session_factory() as session:
        attempt = session.get(CollectionAttempt, attempt_id)
        assert attempt is not None
        return str(attempt.result_payload["private_result_ref"])


def _public_counts(session_factory: sessionmaker[Session]) -> tuple[int, int, int, int]:
    with session_factory() as session:
        return (
            session.scalar(select(func.count()).select_from(Source)) or 0,
            session.scalar(select(func.count()).select_from(SourceVersion)) or 0,
            session.scalar(select(func.count()).select_from(Evidence)) or 0,
            session.scalar(select(func.count()).select_from(OutboxEvent)) or 0,
        )


def _assert_shared_public_history_survives(
    session_factory: sessionmaker[Session],
    state: _SeededState,
) -> None:
    with session_factory() as session:
        assert session.get(Source, state.source_id) is not None
        assert session.get(SourceVersion, state.source_version_id) is not None
        assert session.get(Evidence, state.evidence_id) is not None
        assert session.get(RetainedBody, state.retained_body_id) is not None
        assert session.get(OutboxEvent, state.public_event_id) is not None


def _wait_for_postgres_lock(database_engine: Engine, backend_pid: int) -> None:
    deadline = monotonic() + 1.0
    with database_engine.connect() as connection:
        while monotonic() < deadline:
            wait_event_type = connection.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :backend_pid"),
                {"backend_pid": backend_pid},
            )
            if wait_event_type == "Lock":
                return
            sleep(0.01)
    raise AssertionError("concurrent private deletion never waited on the owner state lock")


@pytest.mark.approved_postgres
def test_isolated_postgres_timeouts_survive_rollback_and_pool_reuse(
    database_engine: Engine,
) -> None:
    with database_engine.connect() as first_connection:
        assert first_connection.scalar(text("SHOW lock_timeout")) == "1500ms"
        assert first_connection.scalar(text("SHOW statement_timeout")) == "5s"
        first_connection.execute(text("SELECT 1"))
        first_connection.rollback()

    with database_engine.connect() as second_connection:
        assert second_connection.scalar(text("SHOW lock_timeout")) == "1500ms"
        assert second_connection.scalar(text("SHOW statement_timeout")) == "5s"


@pytest.mark.approved_postgres
def test_same_deletion_id_with_different_private_scope_is_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    state = _seed_shared_public_source(session_factory)
    first = PrivateDeletionCommand(
        deletion_id=uuid4(),
        owner_user_id=OWNER_A,
        deletion_epoch=7,
        attempt_ids=frozenset({state.attempt_a_project_1}),
        request_deduplication_ids=frozenset({state.dedup_a_project_1}),
        private_reference_keys=frozenset({"a-project-1"}),
    )

    with session_factory.begin() as session:
        assert apply_private_deletion(session, first) == "APPLIED"

    with pytest.raises(PersistenceConflict, match="deletion receipt does not match command"):
        with session_factory.begin() as session:
            apply_private_deletion(
                session,
                replace(first, attempt_ids=frozenset({state.attempt_a_project_2})),
            )


@pytest.mark.approved_postgres
def test_private_deletion_receipt_is_owner_scoped_and_monotonic(
    session_factory: sessionmaker[Session],
) -> None:
    state = _seed_shared_public_source(session_factory)
    first = PrivateDeletionCommand(
        deletion_id=uuid4(),
        owner_user_id=OWNER_A,
        deletion_epoch=7,
        attempt_ids=frozenset({state.attempt_a_project_1}),
        request_deduplication_ids=frozenset({state.dedup_a_project_1}),
        private_reference_keys=frozenset({"a-project-1"}),
    )

    with session_factory.begin() as session:
        assert apply_private_deletion(session, first) == "APPLIED"

    late = replace(
        first,
        deletion_id=uuid4(),
        deletion_epoch=6,
        attempt_ids=frozenset({state.attempt_a_project_2}),
        request_deduplication_ids=frozenset({state.dedup_a_project_2}),
        private_reference_keys=frozenset({"a-project-2"}),
    )
    with session_factory.begin() as session:
        assert apply_private_deletion(session, late) == "STALE"

    assert _attempt_ids_for_owner(session_factory, OWNER_A) == {state.attempt_a_project_2}
    assert _dedup_ids_for_owner(session_factory, OWNER_A) == {state.dedup_a_project_2}
    assert _attempt_ids_for_owner(session_factory, OWNER_B) == {state.attempt_b_project_1}
    assert _dedup_ids_for_owner(session_factory, OWNER_B) == {state.dedup_b_project_1}


@pytest.mark.approved_postgres
def test_private_deletion_receipt_returns_duplicate_for_the_same_command(
    session_factory: sessionmaker[Session],
) -> None:
    state = _seed_shared_public_source(session_factory)
    command = PrivateDeletionCommand(
        deletion_id=uuid4(),
        owner_user_id=OWNER_A,
        deletion_epoch=7,
        attempt_ids=frozenset({state.attempt_a_project_1}),
        request_deduplication_ids=frozenset({state.dedup_a_project_1}),
        private_reference_keys=frozenset({"a-project-1"}),
    )

    with session_factory.begin() as session:
        assert apply_private_deletion(session, command) == "APPLIED"
    with session_factory.begin() as session:
        assert apply_private_deletion(session, command) == "DUPLICATE"


@pytest.mark.approved_postgres
def test_private_deletion_receipt_round_trips_signed_64_bit_maximum_epoch(
    session_factory: sessionmaker[Session],
) -> None:
    deletion_id = uuid4()
    command = PrivateDeletionCommand(
        deletion_id=deletion_id,
        owner_user_id=OWNER_A,
        deletion_epoch=9_223_372_036_854_775_807,
        attempt_ids=frozenset(),
        request_deduplication_ids=frozenset(),
        private_reference_keys=frozenset({"signed-64-bit-maximum"}),
    )

    with session_factory.begin() as session:
        assert apply_private_deletion(session, command) == "APPLIED"

    with session_factory() as session:
        owner_state = session.get(PrivateDeletionOwnerState, OWNER_A)
        receipt = session.get(PrivateDeletionReceipt, deletion_id)
        assert owner_state is not None
        assert receipt is not None
        assert owner_state.latest_epoch == 9_223_372_036_854_775_807
        assert receipt.deletion_epoch == 9_223_372_036_854_775_807


@pytest.mark.approved_postgres
def test_concurrent_first_private_deletion_receipts_serialize_on_owner_state(
    database_engine: Engine,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        assert session.get(PrivateDeletionOwnerState, OWNER_A) is None

    newer = PrivateDeletionCommand(
        deletion_id=uuid4(),
        owner_user_id=OWNER_A,
        deletion_epoch=7,
        attempt_ids=frozenset(),
        request_deduplication_ids=frozenset(),
        private_reference_keys=frozenset({"concurrent-newer"}),
    )
    older = replace(
        newer,
        deletion_id=uuid4(),
        deletion_epoch=6,
        private_reference_keys=frozenset({"concurrent-older"}),
    )
    first_locked = Event()
    release_first = Event()
    second_insert_started = Event()
    backend_pids: dict[str, int] = {}
    results: dict[str, str] = {}
    errors: list[BaseException] = []

    def pause_first_after_owner_lock(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        normalized = statement.lower()
        if (
            current_thread().name == "private-deletion-newer"
            and "private_deletion_owner_states" in normalized
            and "for update" in normalized
        ):
            first_locked.set()
            if not release_first.wait(timeout=3):
                raise AssertionError("timed out while holding the owner state lock")

    def observe_second_owner_insert(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        if (
            current_thread().name == "private-deletion-older"
            and "insert into" in statement.lower()
            and "private_deletion_owner_states" in statement.lower()
        ):
            second_insert_started.set()

    def run_deletion(label: str, command: PrivateDeletionCommand) -> None:
        try:
            with session_factory.begin() as session:
                backend_pid = session.scalar(text("SELECT pg_backend_pid()"))
                assert backend_pid is not None
                backend_pids[label] = backend_pid
                outcome = apply_private_deletion(session, command)
            results[label] = outcome
        except BaseException as error:  # pragma: no cover - asserted below
            errors.append(error)

    first = Thread(
        target=run_deletion,
        args=("newer", newer),
        name="private-deletion-newer",
    )
    second = Thread(
        target=run_deletion,
        args=("older", older),
        name="private-deletion-older",
    )
    event.listen(database_engine, "after_cursor_execute", pause_first_after_owner_lock)
    event.listen(database_engine, "before_cursor_execute", observe_second_owner_insert)
    try:
        first.start()
        assert first_locked.wait(timeout=2)
        second.start()
        assert second_insert_started.wait(timeout=2)
        _wait_for_postgres_lock(database_engine, backend_pids["older"])
    finally:
        release_first.set()
        first.join(timeout=3)
        if second.ident is not None:
            second.join(timeout=3)
        event.remove(database_engine, "after_cursor_execute", pause_first_after_owner_lock)
        event.remove(database_engine, "before_cursor_execute", observe_second_owner_insert)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert results == {"newer": "APPLIED", "older": "STALE"}
    with session_factory() as session:
        owner_state = session.get(PrivateDeletionOwnerState, OWNER_A)
        assert owner_state is not None
        assert owner_state.latest_epoch == 7
        assert session.get(PrivateDeletionReceipt, newer.deletion_id) is not None
        assert session.get(PrivateDeletionReceipt, older.deletion_id) is None


@pytest.mark.approved_postgres
def test_private_deletion_receipt_rejects_rows_owned_by_another_owner(
    session_factory: sessionmaker[Session],
) -> None:
    state = _seed_shared_public_source(session_factory)
    command = PrivateDeletionCommand(
        deletion_id=uuid4(),
        owner_user_id=OWNER_A,
        deletion_epoch=7,
        attempt_ids=frozenset({state.attempt_b_project_1}),
        request_deduplication_ids=frozenset({state.dedup_b_project_1}),
        private_reference_keys=frozenset({"b-project-1"}),
    )

    with pytest.raises(
        PersistenceConflict, match="private deletion candidate belongs to another owner"
    ):
        with session_factory.begin() as session:
            apply_private_deletion(session, command)

    assert _attempt_ids_for_owner(session_factory, OWNER_B) == {state.attempt_b_project_1}
    assert _dedup_ids_for_owner(session_factory, OWNER_B) == {state.dedup_b_project_1}


@pytest.mark.approved_postgres
def test_existing_private_epoch_fence_and_public_boundary_are_enforced(
    session_factory: sessionmaker[Session],
) -> None:
    """GREEN control: missing T067 must not hide already-working W2 invariants."""

    state = _seed_shared_public_source(session_factory)

    assert _public_counts(session_factory) == (1, 1, 1, 1)
    _assert_shared_public_history_survives(session_factory, state)
    public_columns = {
        column
        for model in (Source, SourceVersion, Evidence, OutboxEvent)
        for column in model.__table__.columns.keys()
    }
    assert {"owner_user_id", "project_id", "result_payload"}.isdisjoint(public_columns)

    with session_factory() as session:
        current = assert_current_attempt(
            session,
            attempt_id=state.attempt_a_project_1,
            owner_user_id=OWNER_A,
            execution_fence="private-deletion-fence:a-project-1",
            owner_deletion_epoch=4,
        )
        assert current.result_payload == {
            "private_result_ref": "a-project-1",
            "source_id": str(state.source_id),
        }

        with pytest.raises(StaleExecution):
            assert_current_attempt(
                session,
                attempt_id=state.attempt_a_project_1,
                owner_user_id=OWNER_A,
                execution_fence="private-deletion-fence:a-project-1",
                owner_deletion_epoch=3,
            )


@pytest.mark.approved_postgres
def test_account_deletion_removes_only_a_private_rows_and_preserves_shared_public_source(
    session_factory: sessionmaker[Session],
) -> None:
    """EXPECTED RED until T067 consumes an account-scoped deletion marker."""

    state = _seed_shared_public_source(session_factory)
    side_effects = _PrivateDeletionSideEffects()
    deletion_id = uuid4()

    _process_private_deletion(
        session_factory=session_factory,
        owner_user_id=OWNER_A,
        deletion_id=deletion_id,
        deletion_epoch=5,
        attempt_ids=frozenset({state.attempt_a_project_1, state.attempt_a_project_2}),
        request_deduplication_ids=frozenset({state.dedup_a_project_1, state.dedup_a_project_2}),
        private_reference_keys=frozenset({"a-project-1", "a-project-2"}),
        side_effects=side_effects,
    )

    assert _attempt_ids_for_owner(session_factory, OWNER_A) == set()
    assert _dedup_ids_for_owner(session_factory, OWNER_A) == set()
    assert _attempt_ids_for_owner(session_factory, OWNER_B) == {state.attempt_b_project_1}
    assert _dedup_ids_for_owner(session_factory, OWNER_B) == {state.dedup_b_project_1}
    assert _public_counts(session_factory) == (1, 1, 1, 1)
    _assert_shared_public_history_survives(session_factory, state)
    assert side_effects.events == [
        ("purge", (OWNER_A, frozenset({"a-project-1", "a-project-2"}))),
        ("acknowledge", (deletion_id, 5)),
    ]


@pytest.mark.approved_postgres
def test_project_deletion_cleans_only_a_selected_project_scope(
    session_factory: sessionmaker[Session],
) -> None:
    """EXPECTED RED until T067 distinguishes account from Project deletion scope."""

    state = _seed_shared_public_source(session_factory)
    side_effects = _PrivateDeletionSideEffects()
    deletion_id = uuid4()

    _process_private_deletion(
        session_factory=session_factory,
        owner_user_id=OWNER_A,
        deletion_id=deletion_id,
        deletion_epoch=5,
        attempt_ids=frozenset({state.attempt_a_project_1}),
        request_deduplication_ids=frozenset({state.dedup_a_project_1}),
        private_reference_keys=frozenset({"a-project-1"}),
        side_effects=side_effects,
    )

    assert _attempt_ids_for_owner(session_factory, OWNER_A) == {state.attempt_a_project_2}
    assert _dedup_ids_for_owner(session_factory, OWNER_A) == {state.dedup_a_project_2}
    assert _private_result_reference(session_factory, state.attempt_a_project_2) == "a-project-2"
    assert _attempt_ids_for_owner(session_factory, OWNER_B) == {state.attempt_b_project_1}
    assert _dedup_ids_for_owner(session_factory, OWNER_B) == {state.dedup_b_project_1}
    assert _public_counts(session_factory) == (1, 1, 1, 1)
    _assert_shared_public_history_survives(session_factory, state)
    assert side_effects.events == [
        ("purge", (OWNER_A, frozenset({"a-project-1"}))),
        ("acknowledge", (deletion_id, 5)),
    ]


@pytest.mark.approved_postgres
def test_private_deletion_does_not_acknowledge_when_private_purge_fails(
    session_factory: sessionmaker[Session],
) -> None:
    """EXPECTED RED until T067 wires the W1 private side-effect port before ACK."""

    state = _seed_shared_public_source(session_factory)
    side_effects = _PrivateDeletionSideEffects(fail_purge=True)
    deletion_id = uuid4()

    with pytest.raises(RuntimeError, match="synthetic private purge failure"):
        _process_private_deletion(
            session_factory=session_factory,
            owner_user_id=OWNER_A,
            deletion_id=deletion_id,
            deletion_epoch=5,
            attempt_ids=frozenset({state.attempt_a_project_1}),
            request_deduplication_ids=frozenset({state.dedup_a_project_1}),
            private_reference_keys=frozenset({"a-project-1"}),
            side_effects=side_effects,
        )

    assert side_effects.events == [("purge", (OWNER_A, frozenset({"a-project-1"})))]
    assert _attempt_ids_for_owner(session_factory, OWNER_B) == {state.attempt_b_project_1}
    assert _dedup_ids_for_owner(session_factory, OWNER_B) == {state.dedup_b_project_1}
    _assert_shared_public_history_survives(session_factory, state)


@pytest.mark.approved_postgres
def test_pre_deletion_worker_command_replay_cannot_recreate_private_result(
    session_factory: sessionmaker[Session],
) -> None:
    """EXPECTED RED until T067 connects deletion epoch to the actual worker replay path."""

    state = _seed_shared_public_source(session_factory)
    with session_factory.begin() as session:
        owner_state = session.get(PrivateDeletionOwnerState, OWNER_A)
        if owner_state is None:
            session.add(
                PrivateDeletionOwnerState(
                    owner_user_id=OWNER_A,
                    latest_epoch=4,
                    account_deleted=False,
                )
            )
        else:
            owner_state.latest_epoch = 4
    with session_factory() as session:
        source = session.get(Source, state.source_id)
        policy = session.scalar(
            select(SourcePolicyDecision).where(
                SourcePolicyDecision.source_id == state.source_id,
                SourcePolicyDecision.revision == 1,
            )
        )
        assert source is not None
        assert policy is not None

    command = _collection_command(source).model_copy(
        update={
            "authenticated_owner_ref": OWNER_A,
            "project_ref": str(PROJECT_A_1),
            "owner_deletion_epoch": 4,
            "execution_fence": "private-deletion-worker-replay",
        }
    )
    permit = _permit(command)
    prepared = _complete_prepared(
        command,
        source,
        policy,
        attempt_id=permit.attempt_id,
        aggregate_revision=2,
        content_hash="c" * 64,
    )
    worker_events: list[str] = []
    control = _Control(permit, worker_events)

    def run(context):
        context.enter_stage(CollectionStage.PARSE, policy_revision=context.command.policy_revision)
        return prepared

    execution = _Execution(worker_events, run)
    deletion_epoch_locker = _DeletionEpochLocker({OWNER_A: 4})
    worker = SourceCollectionWorker(
        control=control,
        execution_factory=_Factory(worker_events, execution),
        session_factory=session_factory,
        lock_authority=deletion_epoch_locker,
        committer=_commit_worker_result,
        replayer=_replay_worker_result,
        clock=lambda: NOW,
    )

    assert worker.handle(command.model_dump(mode="json")) == prepared.result
    assert _attempt_ids_for_owner(session_factory, OWNER_A) == {
        state.attempt_a_project_1,
        state.attempt_a_project_2,
        permit.attempt_id,
    }

    deletion_epoch_locker.latest_epoch_by_owner[OWNER_A] = 5
    side_effects = _PrivateDeletionSideEffects()
    deletion_id = uuid4()
    _process_private_deletion(
        session_factory=session_factory,
        owner_user_id=OWNER_A,
        deletion_id=deletion_id,
        deletion_epoch=5,
        attempt_ids=frozenset({state.attempt_a_project_1, permit.attempt_id}),
        request_deduplication_ids=frozenset({state.dedup_a_project_1}),
        private_reference_keys=frozenset({"a-project-1", "worker-replay"}),
        side_effects=side_effects,
    )

    with pytest.raises(
        (StaleExecution, PrivateScopeRejected),
        match="deletion marker has advanced|current epoch",
    ):
        worker.handle(command.model_dump(mode="json"))

    assert _attempt_ids_for_owner(session_factory, OWNER_A) == {state.attempt_a_project_2}
    assert _dedup_ids_for_owner(session_factory, OWNER_A) == {state.dedup_a_project_2}
    assert _private_result_reference(session_factory, state.attempt_a_project_2) == "a-project-2"
    _assert_shared_public_history_survives(session_factory, state)
    assert side_effects.events == [
        ("purge", (OWNER_A, frozenset({"a-project-1", "worker-replay"}))),
        ("acknowledge", (deletion_id, 5)),
    ]


@pytest.mark.approved_postgres
def test_deletion_epoch_wins_over_barriered_late_commit_and_same_command_replay(
    session_factory: sessionmaker[Session],
) -> None:
    """EXPECTED RED until T067 fences a late result before its private commit."""

    state = _seed_shared_public_source(session_factory)
    side_effects = _PrivateDeletionSideEffects()
    deletion_id = uuid4()
    late_commit_has_lock = Event()
    release_late_commit = Event()
    late_commit_finished = Event()
    late_commit_errors: list[BaseException] = []
    deletion_errors: list[BaseException] = []

    def commit_same_command_late() -> None:
        try:
            with session_factory() as session:
                replay = assert_current_attempt(
                    session,
                    attempt_id=state.attempt_a_project_1,
                    owner_user_id=OWNER_A,
                    execution_fence="private-deletion-fence:a-project-1",
                    owner_deletion_epoch=4,
                )
                late_commit_has_lock.set()
                assert release_late_commit.wait(timeout=5)
                replay.result_payload = {
                    "private_result_ref": "a-project-1-late-replay",
                    "source_id": str(state.source_id),
                }
                session.commit()
        except BaseException as error:  # propagated in the test thread
            late_commit_errors.append(error)
        finally:
            late_commit_finished.set()

    def consume_deletion() -> None:
        try:
            _process_private_deletion(
                session_factory=session_factory,
                owner_user_id=OWNER_A,
                deletion_id=deletion_id,
                deletion_epoch=5,
                attempt_ids=frozenset({state.attempt_a_project_1}),
                request_deduplication_ids=frozenset({state.dedup_a_project_1}),
                private_reference_keys=frozenset({"a-project-1"}),
                side_effects=side_effects,
            )
        except BaseException as error:  # propagated in the test thread
            deletion_errors.append(error)

    late_commit_thread = Thread(target=commit_same_command_late)
    deletion_thread = Thread(target=consume_deletion)
    deletion_started = False
    late_commit_thread.start()
    try:
        assert late_commit_has_lock.wait(timeout=5)
        deletion_thread.start()
        deletion_started = True
        release_late_commit.set()
        assert late_commit_finished.wait(timeout=5)
    finally:
        release_late_commit.set()
        late_commit_thread.join(timeout=5)
        if deletion_started:
            deletion_thread.join(timeout=5)

    if late_commit_errors:
        raise late_commit_errors[0]
    if deletion_errors:
        raise deletion_errors[0]
    assert not late_commit_thread.is_alive()
    assert not deletion_thread.is_alive()
    assert _attempt_ids_for_owner(session_factory, OWNER_A) == {state.attempt_a_project_2}
    assert _dedup_ids_for_owner(session_factory, OWNER_A) == {state.dedup_a_project_2}
    assert _public_counts(session_factory) == (1, 1, 1, 1)
    _assert_shared_public_history_survives(session_factory, state)
    assert side_effects.events == [
        ("purge", (OWNER_A, frozenset({"a-project-1"}))),
        ("acknowledge", (deletion_id, 5)),
    ]

    # A duplicate, late delivery of the same deletion command must be harmless and
    # must not make the old private result visible again.
    _process_private_deletion(
        session_factory=session_factory,
        owner_user_id=OWNER_A,
        deletion_id=deletion_id,
        deletion_epoch=5,
        attempt_ids=frozenset({state.attempt_a_project_1}),
        request_deduplication_ids=frozenset({state.dedup_a_project_1}),
        private_reference_keys=frozenset({"a-project-1"}),
        side_effects=side_effects,
    )

    assert _attempt_ids_for_owner(session_factory, OWNER_A) == {state.attempt_a_project_2}
    assert _dedup_ids_for_owner(session_factory, OWNER_A) == {state.dedup_a_project_2}
    assert _public_counts(session_factory) == (1, 1, 1, 1)
    assert side_effects.events == [
        ("purge", (OWNER_A, frozenset({"a-project-1"}))),
        ("acknowledge", (deletion_id, 5)),
        ("purge", (OWNER_A, frozenset({"a-project-1"}))),
        ("acknowledge", (deletion_id, 5)),
    ]


@pytest.mark.approved_postgres
def test_duplicate_deletion_delivery_is_idempotent_and_cannot_regress_epoch(
    session_factory: sessionmaker[Session],
) -> None:
    """EXPECTED RED until T067 persists monotonic deletion epoch acknowledgement."""

    state = _seed_shared_public_source(session_factory)
    side_effects = _PrivateDeletionSideEffects()
    current_deletion_id = uuid4()
    stale_deletion_id = uuid4()

    _process_private_deletion(
        session_factory=session_factory,
        owner_user_id=OWNER_A,
        deletion_id=current_deletion_id,
        deletion_epoch=7,
        attempt_ids=frozenset({state.attempt_a_project_1}),
        request_deduplication_ids=frozenset({state.dedup_a_project_1}),
        private_reference_keys=frozenset({"a-project-1"}),
        side_effects=side_effects,
    )
    _process_private_deletion(
        session_factory=session_factory,
        owner_user_id=OWNER_A,
        deletion_id=stale_deletion_id,
        deletion_epoch=6,
        attempt_ids=frozenset({state.attempt_a_project_2}),
        request_deduplication_ids=frozenset({state.dedup_a_project_2}),
        private_reference_keys=frozenset({"a-project-2"}),
        side_effects=side_effects,
    )

    assert _attempt_ids_for_owner(session_factory, OWNER_A) == {state.attempt_a_project_2}
    assert _dedup_ids_for_owner(session_factory, OWNER_A) == {state.dedup_a_project_2}
    assert _attempt_ids_for_owner(session_factory, OWNER_B) == {state.attempt_b_project_1}
    assert _dedup_ids_for_owner(session_factory, OWNER_B) == {state.dedup_b_project_1}
    assert _private_result_reference(session_factory, state.attempt_a_project_2) == "a-project-2"
    assert _public_counts(session_factory) == (1, 1, 1, 1)
    _assert_shared_public_history_survives(session_factory, state)
    assert side_effects.events == [
        ("purge", (OWNER_A, frozenset({"a-project-1"}))),
        ("acknowledge", (current_deletion_id, 7)),
    ]


@pytest.mark.approved_postgres
def test_stale_epoch_skips_purge_and_ack(
    session_factory: sessionmaker[Session],
) -> None:
    state = _seed_shared_public_source(session_factory)
    side_effects = _PrivateDeletionSideEffects()
    first_id = uuid4()

    _process_private_deletion(
        session_factory=session_factory,
        owner_user_id=OWNER_A,
        deletion_id=first_id,
        deletion_epoch=7,
        attempt_ids=frozenset({state.attempt_a_project_1}),
        request_deduplication_ids=frozenset({state.dedup_a_project_1}),
        private_reference_keys=frozenset({"a-project-1"}),
        side_effects=side_effects,
    )
    _process_private_deletion(
        session_factory=session_factory,
        owner_user_id=OWNER_A,
        deletion_id=uuid4(),
        deletion_epoch=6,
        attempt_ids=frozenset({state.attempt_a_project_2}),
        request_deduplication_ids=frozenset({state.dedup_a_project_2}),
        private_reference_keys=frozenset({"a-project-2"}),
        side_effects=side_effects,
    )

    assert side_effects.events == [
        ("purge", (OWNER_A, frozenset({"a-project-1"}))),
        ("acknowledge", (first_id, 7)),
    ]


@pytest.mark.approved_postgres
def test_replaying_old_receipt_after_newer_deletion_is_stale_without_side_effects(
    session_factory: sessionmaker[Session],
) -> None:
    state = _seed_shared_public_source(session_factory)
    side_effects = _PrivateDeletionSideEffects()
    old_deletion_id = uuid4()
    current_deletion_id = uuid4()

    old_ack = _process_private_deletion(
        session_factory=session_factory,
        owner_user_id=OWNER_A,
        deletion_id=old_deletion_id,
        deletion_epoch=5,
        attempt_ids=frozenset({state.attempt_a_project_1}),
        request_deduplication_ids=frozenset({state.dedup_a_project_1}),
        private_reference_keys=frozenset({"a-project-1"}),
        side_effects=side_effects,
    )
    current_ack = _process_private_deletion(
        session_factory=session_factory,
        owner_user_id=OWNER_A,
        deletion_id=current_deletion_id,
        deletion_epoch=7,
        attempt_ids=frozenset({state.attempt_a_project_2}),
        request_deduplication_ids=frozenset({state.dedup_a_project_2}),
        private_reference_keys=frozenset({"a-project-2"}),
        side_effects=side_effects,
    )
    replay_ack = _process_private_deletion(
        session_factory=session_factory,
        owner_user_id=OWNER_A,
        deletion_id=old_deletion_id,
        deletion_epoch=5,
        attempt_ids=frozenset({state.attempt_a_project_1}),
        request_deduplication_ids=frozenset({state.dedup_a_project_1}),
        private_reference_keys=frozenset({"a-project-1"}),
        side_effects=side_effects,
    )

    assert old_ack.outcome == "APPLIED"
    assert current_ack.outcome == "APPLIED"
    assert replay_ack.outcome == "STALE"
    assert _attempt_ids_for_owner(session_factory, OWNER_A) == set()
    assert _dedup_ids_for_owner(session_factory, OWNER_A) == set()
    assert _attempt_ids_for_owner(session_factory, OWNER_B) == {state.attempt_b_project_1}
    assert _dedup_ids_for_owner(session_factory, OWNER_B) == {state.dedup_b_project_1}
    with session_factory() as session:
        owner_state = session.get(PrivateDeletionOwnerState, OWNER_A)
        assert owner_state is not None
        assert owner_state.latest_epoch == 7
    assert side_effects.events == [
        ("purge", (OWNER_A, frozenset({"a-project-1"}))),
        ("acknowledge", (old_deletion_id, 5)),
        ("purge", (OWNER_A, frozenset({"a-project-2"}))),
        ("acknowledge", (current_deletion_id, 7)),
    ]


@pytest.mark.approved_postgres
def test_foreign_attempt_identifier_rolls_back_without_side_effects(
    session_factory: sessionmaker[Session],
) -> None:
    state = _seed_shared_public_source(session_factory)
    side_effects = _PrivateDeletionSideEffects()

    with pytest.raises(
        PersistenceConflict, match="private deletion candidate belongs to another owner"
    ):
        _process_private_deletion(
            session_factory=session_factory,
            owner_user_id=OWNER_A,
            deletion_id=uuid4(),
            deletion_epoch=7,
            attempt_ids=frozenset({state.attempt_b_project_1}),
            request_deduplication_ids=frozenset(),
            private_reference_keys=frozenset({"b-project-1"}),
            side_effects=side_effects,
        )

    assert side_effects.events == []
