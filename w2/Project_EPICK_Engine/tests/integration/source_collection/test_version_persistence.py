"""PostgreSQL regression coverage for immutable version persistence queries."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from tests.contract.source_collection.test_posting_handoff import _posting_envelope
from tests.integration.source_collection.test_atomic_persistence import (
    _command,
    _complete_prepared,
    _counts,
    _failure_prepared,
    _locker,
    _policy_snapshot,
    _seed_source,
    commit_prepared_collection,
)

from epick_engine.source_collection.contracts import SourceEvent
from epick_engine.source_collection.persistence import (
    Base,
    ExtractionRevision,
    InvalidPreparedCollection,
    OutboxEvent,
    ParserExecution,
    PreparedParserExecution,
    get_current_source_version,
    get_extraction_revision,
    get_latest_source_observation,
    get_source_version,
    list_parser_executions,
    list_revision_evidence,
    list_source_versions,
)

pytestmark = pytest.mark.approved_postgres

_VERSION_A = UUID("00000000-0000-4000-8000-000000000101")
_VERSION_B = UUID("00000000-0000-4000-8000-000000000102")
_EVIDENCE_A = UUID("00000000-0000-4000-8000-000000000201")
_EVIDENCE_A_SECOND = UUID("00000000-0000-4000-8000-000000000202")
_EVIDENCE_B = UUID("00000000-0000-4000-8000-000000000203")
_REVISION_A = UUID("00000000-0000-4000-8000-000000000301")
_REVISION_B = UUID("00000000-0000-4000-8000-000000000302")
_EXECUTION_A = UUID("00000000-0000-4000-8000-000000000401")
_EXECUTION_B = UUID("00000000-0000-4000-8000-000000000402")
_EXECUTION_A_RERUN = UUID("00000000-0000-4000-8000-000000000403")
_EXECUTION_FAILURE = UUID("00000000-0000-4000-8000-000000000404")


@pytest.fixture
def database_engine(approved_postgres_url) -> Iterator[Engine]:
    """Give version-persistence coverage a real, isolated PostgreSQL schema."""

    admin_engine = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema = f"epick_version_persistence_{uuid4().hex}"
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


def _source_envelope_event(
    command,
    source,
    *,
    version,
    revision,
    aggregate_revision: int,
) -> SourceEvent:
    envelope = _posting_envelope(command)
    envelope = envelope.model_copy(
        update={
            "source_id": source.source_id,
            "source_version_id": version.source_version_id,
            "extraction_revision_id": revision.extraction_revision_id,
            "company_id": source.company_id,
            "url_or_path": source.canonical_url,
            "content_hash": version.content_hash,
            "hash_profile_version": version.hash_profile_version,
            "parser_version": revision.parser_version,
            "evidence_spans": [
                span.model_copy(update={"source_version_id": version.source_version_id})
                for span in envelope.evidence_spans
            ],
        }
    )
    return SourceEvent(
        event_id=uuid4(),
        event_type="source.version.available",
        schema_version="w2.source.v1",
        aggregate_id=command.source_id,
        aggregate_revision=aggregate_revision,
        occurred_at=revision.created_at,
        payload=envelope,
    )


def test_source_envelope_rejects_stale_same_source_references_before_writes(
    session_factory: sessionmaker[Session],
) -> None:
    _, source, policy = _seed_source(session_factory)
    initial_command = _command(source)
    initial = _complete_prepared(initial_command, source, policy, aggregate_revision=1)
    commit_prepared_collection(
        session_factory,
        command=initial_command,
        prepared=initial,
        lock_authority=_locker(initial_command, pointer_eligible=True),
    )

    current_command = _command(source)
    current = _complete_prepared(
        current_command,
        source,
        policy,
        aggregate_revision=2,
        content_hash="b" * 64,
    )
    stale_event = _source_envelope_event(
        current_command,
        source,
        version=initial.source_version,
        revision=initial.extraction_revision,
        aggregate_revision=2,
    )
    invalid = replace(current, events=(stale_event,))

    with session_factory() as session:
        before = _counts(session)
    with pytest.raises(InvalidPreparedCollection, match="source envelope"):
        commit_prepared_collection(
            session_factory,
            command=current_command,
            prepared=invalid,
            lock_authority=_locker(current_command, pointer_eligible=True),
        )
    with session_factory() as session:
        assert _counts(session) == before


def test_source_envelope_rejects_foreign_version_revision_pair_before_writes(
    session_factory: sessionmaker[Session],
) -> None:
    _, source, policy = _seed_source(session_factory)
    _, other_source, other_policy = _seed_source(session_factory)
    other_command = _command(other_source)
    other = _complete_prepared(other_command, other_source, other_policy, aggregate_revision=1)
    commit_prepared_collection(
        session_factory,
        command=other_command,
        prepared=other,
        lock_authority=_locker(other_command, pointer_eligible=True),
    )

    current_command = _command(source)
    current = _complete_prepared(current_command, source, policy, aggregate_revision=1)
    foreign_event = _source_envelope_event(
        current_command,
        source,
        version=other.source_version,
        revision=other.extraction_revision,
        aggregate_revision=1,
    )
    invalid = replace(current, events=(foreign_event,))

    with session_factory() as session:
        before = _counts(session)
    with pytest.raises(InvalidPreparedCollection, match="source envelope"):
        commit_prepared_collection(
            session_factory,
            command=current_command,
            prepared=invalid,
            lock_authority=_locker(current_command, pointer_eligible=True),
        )
    with session_factory() as session:
        assert _counts(session) == before


def test_version_queries_keep_history_scoped_and_stably_ordered(
    session_factory: sessionmaker[Session],
) -> None:
    _, source, policy = _seed_source(session_factory)

    initial_command = _command(source)
    initial = _complete_prepared(
        initial_command,
        source,
        policy,
        aggregate_revision=1,
        source_version_id=_VERSION_A,
        evidence_id=_EVIDENCE_A,
        extraction_revision_id=_REVISION_A,
        parser_execution_id=_EXECUTION_A,
    )
    second_evidence = replace(
        initial.evidence[0],
        evidence_id=_EVIDENCE_A_SECOND,
        evidence_key="required:1",
        text_excerpt="Distributed systems experience",
    )
    initial = replace(
        initial,
        evidence=(*initial.evidence, second_evidence),
        extraction_revision=replace(
            initial.extraction_revision,
            evidence_ids=(_EVIDENCE_A, _EVIDENCE_A_SECOND),
        ),
    )
    commit_prepared_collection(
        session_factory,
        command=initial_command,
        prepared=initial,
        lock_authority=_locker(initial_command, pointer_eligible=True),
    )
    duplicate_result = commit_prepared_collection(
        session_factory,
        command=initial_command,
        prepared=initial,
        lock_authority=_locker(initial_command, pointer_eligible=True),
    )

    changed_command = _command(source)
    changed = _complete_prepared(
        changed_command,
        source,
        policy,
        aggregate_revision=2,
        source_version_id=_VERSION_B,
        evidence_id=_EVIDENCE_B,
        extraction_revision_id=_REVISION_B,
        parser_execution_id=_EXECUTION_B,
        content_hash="b" * 64,
    )
    commit_prepared_collection(
        session_factory,
        command=changed_command,
        prepared=changed,
        lock_authority=_locker(changed_command, pointer_eligible=True),
    )

    rerun_command = _command(source)
    rerun = _complete_prepared(
        rerun_command,
        source,
        policy,
        aggregate_revision=3,
        source_version_id=_VERSION_A,
        evidence_id=_EVIDENCE_A,
        extraction_revision_id=_REVISION_A,
        parser_execution_id=_EXECUTION_A_RERUN,
        content_hash="a" * 64,
    )
    rerun = replace(
        rerun,
        evidence=initial.evidence,
        extraction_revision=replace(
            rerun.extraction_revision,
            evidence_ids=initial.extraction_revision.evidence_ids,
        ),
    )
    commit_prepared_collection(
        session_factory,
        command=rerun_command,
        prepared=rerun,
        lock_authority=_locker(rerun_command, pointer_eligible=True),
    )

    failed_command = _command(source)
    failed = _failure_prepared(failed_command, source, policy, aggregate_revision=4)
    failed = replace(
        failed,
        source_version=initial.source_version,
        parser_execution=PreparedParserExecution(
            parser_execution_id=_EXECUTION_FAILURE,
            source_id=source.source_id,
            source_version_id=_VERSION_A,
            content_hash=initial.source_version.content_hash,
            parser_version="atomic-parser-v2",
            output_hash=None,
            extraction_revision_id=None,
            status="failed",
            executed_at=initial.parser_execution.executed_at,
        ),
    )
    commit_prepared_collection(
        session_factory,
        command=failed_command,
        prepared=failed,
        lock_authority=_locker(failed_command, pointer_eligible=True),
    )

    with session_factory() as session:
        current_version = get_current_source_version(session, source_id=source.source_id)
        latest_observation = get_latest_source_observation(session, source_id=source.source_id)
        versions = list_source_versions(session, source_id=source.source_id, limit=10)
        version_after_first_page = list_source_versions(
            session,
            source_id=source.source_id,
            limit=10,
            before=(versions[0].collected_at, versions[0].source_version_id),
        )
        evidence = list_revision_evidence(
            session,
            source_version_id=_VERSION_A,
            extraction_revision_id=_REVISION_A,
            limit=10,
        )
        evidence_after_first = list_revision_evidence(
            session,
            source_version_id=_VERSION_A,
            extraction_revision_id=_REVISION_A,
            limit=10,
            after=(0, _EVIDENCE_A),
        )
        executions = list_parser_executions(session, source_id=source.source_id, limit=10)
        executions_after_rerun = list_parser_executions(
            session,
            source_id=source.source_id,
            limit=10,
            before=(executions[1].executed_at, executions[1].parser_execution_id),
        )
        version_a_executions = list_parser_executions(
            session,
            source_id=source.source_id,
            source_version_id=_VERSION_A,
            limit=10,
        )

        assert duplicate_result == initial.result
        assert current_version is not None
        assert latest_observation is not None
        assert evidence is not None
        assert evidence_after_first is not None
        assert (
            current_version.source_version_id,
            latest_observation.observation_id,
            [version.source_version_id for version in versions],
            [version.source_version_id for version in version_after_first_page],
            [item.evidence_id for item in evidence],
            [item.evidence_id for item in evidence_after_first],
            [execution.parser_execution_id for execution in executions],
            [execution.parser_execution_id for execution in executions_after_rerun],
            [execution.parser_execution_id for execution in version_a_executions],
            [
                (execution.status, execution.output_hash, execution.extraction_revision_id)
                for execution in executions
            ],
        ) == (
            _VERSION_A,
            failed.observation.snapshot.observation_id,
            [_VERSION_B, _VERSION_A],
            [_VERSION_A],
            [_EVIDENCE_A, _EVIDENCE_A_SECOND],
            [_EVIDENCE_A_SECOND],
            [_EXECUTION_FAILURE, _EXECUTION_A_RERUN, _EXECUTION_B, _EXECUTION_A],
            [_EXECUTION_B, _EXECUTION_A],
            [_EXECUTION_FAILURE, _EXECUTION_A_RERUN, _EXECUTION_A],
            [
                ("failed", None, None),
                ("succeeded", "a" * 64, _REVISION_A),
                ("succeeded", "b" * 64, _REVISION_B),
                ("succeeded", "a" * 64, _REVISION_A),
            ],
        )
        assert (
            get_source_version(
                session,
                source_id=source.source_id,
                source_version_id=_VERSION_A,
            ).source_version_id,
            get_source_version(
                session,
                source_id=uuid4(),
                source_version_id=_VERSION_A,
            ),
            get_extraction_revision(
                session,
                source_version_id=_VERSION_A,
                extraction_revision_id=_REVISION_A,
            ).extraction_revision_id,
            get_extraction_revision(
                session,
                source_version_id=_VERSION_B,
                extraction_revision_id=_REVISION_A,
            ),
            list_revision_evidence(
                session,
                source_version_id=_VERSION_B,
                extraction_revision_id=_REVISION_A,
                limit=10,
            ),
        ) == (_VERSION_A, None, _REVISION_A, None, None)
        with pytest.raises(ValueError, match="limit must be positive"):
            list_source_versions(session, source_id=source.source_id, limit=0)


def test_reused_revision_event_uses_the_revision_creator_parser_version(
    session_factory: sessionmaker[Session],
) -> None:
    _, source, policy = _seed_source(session_factory)

    initial_command = _command(source)
    initial = _complete_prepared(initial_command, source, policy, aggregate_revision=1)
    commit_prepared_collection(
        session_factory,
        command=initial_command,
        prepared=initial,
        lock_authority=_locker(initial_command, pointer_eligible=True),
    )

    rerun_command = _command(source)
    rerun = _complete_prepared(
        rerun_command,
        source,
        policy,
        aggregate_revision=2,
        source_version_id=initial.source_version.source_version_id,
        evidence_id=initial.evidence[0].evidence_id,
        parser_execution_id=uuid4(),
        content_hash=initial.source_version.content_hash,
    )
    rerun = replace(
        rerun,
        extraction_revision=replace(
            rerun.extraction_revision,
            parser_version="atomic-parser-v2",
        ),
        parser_execution=replace(
            rerun.parser_execution,
            parser_version="atomic-parser-v2",
        ),
    )
    envelope = _posting_envelope(rerun_command)
    envelope = envelope.model_copy(
        update={
            "source_id": source.source_id,
            "source_version_id": rerun.source_version.source_version_id,
            "extraction_revision_id": rerun.extraction_revision.extraction_revision_id,
            "company_id": source.company_id,
            "url_or_path": source.canonical_url,
            "content_hash": rerun.source_version.content_hash,
            "hash_profile_version": rerun.source_version.hash_profile_version,
            "parser_version": "atomic-parser-v2",
            "policy": _policy_snapshot(policy),
            "evidence_spans": [
                span.model_copy(
                    update={"source_version_id": rerun.source_version.source_version_id}
                )
                for span in envelope.evidence_spans
            ],
        }
    )
    event = SourceEvent(
        event_id=uuid4(),
        event_type="source.version.available",
        schema_version="w2.source.v1",
        aggregate_id=source.source_id,
        aggregate_revision=2,
        occurred_at=rerun.finalized_at,
        payload=envelope,
    )
    rerun = replace(rerun, events=(event,))
    returned = commit_prepared_collection(
        session_factory,
        command=rerun_command,
        prepared=rerun,
        lock_authority=_locker(rerun_command, pointer_eligible=True),
    )

    with session_factory() as session:
        revision = session.get(
            ExtractionRevision,
            initial.extraction_revision.extraction_revision_id,
        )
        rerun_execution = session.get(
            ParserExecution,
            rerun.parser_execution.parser_execution_id,
        )
        persisted_event = session.get(OutboxEvent, event.event_id)

        assert (
            returned.successful_source_refs[0].extraction_revision_id,
            rerun_execution.parser_version if rerun_execution is not None else None,
            (rerun_execution.extraction_revision_id if rerun_execution is not None else None),
            (
                persisted_event.payload["extraction_revision_id"]
                if persisted_event is not None
                else None
            ),
            persisted_event.payload["parser_version"] if persisted_event is not None else None,
            revision.parser_version if revision is not None else None,
        ) == (
            initial.extraction_revision.extraction_revision_id,
            "atomic-parser-v2",
            initial.extraction_revision.extraction_revision_id,
            str(initial.extraction_revision.extraction_revision_id),
            "atomic-parser-v1",
            "atomic-parser-v1",
        )
