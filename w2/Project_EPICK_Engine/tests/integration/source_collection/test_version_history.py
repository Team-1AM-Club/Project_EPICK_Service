from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from tests.contract.source_collection.test_posting_handoff import _posting_envelope
from tests.integration.source_collection.test_atomic_persistence import (
    _command,
    _complete_prepared,
    _failure_prepared,
    _locker,
    _policy_snapshot,
    _seed_source,
    commit_prepared_collection,
)
from tests.unit.source_collection.test_service_pipeline import (
    _candidate as _pipeline_candidate,
)
from tests.unit.source_collection.test_service_pipeline import (
    _Collector as _PipelineCollector,
)
from tests.unit.source_collection.test_service_pipeline import (
    _Context as _PipelineContext,
)
from tests.unit.source_collection.test_service_pipeline import (
    _fetch_result as _pipeline_fetch_result,
)
from tests.unit.source_collection.test_service_pipeline import (
    _input as _pipeline_input,
)
from tests.unit.source_collection.test_service_pipeline import (
    _InputProvider as _PipelineInputProvider,
)

import epick_engine.source_collection.parsing as parsing_module
from epick_engine.source_collection.contracts import (
    Locator,
    LocatorKind,
    PostingSectionKind,
    SourceEvent,
)
from epick_engine.source_collection.parsing import EvidenceDraft
from epick_engine.source_collection.persistence import (
    Base,
    Evidence,
    ExtractionRevision,
    OutboxEvent,
    ParserExecution,
    PreparedParserExecution,
    Source,
    SourceObservation,
    SourceVersion,
)
from epick_engine.source_collection.service import StaticCollectionExecution

pytestmark = pytest.mark.approved_postgres


@pytest.fixture
def database_engine(approved_postgres_url) -> Iterator[Engine]:
    """Give version-history coverage a real, isolated PostgreSQL schema."""
    admin_engine = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema = f"epick_version_history_{uuid4().hex}"
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


