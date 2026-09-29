"""T093 SourceRestriction storage primitives against approved PostgreSQL."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Lock
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from epick_engine.source_collection import commit_gate_store  # noqa: F401
from epick_engine.source_collection import persistence as persistence_module
from epick_engine.source_collection.contracts import (
    AccuracyStatus,
    RestrictionStatus,
    SourceRestrictionSnapshot,
)
from epick_engine.source_collection.persistence import (
    Base,
    Company,
    Evidence,
    OutboxEvent,
    PersistenceConflict,
    Source,
    SourcePolicyDecision,
    SourceVersion,
)

pytestmark = pytest.mark.approved_postgres
NOW = datetime(2026, 9, 20, 9, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class RestrictionScope:
    source_id: UUID
    source_version_one_id: UUID
    source_version_two_id: UUID
    other_source_id: UUID
    other_source_version_id: UUID
    replacement_source_id: UUID
    evidence_id: UUID
    outbox_event_id: UUID


@pytest.fixture
def storage_api() -> SimpleNamespace:
    names = (
        "SourceRestrictionIdentity",
        "SourceRestriction",
        "record_source_restriction",
        "get_current_source_restriction",
        "list_current_source_restrictions",
        "list_source_restrictions",
    )
    values = {name: getattr(persistence_module, name, None) for name in names}
    assert all(value is not None for value in values.values()), (
        "T093 requires identity/history models and storage-only restriction helpers"
    )
    return SimpleNamespace(**values)


@pytest.fixture
def database_engine(approved_postgres_url) -> Iterator[Engine]:
    admin = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema = f"epick_source_restriction_{uuid4().hex}"
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = admin.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def session_factory(database_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(database_engine, expire_on_commit=False)


@pytest.fixture
def restriction_scope(session_factory: sessionmaker[Session]) -> RestrictionScope:
    primary_company_id, other_company_id = uuid4(), uuid4()
    source_id, other_source_id, replacement_source_id = uuid4(), uuid4(), uuid4()
    version_one_id, version_two_id, other_version_id = uuid4(), uuid4(), uuid4()
    evidence_id, outbox_event_id = uuid4(), uuid4()
    primary_policy_id, other_policy_id = uuid4(), uuid4()
    unknown_date = {"status": "unknown", "raw_text": None, "value": None}

    primary_company = Company(
        company_id=primary_company_id,
        legal_name="Synthetic Primary Company",
        aliases=[],
        official_domains=["primary.example.test"],
        legal_identifiers={},
        identity_status="confirmed",
        identity_evidence=["synthetic://primary-company"],
    )
    other_company = Company(
        company_id=other_company_id,
        legal_name="Synthetic Other Company",
        aliases=[],
        official_domains=["other.example.test"],
        legal_identifiers={},
        identity_status="confirmed",
        identity_evidence=["synthetic://other-company"],
    )
    source = Source(
        source_id=source_id,
        company_id=primary_company_id,
        source_type="company_website",
        canonical_url="https://primary.example.test/source",
        title="Synthetic Primary Source",
    )
    replacement = Source(
        source_id=replacement_source_id,
        company_id=primary_company_id,
        source_type="company_website",
        canonical_url="https://primary.example.test/replacement",
        title="Synthetic Replacement Source",
    )
    other_source = Source(
        source_id=other_source_id,
        company_id=other_company_id,
        source_type="company_website",
        canonical_url="https://other.example.test/source",
        title="Synthetic Other Source",
    )
    primary_policy = SourcePolicyDecision(
        policy_decision_id=primary_policy_id,
        source_id=source_id,
        revision=1,
        official_status="verified",
        access_class="public",
        collection_permission="allowed",
        excerpt_storage_permission="allowed",
        body_storage_permission="denied",
        redistribution_permission="unknown",
        evidence_refs=["synthetic://primary-policy"],
        checked_at=NOW,
        policy_version="synthetic-policy-v1",
    )
    other_policy = SourcePolicyDecision(
        policy_decision_id=other_policy_id,
        source_id=other_source_id,
        revision=1,
        official_status="verified",
        access_class="public",
        collection_permission="allowed",
        excerpt_storage_permission="allowed",
        body_storage_permission="denied",
        redistribution_permission="unknown",
        evidence_refs=["synthetic://other-policy"],
        checked_at=NOW,
        policy_version="synthetic-policy-v1",
    )

    def version(
        version_id: UUID,
        owner_source: Source,
        policy_id: UUID,
        content_hash: str,
        collected_at: datetime,
    ) -> SourceVersion:
        return SourceVersion(
            source_version_id=version_id,
            source_id=owner_source.source_id,
            company_id=owner_source.company_id,
            title=owner_source.title,
            source_type=owner_source.source_type,
            canonical_url=owner_source.canonical_url,
            content_hash=content_hash,
            hash_profile_version="synthetic-response-v1",
            representation="html",
            first_parser_version="synthetic-parser-v1",
            collected_at=collected_at,
            published_at=unknown_date,
            valid_from=unknown_date,
            valid_to=unknown_date,
            language="ko",
            policy_decision_id=policy_id,
        )

    with session_factory.begin() as session:
        session.add_all([primary_company, other_company])
        session.flush()
        session.add_all([source, replacement, other_source])
        session.flush()
        session.add_all([primary_policy, other_policy])
        session.flush()
        session.add_all(
            [
                version(version_one_id, source, primary_policy_id, "a" * 64, NOW),
                version(
                    version_two_id,
                    source,
                    primary_policy_id,
                    "b" * 64,
                    NOW + timedelta(minutes=1),
                ),
                version(other_version_id, other_source, other_policy_id, "c" * 64, NOW),
            ]
        )
        session.flush()
        source.current_source_version_id = version_two_id
        session.add_all(
            [
                Evidence(
                    evidence_id=evidence_id,
                    source_version_id=version_one_id,
                    evidence_key="synthetic-restriction:0",
                    section_title="Synthetic evidence",
                    text_excerpt="Synthetic historical evidence",
                    locator={"kind": "css", "value": "#evidence"},
                    chunk_order=0,
                    origin_kind="direct",
                ),
                OutboxEvent(
                    event_id=outbox_event_id,
                    aggregate_id=source_id,
                    aggregate_revision=1,
                    event_type="source.version.available",
                    schema_version="w2.source.v1",
                    payload={"fixture": "baseline"},
                    occurred_at=NOW,
                    delivery_state="pending",
                ),
            ]
        )

    return RestrictionScope(
        source_id=source_id,
        source_version_one_id=version_one_id,
        source_version_two_id=version_two_id,
        other_source_id=other_source_id,
        other_source_version_id=other_version_id,
        replacement_source_id=replacement_source_id,
        evidence_id=evidence_id,
        outbox_event_id=outbox_event_id,
    )


def _snapshot(
    scope: RestrictionScope,
    *,
    restriction_id: UUID,
    revision: int,
    status: RestrictionStatus,
    source_version_id: UUID | None,
    source_id: UUID | None = None,
    reason_code: str = "CONFIRMED_SOURCE_ERROR",
    changed_at: datetime | None = None,
    replacement_ref: UUID | None = None,
) -> SourceRestrictionSnapshot:
    return SourceRestrictionSnapshot(
        restriction_id=restriction_id,
        source_id=source_id or scope.source_id,
        source_version_id=source_version_id,
        restriction_revision=revision,
        restriction_status=status,
        accuracy_status=(
            AccuracyStatus.ERROR_CONFIRMED
            if status is RestrictionStatus.ACTIVE
            else AccuracyStatus.VERIFIED_IN_SCOPE
        ),
        reason_code=reason_code,
        changed_at=changed_at or NOW + timedelta(minutes=revision),
        replacement_ref=replacement_ref,
    )


def test_source_wide_revision_sequence_keeps_latest_state_per_stable_identity(
    session_factory: sessionmaker[Session],
    restriction_scope: RestrictionScope,
    storage_api: SimpleNamespace,
) -> None:
    scope = restriction_scope
    restriction_a, restriction_b, restriction_c = uuid4(), uuid4(), uuid4()
    sequence = (
        _snapshot(
            scope,
            restriction_id=restriction_a,
            revision=1,
            status=RestrictionStatus.ACTIVE,
            source_version_id=scope.source_version_one_id,
        ),
        _snapshot(
            scope,
            restriction_id=restriction_b,
            revision=2,
            status=RestrictionStatus.ACTIVE,
            source_version_id=None,
        ),
        _snapshot(
            scope,
            restriction_id=restriction_a,
            revision=3,
            status=RestrictionStatus.CLEARED,
            source_version_id=scope.source_version_one_id,
        ),
        _snapshot(
            scope,
            restriction_id=restriction_b,
            revision=4,
            status=RestrictionStatus.CLEARED,
            source_version_id=None,
        ),
        _snapshot(
            scope,
            restriction_id=restriction_a,
            revision=5,
            status=RestrictionStatus.ACTIVE,
            source_version_id=scope.source_version_one_id,
        ),
        _snapshot(
            scope,
            restriction_id=restriction_c,
            revision=6,
            status=RestrictionStatus.ACTIVE,
            source_version_id=scope.source_version_two_id,
            replacement_ref=scope.replacement_source_id,
        ),
        _snapshot(
            scope,
            restriction_id=restriction_a,
            revision=7,
            status=RestrictionStatus.CLEARED,
            source_version_id=scope.source_version_one_id,
        ),
        _snapshot(
            scope,
            restriction_id=restriction_c,
            revision=8,
            status=RestrictionStatus.CLEARED,
            source_version_id=scope.source_version_two_id,
        ),
    )

    with session_factory.begin() as session:
        before_versions = session.scalars(
            select(SourceVersion.source_version_id)
            .where(SourceVersion.source_id == scope.source_id)
            .order_by(SourceVersion.source_version_id)
        ).all()
        before_evidence = session.scalars(select(Evidence.evidence_id)).all()
        before_outbox = session.scalars(
            select(OutboxEvent.event_id).order_by(OutboxEvent.event_id)
        ).all()
        before_current = session.get(Source, scope.source_id).current_source_version_id

        for index, expected in enumerate(sequence):
            assert (
                storage_api.record_source_restriction(
                    session,
                    snapshot=expected,
                    evidence_refs=(f"synthetic://restriction/{expected.restriction_revision}",),
                )
                == expected
            )
            if index == 2:
                after_a_clear = {
                    item.restriction_id: item
                    for item in storage_api.list_current_source_restrictions(
                        session, source_id=scope.source_id
                    )
                }
                assert after_a_clear[restriction_a].restriction_status is RestrictionStatus.CLEARED
                assert after_a_clear[restriction_b].restriction_status is RestrictionStatus.ACTIVE

        latest = {
            item.restriction_id: item
            for item in storage_api.list_current_source_restrictions(
                session, source_id=scope.source_id
            )
        }
        assert {key: value.restriction_revision for key, value in latest.items()} == {
            restriction_a: 7,
            restriction_b: 4,
            restriction_c: 8,
        }
        assert latest[restriction_a].source_version_id == scope.source_version_one_id
        assert latest[restriction_b].source_version_id is None
        assert latest[restriction_c].source_version_id == scope.source_version_two_id
        assert [
            item.restriction_revision
            for item in storage_api.list_source_restrictions(session, source_id=scope.source_id)
        ] == list(range(1, 9))
        assert (
            storage_api.get_current_source_restriction(
                session,
                source_id=scope.source_id,
                restriction_id=restriction_c,
            )
            == sequence[-1]
        )

        assert (
            session.scalars(
                select(SourceVersion.source_version_id)
                .where(SourceVersion.source_id == scope.source_id)
                .order_by(SourceVersion.source_version_id)
            ).all()
            == before_versions
        )
        assert session.scalars(select(Evidence.evidence_id)).all() == before_evidence
        after_outbox = session.scalars(
            select(OutboxEvent.event_id).order_by(OutboxEvent.event_id)
        ).all()
        assert set(before_outbox).issubset(after_outbox)
        assert len(after_outbox) == len(before_outbox) + len(sequence)
        assert session.get(Source, scope.source_id).current_source_version_id == before_current


def test_exact_replay_returns_existing_row_but_mutation_gap_and_revision_reuse_conflict(
    session_factory: sessionmaker[Session],
    restriction_scope: RestrictionScope,
    storage_api: SimpleNamespace,
) -> None:
    scope = restriction_scope
    restriction_id = uuid4()
    revision_one = _snapshot(
        scope,
        restriction_id=restriction_id,
        revision=1,
        status=RestrictionStatus.ACTIVE,
        source_version_id=scope.source_version_one_id,
    )
    with session_factory.begin() as session:
        first = storage_api.record_source_restriction(
            session,
            snapshot=revision_one,
            evidence_refs=("synthetic://same",),
        )
        assert (
            storage_api.record_source_restriction(
                session,
                snapshot=revision_one,
                evidence_refs=("synthetic://same",),
            )
            == first
        )
        with pytest.raises(PersistenceConflict, match="payload"):
            storage_api.record_source_restriction(
                session,
                snapshot=revision_one.model_copy(update={"reason_code": "MUTATED"}),
                evidence_refs=("synthetic://same",),
            )
        with pytest.raises(PersistenceConflict, match="payload"):
            storage_api.record_source_restriction(
                session,
                snapshot=revision_one,
                evidence_refs=("synthetic://mutated",),
            )
        with pytest.raises(PersistenceConflict, match="next restriction revision"):
            storage_api.record_source_restriction(
                session,
                snapshot=_snapshot(
                    scope,
                    restriction_id=restriction_id,
                    revision=3,
                    status=RestrictionStatus.CLEARED,
                    source_version_id=scope.source_version_one_id,
                ),
                evidence_refs=(),
            )
        with pytest.raises(PersistenceConflict, match="revision"):
            storage_api.record_source_restriction(
                session,
                snapshot=_snapshot(
                    scope,
                    restriction_id=uuid4(),
                    revision=1,
                    status=RestrictionStatus.ACTIVE,
                    source_version_id=None,
                ),
                evidence_refs=(),
            )

        revision_two = _snapshot(
            scope,
            restriction_id=restriction_id,
            revision=2,
            status=RestrictionStatus.CLEARED,
            source_version_id=scope.source_version_one_id,
            changed_at=NOW - timedelta(days=1),
        )
        assert (
            storage_api.record_source_restriction(
                session,
                snapshot=revision_two,
                evidence_refs=("synthetic://revision-two",),
            )
            == revision_two
        )
        assert (
            storage_api.get_current_source_restriction(
                session,
                source_id=scope.source_id,
                restriction_id=restriction_id,
            )
            == revision_two
        )


def test_scope_change_wrong_version_and_unregistered_replacement_leave_no_partial_rows(
    session_factory: sessionmaker[Session],
    restriction_scope: RestrictionScope,
    storage_api: SimpleNamespace,
) -> None:
    scope = restriction_scope
    restriction_id = uuid4()
    with session_factory.begin() as session:
        with pytest.raises(PersistenceConflict, match="version"):
            storage_api.record_source_restriction(
                session,
                snapshot=_snapshot(
                    scope,
                    restriction_id=restriction_id,
                    revision=1,
                    status=RestrictionStatus.ACTIVE,
                    source_version_id=scope.other_source_version_id,
                ),
                evidence_refs=(),
            )
        with pytest.raises(PersistenceConflict, match="replacement"):
            storage_api.record_source_restriction(
                session,
                snapshot=_snapshot(
                    scope,
                    restriction_id=restriction_id,
                    revision=1,
                    status=RestrictionStatus.ACTIVE,
                    source_version_id=scope.source_version_one_id,
                    replacement_ref=uuid4(),
                ),
                evidence_refs=(),
            )
        assert (
            session.scalar(select(func.count()).select_from(storage_api.SourceRestrictionIdentity))
            == 0
        )
        assert session.scalar(select(func.count()).select_from(storage_api.SourceRestriction)) == 0

        first = _snapshot(
            scope,
            restriction_id=restriction_id,
            revision=1,
            status=RestrictionStatus.ACTIVE,
            source_version_id=scope.source_version_one_id,
            replacement_ref=scope.replacement_source_id,
        )
        storage_api.record_source_restriction(session, snapshot=first, evidence_refs=())
        with pytest.raises(PersistenceConflict, match="scope"):
            storage_api.record_source_restriction(
                session,
                snapshot=_snapshot(
                    scope,
                    restriction_id=restriction_id,
                    revision=2,
                    status=RestrictionStatus.CLEARED,
                    source_version_id=scope.source_version_two_id,
                ),
                evidence_refs=(),
            )
        with pytest.raises(PersistenceConflict, match="scope"):
            storage_api.record_source_restriction(
                session,
                snapshot=_snapshot(
                    scope,
                    restriction_id=restriction_id,
                    revision=1,
                    status=RestrictionStatus.ACTIVE,
                    source_version_id=scope.other_source_version_id,
                    source_id=scope.other_source_id,
                ),
                evidence_refs=(),
            )

        second = _snapshot(
            scope,
            restriction_id=restriction_id,
            revision=2,
            status=RestrictionStatus.CLEARED,
            source_version_id=scope.source_version_one_id,
            replacement_ref=scope.source_id,
        )
        assert (
            storage_api.record_source_restriction(session, snapshot=second, evidence_refs=())
            == second
        )


def test_caller_rollback_does_not_consume_source_revision(
    session_factory: sessionmaker[Session],
    restriction_scope: RestrictionScope,
    storage_api: SimpleNamespace,
) -> None:
    scope = restriction_scope
    rolled_back_id, committed_id = uuid4(), uuid4()
    session = session_factory()
    try:
        transaction = session.begin()
        storage_api.record_source_restriction(
            session,
            snapshot=_snapshot(
                scope,
                restriction_id=rolled_back_id,
                revision=1,
                status=RestrictionStatus.ACTIVE,
                source_version_id=None,
            ),
            evidence_refs=("synthetic://rolled-back",),
        )
        transaction.rollback()
    finally:
        session.close()

    committed = _snapshot(
        scope,
        restriction_id=committed_id,
        revision=1,
        status=RestrictionStatus.ACTIVE,
        source_version_id=None,
    )
    with session_factory.begin() as session:
        assert (
            storage_api.record_source_restriction(
                session,
                snapshot=committed,
                evidence_refs=("synthetic://committed",),
            )
            == committed
        )
    with session_factory() as session:
        assert (
            storage_api.get_current_source_restriction(
                session, source_id=scope.source_id, restriction_id=rolled_back_id
            )
            is None
        )
        assert (
            storage_api.get_current_source_restriction(
                session, source_id=scope.source_id, restriction_id=committed_id
            )
            == committed
        )


def test_concurrent_first_writers_serialize_one_revision_without_partial_identity(
    session_factory: sessionmaker[Session],
    restriction_scope: RestrictionScope,
    storage_api: SimpleNamespace,
) -> None:
    scope = restriction_scope
    snapshots = tuple(
        _snapshot(
            scope,
            restriction_id=uuid4(),
            revision=1,
            status=RestrictionStatus.ACTIVE,
            source_version_id=None,
        )
        for _ in range(2)
    )
    candidates = {snapshot.restriction_id: snapshot for snapshot in snapshots}
    ready = Barrier(2)

    def write(snapshot: SourceRestrictionSnapshot) -> tuple[UUID, str]:
        try:
            with session_factory.begin() as session:
                ready.wait(timeout=5)
                storage_api.record_source_restriction(
                    session,
                    snapshot=snapshot,
                    evidence_refs=(f"synthetic://concurrent/{snapshot.restriction_id}",),
                )
        except PersistenceConflict:
            return snapshot.restriction_id, "conflict"
        return snapshot.restriction_id, "stored"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = dict(pool.map(write, candidates.values()))
    assert sorted(outcomes.values()) == ["conflict", "stored"]
    winner = next(key for key, value in outcomes.items() if value == "stored")
    loser = next(key for key, value in outcomes.items() if value == "conflict")

    with session_factory.begin() as session:
        assert (
            session.scalar(select(func.count()).select_from(storage_api.SourceRestrictionIdentity))
            == 1
        )
        assert session.scalar(select(func.count()).select_from(storage_api.SourceRestriction)) == 1
        revision_two = _snapshot(
            scope,
            restriction_id=loser,
            revision=2,
            status=RestrictionStatus.ACTIVE,
            source_version_id=None,
        )
        storage_api.record_source_restriction(
            session,
            snapshot=revision_two,
            evidence_refs=("synthetic://after-conflict",),
        )
        assert {
            item.restriction_id
            for item in storage_api.list_current_source_restrictions(
                session, source_id=scope.source_id
            )
        } == {winner, loser}


def test_concurrent_sources_cannot_claim_the_same_new_restriction_identity(
    session_factory: sessionmaker[Session],
    restriction_scope: RestrictionScope,
    storage_api: SimpleNamespace,
) -> None:
    scope = restriction_scope
    restriction_id = uuid4()
    ready = Barrier(2)
    identity_reads = 0
    identity_reads_lock = Lock()
    identity_reads_ready = Barrier(2)
    candidates = (
        _snapshot(
            scope,
            restriction_id=restriction_id,
            revision=1,
            status=RestrictionStatus.ACTIVE,
            source_version_id=None,
        ),
        _snapshot(
            scope,
            restriction_id=restriction_id,
            revision=1,
            status=RestrictionStatus.ACTIVE,
            source_version_id=None,
            source_id=scope.other_source_id,
        ),
    )

    engine = session_factory.kw["bind"]

    def synchronize_absent_identity_reads(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        nonlocal identity_reads
        if "FROM source_restriction_identities" not in statement:
            return
        with identity_reads_lock:
            if identity_reads >= 2:
                return
            identity_reads += 1
        identity_reads_ready.wait(timeout=5)

    def write(snapshot: SourceRestrictionSnapshot) -> str:
        try:
            with session_factory.begin() as session:
                ready.wait(timeout=5)
                storage_api.record_source_restriction(
                    session,
                    snapshot=snapshot,
                    evidence_refs=(f"synthetic://identity-race/{snapshot.source_id}",),
                )
        except PersistenceConflict:
            return "conflict"
        return "stored"

    event.listen(engine, "after_cursor_execute", synchronize_absent_identity_reads)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(write, candidates)) == ["conflict", "stored"]
    finally:
        event.remove(engine, "after_cursor_execute", synchronize_absent_identity_reads)
    with session_factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(storage_api.SourceRestrictionIdentity))
            == 1
        )
        assert session.scalar(select(func.count()).select_from(storage_api.SourceRestriction)) == 1


def test_migration_upgrades_0005_to_current_head_and_matches_complete_metadata(
    approved_postgres_url,
) -> None:
    root = Path(__file__).resolve().parents[3]
    scripts = ScriptDirectory.from_config(Config(root / "alembic.ini"))
    assert scripts.get_heads() == ["0013_deletion_ack_confirmed"]
    schema = f"epick_source_restriction_migration_{uuid4().hex}"
    admin = create_engine(approved_postgres_url, pool_pre_ping=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        scoped_url = approved_postgres_url.set(
            query={**approved_postgres_url.query, "options": f"-csearch_path={schema}"}
        )
        environment = {
            **os.environ,
            "EPICK_DATABASE_URL": scoped_url.render_as_string(hide_password=False),
        }
        for target in ["0005_private_gate_delivery", "head"]:
            completed = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", target],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert completed.returncode == 0, f"isolated Alembic upgrade to {target} failed"
        with admin.begin() as connection:
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            assert MigrationContext.configure(connection).get_current_revision() == (
                "0013_deletion_ack_confirmed"
            )
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