def test_concurrent_a_a_b_a_keeps_two_logical_versions_and_returns_to_a(
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

    repeated_command = _command(source)
    repeated = _complete_prepared(
        repeated_command,
        source,
        policy,
        aggregate_revision=2,
        source_version_id=initial.source_version.source_version_id,
        evidence_id=initial.evidence[0].evidence_id,
        extraction_revision_id=initial.extraction_revision.extraction_revision_id,
        content_hash=initial.source_version.content_hash,
    )
    changed_command = _command(source)
    changed = _complete_prepared(
        changed_command,
        source,
        policy,
        aggregate_revision=3,
        content_hash="b" * 64,
    )
    start = Barrier(3)

    def commit_after_start(command, prepared):
        start.wait(timeout=10)
        return commit_prepared_collection(
            session_factory,
            command=command,
            prepared=prepared,
            lock_authority=_locker(command, pointer_eligible=True),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        repeated_future = executor.submit(commit_after_start, repeated_command, repeated)
        changed_future = executor.submit(commit_after_start, changed_command, changed)
        start.wait(timeout=10)
        repeated_result = repeated_future.result(timeout=15)
        changed_result = changed_future.result(timeout=15)

    returned_command = _command(source)
    returned = _complete_prepared(
        returned_command,
        source,
        policy,
        aggregate_revision=4,
        source_version_id=initial.source_version.source_version_id,
        evidence_id=initial.evidence[0].evidence_id,
        extraction_revision_id=initial.extraction_revision.extraction_revision_id,
        content_hash=initial.source_version.content_hash,
    )
    returned_result = commit_prepared_collection(
        session_factory,
        command=returned_command,
        prepared=returned,
        lock_authority=_locker(returned_command, pointer_eligible=True),
    )

    with session_factory() as session:
        persisted_source = session.get(Source, source.source_id)
        versions = session.scalars(
            select(SourceVersion).where(SourceVersion.source_id == source.source_id)
        ).all()
        executions = session.scalars(
            select(ParserExecution).where(ParserExecution.source_id == source.source_id)
        ).all()
        observations = session.scalars(
            select(SourceObservation).where(SourceObservation.source_id == source.source_id)
        ).all()
        observations_by_id = {
            observation.observation_id: observation for observation in observations
        }

        assert (
            repeated_result.successful_source_refs[0].source_version_id,
            changed_result.successful_source_refs[0].source_version_id,
            returned_result.successful_source_refs[0].source_version_id,
            frozenset(observations_by_id),
            tuple(
                observations_by_id[observation_id].source_version_id
                for observation_id in (
                    initial.observation.snapshot.observation_id,
                    repeated.observation.snapshot.observation_id,
                    changed.observation.snapshot.observation_id,
                    returned.observation.snapshot.observation_id,
                )
            ),
            persisted_source.latest_observation_id if persisted_source is not None else None,
            persisted_source.current_source_version_id if persisted_source is not None else None,
            frozenset(version.content_hash for version in versions),
            len(executions),
        ) == (
            initial.source_version.source_version_id,
            changed.source_version.source_version_id,
            initial.source_version.source_version_id,
            frozenset(
                {
                    initial.observation.snapshot.observation_id,
                    repeated.observation.snapshot.observation_id,
                    changed.observation.snapshot.observation_id,
                    returned.observation.snapshot.observation_id,
                }
            ),
            (
                initial.source_version.source_version_id,
                initial.source_version.source_version_id,
                changed.source_version.source_version_id,
                initial.source_version.source_version_id,
            ),
            returned.observation.snapshot.observation_id,
            initial.source_version.source_version_id,
            frozenset({"a" * 64, "b" * 64}),
            4,
        )


def test_late_attempt_cannot_replace_a_newer_current_pointer(
    session_factory: sessionmaker[Session],
) -> None:
    _, source, policy = _seed_source(session_factory)

    first_command = _command(source)
    first = _complete_prepared(first_command, source, policy, aggregate_revision=1)
    commit_prepared_collection(
        session_factory,
        command=first_command,
        prepared=first,
        lock_authority=_locker(first_command, pointer_eligible=True),
    )

    current_command = _command(source)
    current = _complete_prepared(
        current_command,
        source,
        policy,
        aggregate_revision=2,
        content_hash="b" * 64,
    )
    commit_prepared_collection(
        session_factory,
        command=current_command,
        prepared=current,
        lock_authority=_locker(current_command, pointer_eligible=True),
    )

    late_command = _command(source)
    late = _complete_prepared(
        late_command,
        source,
        policy,
        aggregate_revision=3,
        source_version_id=first.source_version.source_version_id,
        evidence_id=first.evidence[0].evidence_id,
        extraction_revision_id=first.extraction_revision.extraction_revision_id,
        content_hash=first.source_version.content_hash,
    )
    commit_prepared_collection(
        session_factory,
        command=late_command,
        prepared=late,
        lock_authority=_locker(late_command, pointer_eligible=False),
    )

    with session_factory() as session:
        persisted_source = session.get(Source, source.source_id)
        executions = session.scalars(
            select(ParserExecution).where(ParserExecution.source_id == source.source_id)
        ).all()

        assert (
            persisted_source.current_source_version_id if persisted_source is not None else None,
            persisted_source.latest_observation_id if persisted_source is not None else None,
            len(executions),
        ) == (
            current.source_version.source_version_id,
            current.observation.snapshot.observation_id,
            3,
        )


def test_parser_execution_history_distinguishes_delivery_rerun_output_and_failure(
    session_factory: sessionmaker[Session],
) -> None:
    _, source, policy = _seed_source(session_factory)

    command = _command(source)
    initial = _complete_prepared(
        command,
        source,
        policy,
        aggregate_revision=1,
        parser_execution_id=uuid4(),
    )
    commit_prepared_collection(
        session_factory,
        command=command,
        prepared=initial,
        lock_authority=_locker(command, pointer_eligible=True),
    )
    duplicate_result = commit_prepared_collection(
        session_factory,
        command=command,
        prepared=initial,
        lock_authority=_locker(command, pointer_eligible=True),
    )

    rerun_command = _command(source)
    rerun = _complete_prepared(
        rerun_command,
        source,
        policy,
        aggregate_revision=2,
        source_version_id=initial.source_version.source_version_id,
        evidence_id=initial.evidence[0].evidence_id,
        extraction_revision_id=initial.extraction_revision.extraction_revision_id,
        parser_execution_id=uuid4(),
        content_hash=initial.source_version.content_hash,
    )
    commit_prepared_collection(
        session_factory,
        command=rerun_command,
        prepared=rerun,
        lock_authority=_locker(rerun_command, pointer_eligible=True),
    )

    changed_output_command = _command(source)
    changed_output = _complete_prepared(
        changed_output_command,
        source,
        policy,
        aggregate_revision=3,
        source_version_id=initial.source_version.source_version_id,
        evidence_id=initial.evidence[0].evidence_id,
        parser_execution_id=uuid4(),
        content_hash=initial.source_version.content_hash,
    )
    changed_output = replace(
        changed_output,
        extraction_revision=replace(
            changed_output.extraction_revision,
            output_hash="b" * 64,
        ),
        parser_execution=replace(
            changed_output.parser_execution,
            output_hash="b" * 64,
        ),
    )
    commit_prepared_collection(
        session_factory,
        command=changed_output_command,
        prepared=changed_output,
        lock_authority=_locker(changed_output_command, pointer_eligible=True),
    )

    failed_command = _command(source)
    failed = _failure_prepared(failed_command, source, policy, aggregate_revision=4)
    failed = replace(
        failed,
        source_version=initial.source_version,
        parser_execution=PreparedParserExecution(
            parser_execution_id=uuid4(),
            source_id=source.source_id,
            source_version_id=initial.source_version.source_version_id,
            content_hash=initial.source_version.content_hash,
            parser_version="atomic-parser-v1",
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
        executions = session.scalars(
            select(ParserExecution).where(ParserExecution.source_id == source.source_id)
        ).all()
        revisions = session.scalars(
            select(ExtractionRevision).where(
                ExtractionRevision.source_version_id == initial.source_version.source_version_id
            )
        ).all()

        assert (
            duplicate_result,
            Counter(
                (execution.status, execution.output_hash, execution.extraction_revision_id)
                for execution in executions
            ),
            frozenset(revision.output_hash for revision in revisions),
        ) == (
            initial.result,
            Counter(
                {
                    (
                        "succeeded",
                        "a" * 64,
                        initial.extraction_revision.extraction_revision_id,
                    ): 2,
                    (
                        "succeeded",
                        "b" * 64,
                        changed_output.extraction_revision.extraction_revision_id,
                    ): 1,
                    ("failed", None, None): 1,
                }
            ),
            frozenset({"a" * 64, "b" * 64}),
        )


def test_parser_v3_changed_output_preserves_prior_revision_evidence_and_snapshot(
    session_factory: sessionmaker[Session],
) -> None:
    _, source, policy = _seed_source(session_factory)

    initial_command = _command(source)
    initial = _complete_prepared(
        initial_command,
        source,
        policy,
        aggregate_revision=1,
    )
    initial = replace(
        initial,
        source_version=replace(
            initial.source_version,
            first_parser_version="epick-static-evidence-v2",
        ),
        extraction_revision=replace(
            initial.extraction_revision,
            parser_version="epick-static-evidence-v2",
        ),
        parser_execution=replace(
            initial.parser_execution,
            parser_version="epick-static-evidence-v2",
        ),
    )
    commit_prepared_collection(
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
        source_version_id=initial.source_version.source_version_id,
        evidence_id=initial.evidence[0].evidence_id,
        content_hash=initial.source_version.content_hash,
    )
    changed_section = changed.extraction_revision.posting_sections[0].model_copy(
        update={
            "section_key": "preferred-v3",
            "kind": PostingSectionKind.PREFERRED,
        }
    )
    changed = replace(
        changed,
        source_version=replace(
            changed.source_version,
            first_parser_version="epick-static-evidence-v2",
        ),
        extraction_revision=replace(
            changed.extraction_revision,
            parser_version=parsing_module.PARSER_VERSION,
            output_hash="b" * 64,
            posting_sections=(changed_section,),
        ),
        parser_execution=replace(
            changed.parser_execution,
            parser_version=parsing_module.PARSER_VERSION,
            output_hash="b" * 64,
        ),
    )
    commit_prepared_collection(
        session_factory,
        command=changed_command,
        prepared=changed,
        lock_authority=_locker(changed_command, pointer_eligible=True),
    )

    with session_factory() as session:
        revisions = {
            revision.extraction_revision_id: revision
            for revision in session.scalars(
                select(ExtractionRevision).where(
                    ExtractionRevision.source_version_id == initial.source_version.source_version_id
                )
            )
        }
        evidence = session.scalars(
            select(Evidence).where(
                Evidence.source_version_id == initial.source_version.source_version_id
            )
        ).all()
        observations = {
            observation.observation_id: observation
            for observation in session.scalars(
                select(SourceObservation).where(SourceObservation.source_id == source.source_id)
            )
        }
        source_versions = session.scalars(
            select(SourceVersion).where(SourceVersion.source_id == source.source_id)
        ).all()
        initial_event = session.get(OutboxEvent, initial.events[0].event_id)

        assert (
            parsing_module.PARSER_VERSION,
            [(item.source_version_id, item.content_hash) for item in source_versions],
            len(revisions),
            frozenset(item.output_hash for item in revisions.values()),
            revisions[initial.extraction_revision.extraction_revision_id].parser_version,
            revisions[initial.extraction_revision.extraction_revision_id].posting_sections[0][
                "kind"
            ],
            revisions[changed.extraction_revision.extraction_revision_id].parser_version,
            revisions[changed.extraction_revision.extraction_revision_id].posting_sections[0][
                "kind"
            ],
            [(item.evidence_id, item.text_excerpt) for item in evidence],
            frozenset(observations),
            frozenset(item.source_version_id for item in observations.values()),
            initial_event.payload if initial_event is not None else None,
        ) == (
            "epick-static-evidence-v3",
            [(initial.source_version.source_version_id, initial.source_version.content_hash)],
            2,
            frozenset({"a" * 64, "b" * 64}),
            "epick-static-evidence-v2",
            PostingSectionKind.REQUIRED.value,
            "epick-static-evidence-v3",
            PostingSectionKind.PREFERRED.value,
            [(initial.evidence[0].evidence_id, initial.evidence[0].text_excerpt)],
            frozenset(
                {
                    initial.observation.snapshot.observation_id,
                    changed.observation.snapshot.observation_id,
                }
            ),
            frozenset({initial.source_version.source_version_id}),
            initial.events[0].payload.model_dump(mode="json"),
        )


def test_refetched_different_response_creates_a_new_version_without_mixing_evidence(
    session_factory: sessionmaker[Session],
) -> None:
    _, source, policy = _seed_source(session_factory)

    historical_command = _command(source)
    historical = _complete_prepared(
        historical_command,
        source,
        policy,
        aggregate_revision=1,
    )
    commit_prepared_collection(
        session_factory,
        command=historical_command,
        prepared=historical,
        lock_authority=_locker(historical_command, pointer_eligible=True),
    )

    refetched_command = _command(source)
    refetched = _complete_prepared(
        refetched_command,
        source,
        policy,
        aggregate_revision=2,
        content_hash="b" * 64,
    )
    commit_prepared_collection(
        session_factory,
        command=refetched_command,
        prepared=refetched,
        lock_authority=_locker(refetched_command, pointer_eligible=True),
    )

    with session_factory() as session:
        versions = session.scalars(
            select(SourceVersion).where(SourceVersion.source_id == source.source_id)
        ).all()
        revisions = session.scalars(
            select(ExtractionRevision).where(
                ExtractionRevision.source_version_id.in_(
                    [
                        historical.source_version.source_version_id,
                        refetched.source_version.source_version_id,
                    ]
                )
            )
        ).all()
        evidence = session.scalars(
            select(Evidence).where(
                Evidence.source_version_id.in_(
                    [
                        historical.source_version.source_version_id,
                        refetched.source_version.source_version_id,
                    ]
                )
            )
        ).all()

        assert (
            frozenset((version.source_version_id, version.content_hash) for version in versions),
            frozenset(
                (revision.source_version_id, revision.extraction_revision_id)
                for revision in revisions
            ),
            frozenset((item.source_version_id, item.evidence_id) for item in evidence),
        ) == (
            frozenset(
                {
                    (historical.source_version.source_version_id, "a" * 64),
                    (refetched.source_version.source_version_id, "b" * 64),
                }
            ),
            frozenset(
                {
                    (
                        historical.source_version.source_version_id,
                        historical.extraction_revision.extraction_revision_id,
                    ),
                    (
                        refetched.source_version.source_version_id,
                        refetched.extraction_revision.extraction_revision_id,
                    ),
                }
            ),
            frozenset(
                {
                    (
                        historical.source_version.source_version_id,
                        historical.evidence[0].evidence_id,
                    ),
                    (refetched.source_version.source_version_id, refetched.evidence[0].evidence_id),
                }
            ),
        )


def test_bodyless_historical_refetch_uses_fresh_response_without_mixing_evidence(
    session_factory: sessionmaker[Session],
) -> None:
    _, source, policy = _seed_source(session_factory)
    historical_document = _pipeline_candidate().document.text
    refetched_document = historical_document.replace("문서화 경험", "분산 시스템 경험")

    def prepare_refetch(command, document: str, aggregate_revision: int):
        candidate = _pipeline_candidate()
        response = replace(
            candidate,
            document=replace(candidate.document, text=document),
            raw_size=len(document.encode("utf-8")),
            decompressed_size=len(document.encode("utf-8")),
        )
        base_input = _pipeline_input()
        input_value = replace(
            base_input,
            source_id=source.source_id,
            company_id=source.company_id,
            source_url=source.canonical_url,
            title=source.title,
            policy=_policy_snapshot(policy),
            policy_revision=policy.revision,
            aggregate_revision=aggregate_revision,
        )
        events: list[str] = []
        execution = StaticCollectionExecution(
            attempt_id=uuid4(),
            input_provider=_PipelineInputProvider(input_value, events),
            collector=_PipelineCollector(
                replace(_pipeline_fetch_result(response), command_id=command.command_id),
                events,
            ),
            clock=lambda: policy.checked_at,
            uuid_factory=uuid4,
        )
        return execution.run_once(_PipelineContext(command))

    historical_command = _command(source)
    historical = prepare_refetch(historical_command, historical_document, 1)
    commit_prepared_collection(
        session_factory,
        command=historical_command,
        prepared=historical,
        lock_authority=_locker(historical_command, pointer_eligible=True),
    )

    refetched_command = _command(source)
    refetched = prepare_refetch(refetched_command, refetched_document, 2)
    commit_prepared_collection(
        session_factory,
        command=refetched_command,
        prepared=refetched,
        lock_authority=_locker(refetched_command, pointer_eligible=True),
    )

    with session_factory() as session:
        versions = session.scalars(
            select(SourceVersion).where(SourceVersion.source_id == source.source_id)
        ).all()
        revisions = session.scalars(
            select(ExtractionRevision).where(
                ExtractionRevision.source_version_id.in_(
                    [
                        historical.source_version.source_version_id,
                        refetched.source_version.source_version_id,
                    ]
                )
            )
        ).all()
        evidence = session.scalars(
            select(Evidence).where(
                Evidence.source_version_id.in_(
                    [
                        historical.source_version.source_version_id,
                        refetched.source_version.source_version_id,
                    ]
                )
            )
        ).all()
        historical_event = session.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == source.source_id,
                OutboxEvent.aggregate_revision == 1,
            )
        )

        assert (
            historical.source_version.source_version_id
            != refetched.source_version.source_version_id,
            historical.extraction_revision.extraction_revision_id
            != refetched.extraction_revision.extraction_revision_id,
            frozenset((version.source_version_id, version.content_hash) for version in versions),
            frozenset(
                (revision.source_version_id, revision.extraction_revision_id)
                for revision in revisions
            ),
            {
                source_version_id: frozenset(
                    (item.evidence_key, item.text_excerpt)
                    for item in evidence
                    if item.source_version_id == source_version_id
                )
                for source_version_id in (
                    historical.source_version.source_version_id,
                    refetched.source_version.source_version_id,
                )
            },
            (historical_event.payload["retention_scope"] if historical_event is not None else None),
            (
                historical_event.payload["normalized_body_ref"]
                if historical_event is not None
                else None
            ),
        ) == (
            True,
            True,
            frozenset(
                {
                    (
                        historical.source_version.source_version_id,
                        historical.source_version.content_hash,
                    ),
                    (
                        refetched.source_version.source_version_id,
                        refetched.source_version.content_hash,
                    ),
                }
            ),
            frozenset(
                {
                    (
                        historical.source_version.source_version_id,
                        historical.extraction_revision.extraction_revision_id,
                    ),
                    (
                        refetched.source_version.source_version_id,
                        refetched.extraction_revision.extraction_revision_id,
                    ),
                }
            ),
            {
                historical.source_version.source_version_id: frozenset(
                    (item.evidence_key, item.text_excerpt) for item in historical.evidence
                ),
                refetched.source_version.source_version_id: frozenset(
                    (item.evidence_key, item.text_excerpt) for item in refetched.evidence
                ),
            },
            "excerpts_only",
            None,
        )


def test_reused_revision_event_keeps_the_revision_creator_parser_version(
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


def test_unicode_normalized_text_offsets_are_code_points_not_utf8_bytes() -> None:
    normalized_body = "지원 자격: 가😀나"
    excerpt = "가😀나"
    code_point_start = normalized_body.index(excerpt)
    code_point_end = code_point_start + len(excerpt)
    byte_start = len(normalized_body[:code_point_start].encode("utf-8"))
    byte_end = byte_start + len(excerpt.encode("utf-8"))
    common = {
        "evidence_key": "unicode:0",
        "section_title": "지원 자격",
        "text_excerpt": excerpt,
        "chunk_order": 0,
        "origin_kind": "direct",
    }
    code_point_evidence = EvidenceDraft(
        **common,
        locator=Locator(
            kind=LocatorKind.NORMALIZED_TEXT,
            value="retained-body",
            normalization_version="unicode-v1",
            start=code_point_start,
            end=code_point_end,
        ),
    )
    byte_evidence = EvidenceDraft(
        **common,
        locator=Locator(
            kind=LocatorKind.NORMALIZED_TEXT,
            value="retained-body",
            normalization_version="unicode-v1",
            start=byte_start,
            end=byte_end,
        ),
    )

    assert (
        parsing_module.validate_evidence_locator(normalized_body, code_point_evidence),
        parsing_module.validate_evidence_locator(normalized_body, byte_evidence),
    ) == (True, False)
