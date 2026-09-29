"""Private restriction mutation receipts against approved PostgreSQL."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from epick_engine.source_collection import (
    commit_gate_store,  # noqa: F401
    persistence,
)
from epick_engine.source_collection.contracts import (
    AccuracyStatus,
    RestrictionStatus,
    SourceObservationSnapshot,
    SourceRestrictionSnapshot,
)
from epick_engine.source_collection.persistence import (
    Base,
    Company,
    OutboxEvent,
    PersistenceConflict,
    Source,
)
from epick_engine.source_collection.restriction_service import (
    RestrictionMutation,
    RestrictionMutationConflict,
    RestrictionMutationDenied,
    SourceRestrictionService,
)

pytestmark = pytest.mark.approved_postgres
NOW = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class RestrictionScope:
    source_id: UUID


@dataclass(slots=True)
class SyntheticAuthority:
    authority_ref: str = "fixture:operator-a"
    denied: bool = False
    barrier: Barrier | None = None

    def authorize(self, *, request: RestrictionMutation) -> str:
        if self.denied:
            raise RestrictionMutationDenied()
        if self.barrier is not None:
            self.barrier.wait(timeout=5)
        return self.authority_ref


@pytest.fixture
def storage_api() -> SimpleNamespace:
    names = (
        "RestrictionMutationReceipt",
        "lock_source_restriction_revision",
        "get_source_restriction_revision",
    )
    values = {name: getattr(persistence, name, None) for name in names}
    assert all(value is not None for value in values.values()), (
        "T095 Task 1 requires the private receipt and persistence helpers"
    )
    return SimpleNamespace(**values)


@pytest.fixture
def database_engine(approved_postgres_url) -> Iterator[Engine]:
    admin = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema = f"epick_restriction_mutation_{uuid4().hex}"
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
    company_id, source_id = uuid4(), uuid4()
    with session_factory.begin() as session:
        session.add(
            Company(
                company_id=company_id,
                legal_name="Synthetic Receipt Company",
                aliases=[],
                official_domains=["receipt.example.test"],
                legal_identifiers={},
                identity_status="confirmed",
                identity_evidence=["synthetic://receipt-company"],
            )
        )
        session.flush()
        session.add(
            Source(
                source_id=source_id,
                company_id=company_id,
                source_type="company_website",
                canonical_url="https://receipt.example.test/source",
                title="Synthetic Receipt Source",
            )
        )
    return RestrictionScope(source_id=source_id)


@pytest.fixture
def authority() -> SyntheticAuthority:
    return SyntheticAuthority()


@pytest.fixture
def service(
    session_factory: sessionmaker[Session],
    authority: SyntheticAuthority,
) -> SourceRestrictionService:
    return SourceRestrictionService(session_factory=session_factory, authority=authority)


@pytest.fixture
def create_request(restriction_scope: RestrictionScope) -> RestrictionMutation:
    return RestrictionMutation(
        request_id=uuid4(),
        kind="create",
        source_id=restriction_scope.source_id,
        source_version_id=None,
        restriction_id=None,
        accuracy_status=AccuracyStatus.ERROR_CONFIRMED,
        reason_code="CONFIRMED_ERROR",
        evidence_refs=("fixture:evidence",),
        changed_at=NOW,
        replacement_ref=None,
    )


def _snapshot(
    scope: RestrictionScope,
    *,
    restriction_id: UUID,
    revision: int,
    status: RestrictionStatus,
) -> SourceRestrictionSnapshot:
    return SourceRestrictionSnapshot(
        restriction_id=restriction_id,
        source_id=scope.source_id,
        source_version_id=None,
        restriction_revision=revision,
        restriction_status=status,
        accuracy_status=AccuracyStatus.UNVERIFIED,
        reason_code=f"synthetic-receipt-{revision}",
        changed_at=NOW,
        replacement_ref=None,
    )


def test_receipt_api_is_present() -> None:
    assert getattr(persistence, "RestrictionMutationReceipt", None) is not None
    assert getattr(persistence, "lock_source_restriction_revision", None) is not None
    assert getattr(persistence, "get_source_restriction_revision", None) is not None


def test_restriction_mutation_commits_one_public_event_per_new_revision(
    service: SourceRestrictionService,
    session_factory: sessionmaker[Session],
    create_request: RestrictionMutation,
) -> None:
    created = service.apply(create_request)
    assert service.apply(create_request) == created

    with session_factory() as session:
        first_events = session.scalars(select(OutboxEvent)).all()
    assert len(first_events) == 1
    first = first_events[0]
    assert first.event_type == "source.restriction.changed"
    assert first.aggregate_id == create_request.source_id
    assert first.aggregate_revision == 1
    assert first.payload["restriction_revision"] == 1
    assert first.payload["restriction_id"] == str(created.restriction_id)
    assert first.delivery_state == "pending"

    cleared = service.apply(
        RestrictionMutation(
            request_id=uuid4(),
            kind="clear",
            source_id=create_request.source_id,
            source_version_id=None,
            restriction_id=created.restriction_id,
            accuracy_status=AccuracyStatus.UNVERIFIED,
            reason_code="CORRECTION_CONFIRMED",
            evidence_refs=("fixture:correction",),
            changed_at=NOW,
            replacement_ref=None,
        )
    )
    with session_factory() as session:
        events = session.scalars(select(OutboxEvent).order_by(OutboxEvent.aggregate_revision)).all()
    revisions = [
        (event.aggregate_revision, event.payload["restriction_revision"]) for event in events
    ]
    assert revisions == [
        (1, 1),
        (2, 2),
    ]
    assert events[1].payload["restriction_status"] == "cleared"
    assert events[1].payload["restriction_id"] == str(cleared.restriction_id)


def test_restriction_revision_remains_distinct_from_mixed_aggregate_revision(
    service: SourceRestrictionService,
    session_factory: sessionmaker[Session],
    create_request: RestrictionMutation,
) -> None:
    observation = SourceObservationSnapshot(
        observation_id=uuid4(),
        source_id=create_request.source_id,
        source_version_id=None,
        policy_decision_id=None,
        observed_at=NOW,
        access_class="public",
        acquisition_status="AVAILABLE",
        http_status=200,
        checked_url="https://receipt.example.test/source",
        error_code=None,
        representation="static_html",
    )
    with session_factory.begin() as session:
        session.add(
            OutboxEvent(
                event_id=uuid4(),
                aggregate_id=create_request.source_id,
                aggregate_revision=1,
                event_type="source.observation.changed",
                schema_version="w2.source.v1",
                payload=observation.model_dump(mode="json"),
                occurred_at=NOW,
                delivery_state="delivered",
            )
        )

    stored = service.apply(create_request)

    with session_factory() as session:
        events = session.scalars(select(OutboxEvent).order_by(OutboxEvent.aggregate_revision)).all()
    assert [(event.event_type, event.aggregate_revision) for event in events] == [
        ("source.observation.changed", 1),
        ("source.restriction.changed", 2),
    ]
    assert stored.restriction_revision == 1
    assert events[1].payload["restriction_revision"] == 1


def test_lock_source_revision_returns_gap_free_next_revision_and_rejects_unknown_source(
    session_factory: sessionmaker[Session],
    restriction_scope: RestrictionScope,
    storage_api: SimpleNamespace,
) -> None:
    restriction_id = uuid4()
    with session_factory.begin() as session:
        assert (
            storage_api.lock_source_restriction_revision(
                session, source_id=restriction_scope.source_id
            )
            == 1
        )
        persistence.record_source_restriction(
            session,
            snapshot=_snapshot(
                restriction_scope,
                restriction_id=restriction_id,
                revision=1,
                status=RestrictionStatus.ACTIVE,
            ),
        )
        assert (
            storage_api.lock_source_restriction_revision(
                session, source_id=restriction_scope.source_id
            )
            == 2
        )
        with pytest.raises(PersistenceConflict, match="not registered"):
            storage_api.lock_source_restriction_revision(session, source_id=uuid4())


def test_exact_revision_lookup_never_substitutes_latest_history(
    session_factory: sessionmaker[Session],
    restriction_scope: RestrictionScope,
    storage_api: SimpleNamespace,
) -> None:
    restriction_id = uuid4()
    first = _snapshot(
        restriction_scope,
        restriction_id=restriction_id,
        revision=1,
        status=RestrictionStatus.ACTIVE,
    )
    second = _snapshot(
        restriction_scope,
        restriction_id=restriction_id,
        revision=2,
        status=RestrictionStatus.CLEARED,
    )
    with session_factory.begin() as session:
        persistence.record_source_restriction(session, snapshot=first)
        persistence.record_source_restriction(session, snapshot=second)
    with session_factory() as session:
        assert (
            storage_api.get_source_restriction_revision(
                session,
                restriction_id=restriction_id,
                restriction_revision=1,
            )
            == first
        )
        assert (
            storage_api.get_source_restriction_revision(
                session,
                restriction_id=restriction_id,
                restriction_revision=99,
            )
            is None
        )


def test_receipt_persists_only_for_an_existing_immutable_history_row(
    session_factory: sessionmaker[Session],
    restriction_scope: RestrictionScope,
    storage_api: SimpleNamespace,
) -> None:
    restriction_id, request_id = uuid4(), uuid4()
    with session_factory.begin() as session:
        persistence.record_source_restriction(
            session,
            snapshot=_snapshot(
                restriction_scope,
                restriction_id=restriction_id,
                revision=1,
                status=RestrictionStatus.ACTIVE,
            ),
        )
        session.add(
            storage_api.RestrictionMutationReceipt(
                authority_ref="issuer:test-subject",
                request_id=request_id,
                request_hash="a" * 64,
                restriction_id=restriction_id,
                restriction_revision=1,
                created_at=NOW,
            )
        )
    with session_factory() as session:
        stored = session.get(
            storage_api.RestrictionMutationReceipt,
            {"authority_ref": "issuer:test-subject", "request_id": request_id},
        )
        assert stored is not None
        assert stored.request_hash == "a" * 64
        assert stored.restriction_id == restriction_id
        assert stored.restriction_revision == 1

    with pytest.raises(IntegrityError):
        with session_factory.begin() as session:
            session.add(
                storage_api.RestrictionMutationReceipt(
                    authority_ref="issuer:other-subject",
                    request_id=uuid4(),
                    request_hash="b" * 64,
                    restriction_id=uuid4(),
                    restriction_revision=1,
                    created_at=NOW,
                )
            )
            session.flush()


def test_receipt_primary_key_rejects_duplicate_authority_request_pair(
    session_factory: sessionmaker[Session],
    restriction_scope: RestrictionScope,
    storage_api: SimpleNamespace,
) -> None:
    restriction_id, request_id = uuid4(), uuid4()
    with session_factory.begin() as session:
        persistence.record_source_restriction(
            session,
            snapshot=_snapshot(
                restriction_scope,
                restriction_id=restriction_id,
                revision=1,
                status=RestrictionStatus.ACTIVE,
            ),
        )
        session.add(
            storage_api.RestrictionMutationReceipt(
                authority_ref="issuer:duplicate-subject",
                request_id=request_id,
                request_hash="c" * 64,
                restriction_id=restriction_id,
                restriction_revision=1,
                created_at=NOW,
            )
        )
    with pytest.raises(IntegrityError):
        with session_factory.begin() as session:
            session.add(
                storage_api.RestrictionMutationReceipt(
                    authority_ref="issuer:duplicate-subject",
                    request_id=request_id,
                    request_hash="d" * 64,
                    restriction_id=restriction_id,
                    restriction_revision=1,
                    created_at=NOW,
                )
            )
            session.flush()
    with session_factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(storage_api.RestrictionMutationReceipt))
            == 1
        )


def test_failed_receipt_write_rolls_back_history_and_identity(
    session_factory: sessionmaker[Session],
    restriction_scope: RestrictionScope,
    storage_api: SimpleNamespace,
) -> None:
    restriction_id = uuid4()
    with pytest.raises(IntegrityError):
        with session_factory.begin() as session:
            persistence.record_source_restriction(
                session,
                snapshot=_snapshot(
                    restriction_scope,
                    restriction_id=restriction_id,
                    revision=1,
                    status=RestrictionStatus.ACTIVE,
                ),
            )
            session.add(
                storage_api.RestrictionMutationReceipt(
                    authority_ref="issuer:rollback-subject",
                    request_id=uuid4(),
                    request_hash="not-a-sha256",
                    restriction_id=restriction_id,
                    restriction_revision=1,
                    created_at=NOW,
                )
            )
            session.flush()
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(persistence.SourceRestriction)) == 0
        assert (
            session.scalar(select(func.count()).select_from(persistence.SourceRestrictionIdentity))
            == 0
        )
        assert (
            session.scalar(select(func.count()).select_from(storage_api.RestrictionMutationReceipt))
            == 0
        )


def test_migration_upgrades_0006_through_0007_to_head_and_matches_complete_metadata(
    approved_postgres_url,
) -> None:
    root = Path(__file__).resolve().parents[3]
    scripts = ScriptDirectory.from_config(Config(root / "alembic.ini"))
    assert scripts.get_heads() == ["0013_deletion_ack_confirmed"]
    assert scripts.get_revision("0007_restriction_receipt").down_revision == (
        "0006_source_restriction"
    )
    assert scripts.get_revision("0008_collection_runtime").down_revision == (
        "0007_restriction_receipt"
    )
    schema = f"epick_restriction_mutation_migration_{uuid4().hex}"
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
        for target in ["0006_source_restriction", "head"]:
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


def _restriction_counts(session: Session) -> tuple[int, int, int]:
    return (
        session.scalar(select(func.count()).select_from(persistence.SourceRestrictionIdentity))
        or 0,
        session.scalar(select(func.count()).select_from(persistence.SourceRestriction)) or 0,
        session.scalar(select(func.count()).select_from(persistence.RestrictionMutationReceipt))
        or 0,
    )


def _register_source(session_factory: sessionmaker[Session]) -> UUID:
    company_id, source_id = uuid4(), uuid4()
    with session_factory.begin() as session:
        session.add(
            Company(
                company_id=company_id,
                legal_name=f"Synthetic Mutation Company {company_id}",
                aliases=[],
                official_domains=[f"{company_id}.example.test"],
                legal_identifiers={},
                identity_status="confirmed",
                identity_evidence=["synthetic://mutation-company"],
            )
        )
        session.flush()
        session.add(
            Source(
                source_id=source_id,
                company_id=company_id,
                source_type="company_website",
                canonical_url=f"https://{source_id}.example.test/source",
                title="Synthetic Mutation Source",
            )
        )
    return source_id


def _mutation_from(
    request: RestrictionMutation,
    **updates: object,
) -> RestrictionMutation:
    values = request.model_dump()
    values.update(updates)
    return RestrictionMutation(**values)


def _concurrent_results(
    first: Callable[[], object],
    second: Callable[[], object],
) -> tuple[object, object]:
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures: tuple[Future[object], Future[object]] = (
            executor.submit(first),
            executor.submit(second),
        )
        return tuple(future.result(timeout=15) for future in futures)  # type: ignore[return-value]


def test_create_replay_returns_original_snapshot(
    service: SourceRestrictionService,
    create_request: RestrictionMutation,
    session_factory: sessionmaker[Session],
) -> None:
    first = service.apply(create_request)
    replay = service.apply(create_request)

    assert replay == first
    assert first.restriction_revision == 1
    assert first.restriction_status is RestrictionStatus.ACTIVE
    with session_factory() as session:
        assert _restriction_counts(session) == (1, 1, 1)
        history = session.scalar(select(persistence.SourceRestriction))
        assert history is not None
        assert history.evidence_refs == ["fixture:evidence"]
        source = session.get(Source, create_request.source_id)
        assert source is not None
        assert source.current_source_version_id is None
        assert source.latest_observation_id is None
        assert session.scalar(select(func.count()).select_from(persistence.SourceVersion)) == 0
        assert session.scalar(select(func.count()).select_from(persistence.Evidence)) == 0
        assert session.scalar(select(func.count()).select_from(persistence.OutboxEvent)) == 1


@pytest.mark.parametrize(
    "updates",
    [
        {"reason_code": "OTHER_REASON"},
        {"source_id": UUID("60000000-0000-0000-0000-000000000006")},
        {
            "kind": "reactivate",
            "restriction_id": UUID("70000000-0000-0000-0000-000000000007"),
        },
        {"evidence_refs": ("fixture:second", "fixture:first")},
    ],
    ids=["payload", "source", "kind", "evidence-order"],
)
def test_changed_payload_same_key_is_rejected(
    service: SourceRestrictionService,
    create_request: RestrictionMutation,
    session_factory: sessionmaker[Session],
    updates: dict[str, object],
) -> None:
    original = _mutation_from(
        create_request,
        evidence_refs=("fixture:first", "fixture:second"),
    )
    service.apply(original)
    changed = _mutation_from(original, **updates)

    with pytest.raises(
        RestrictionMutationConflict,
        match="^restriction mutation conflict$",
    ):
        service.apply(changed)

    with session_factory() as session:
        assert _restriction_counts(session) == (1, 1, 1)


def test_changed_target_id_same_key_is_rejected(
    service: SourceRestrictionService,
    create_request: RestrictionMutation,
    session_factory: sessionmaker[Session],
) -> None:
    first = service.apply(create_request)
    second = service.apply(
        _mutation_from(create_request, request_id=uuid4(), reason_code="SECOND_CREATE")
    )
    request_id = uuid4()
    original = _mutation_from(
        create_request,
        request_id=request_id,
        kind="reactivate",
        restriction_id=first.restriction_id,
        reason_code="REACTIVATE_FIRST",
    )
    service.apply(original)
    changed = _mutation_from(original, restriction_id=second.restriction_id)

    with pytest.raises(RestrictionMutationConflict):
        service.apply(changed)

    with session_factory() as session:
        assert _restriction_counts(session) == (2, 3, 3)


@pytest.mark.parametrize(
    ("first_ref", "second_ref"),
    [
        ("fixture:operator-a", "fixture:operator-b"),
        ("issuer:사용자", "issuer:使用者"),
        ("issuer-a:local-user", "issuer-b:local-user"),
    ],
)
def test_same_request_id_is_namespaced_by_exact_authority(
    session_factory: sessionmaker[Session],
    create_request: RestrictionMutation,
    first_ref: str,
    second_ref: str,
) -> None:
    first_service = SourceRestrictionService(
        session_factory=session_factory,
        authority=SyntheticAuthority(first_ref),
    )
    second_service = SourceRestrictionService(
        session_factory=session_factory,
        authority=SyntheticAuthority(second_ref),
    )

    first = first_service.apply(create_request)
    second = second_service.apply(create_request)

    assert first.restriction_id != second.restriction_id
    assert {first.restriction_revision, second.restriction_revision} == {1, 2}
    with session_factory() as session:
        refs = set(
            session.scalars(select(persistence.RestrictionMutationReceipt.authority_ref)).all()
        )
        assert refs == {first_ref, second_ref}
        assert _restriction_counts(session) == (2, 2, 2)


@pytest.mark.parametrize(
    "authority_ref",
    ["", "   ", "fixture-operator", ":operator", "fixture:", "fixture:\noperator"],
)
def test_invalid_authority_identity_is_denied_before_database_access(
    session_factory: sessionmaker[Session],
    create_request: RestrictionMutation,
    authority_ref: str,
) -> None:
    service = SourceRestrictionService(
        session_factory=session_factory,
        authority=SyntheticAuthority(authority_ref),
    )

    with pytest.raises(RestrictionMutationDenied, match="^restriction mutation denied$"):
        service.apply(create_request)

    with session_factory() as session:
        assert _restriction_counts(session) == (0, 0, 0)


def test_create_clear_reactivate_then_create_replay_preserves_latest_state(
    service: SourceRestrictionService,
    create_request: RestrictionMutation,
    session_factory: sessionmaker[Session],
) -> None:
    created = service.apply(create_request)
    cleared = service.apply(
        _mutation_from(
            create_request,
            request_id=uuid4(),
            kind="clear",
            restriction_id=created.restriction_id,
            reason_code="CLEARED_AFTER_REVIEW",
        )
    )
    reactivated = service.apply(
        _mutation_from(
            create_request,
            request_id=uuid4(),
            kind="reactivate",
            restriction_id=created.restriction_id,
            reason_code="REACTIVATED_AFTER_REVIEW",
        )
    )

    replay = service.apply(create_request)

    assert replay == created
    assert cleared.restriction_status is RestrictionStatus.CLEARED
    assert reactivated.restriction_status is RestrictionStatus.ACTIVE
    assert [
        created.restriction_revision,
        cleared.restriction_revision,
        reactivated.restriction_revision,
    ] == [
        1,
        2,
        3,
    ]
    with session_factory() as session:
        latest = persistence.get_current_source_restriction(
            session,
            source_id=create_request.source_id,
            restriction_id=created.restriction_id,
        )
        assert latest == reactivated
        assert _restriction_counts(session) == (1, 3, 3)


def test_revoked_authority_cannot_replay_or_discover_receipt(
    service: SourceRestrictionService,
    authority: SyntheticAuthority,
    create_request: RestrictionMutation,
    session_factory: sessionmaker[Session],
) -> None:
    created = service.apply(create_request)
    authority.denied = True

    with pytest.raises(RestrictionMutationDenied, match="^restriction mutation denied$"):
        service.apply(create_request)

    with session_factory() as session:
        assert _restriction_counts(session) == (1, 1, 1)
        assert (
            persistence.get_current_source_restriction(
                session,
                source_id=create_request.source_id,
                restriction_id=created.restriction_id,
            )
            == created
        )


def test_denied_new_request_writes_nothing(
    session_factory: sessionmaker[Session],
    create_request: RestrictionMutation,
) -> None:
    service = SourceRestrictionService(
        session_factory=session_factory,
        authority=SyntheticAuthority(denied=True),
    )

    with pytest.raises(RestrictionMutationDenied):
        service.apply(create_request)

    with session_factory() as session:
        assert _restriction_counts(session) == (0, 0, 0)


def test_unknown_restriction_does_not_consume_revision(
    service: SourceRestrictionService,
    create_request: RestrictionMutation,
    session_factory: sessionmaker[Session],
) -> None:
    missing = _mutation_from(
        create_request,
        kind="clear",
        restriction_id=uuid4(),
    )

    with pytest.raises(RestrictionMutationConflict, match="^restriction mutation conflict$"):
        service.apply(missing)

    created = service.apply(_mutation_from(create_request, request_id=uuid4()))
    assert created.restriction_revision == 1
    with session_factory() as session:
        assert _restriction_counts(session) == (1, 1, 1)


def test_existing_restriction_cannot_move_source_or_version_scope(
    service: SourceRestrictionService,
    create_request: RestrictionMutation,
    session_factory: sessionmaker[Session],
) -> None:
    created = service.apply(create_request)
    other_source_id = _register_source(session_factory)
    wrong_source = _mutation_from(
        create_request,
        request_id=uuid4(),
        kind="clear",
        source_id=other_source_id,
        restriction_id=created.restriction_id,
    )
    wrong_version = _mutation_from(
        create_request,
        request_id=uuid4(),
        kind="clear",
        source_version_id=uuid4(),
        restriction_id=created.restriction_id,
    )

    with pytest.raises(RestrictionMutationConflict):
        service.apply(wrong_source)
    with pytest.raises(RestrictionMutationConflict):
        service.apply(wrong_version)

    other_created = service.apply(
        _mutation_from(
            create_request,
            request_id=uuid4(),
            source_id=other_source_id,
        )
    )
    assert other_created.restriction_revision == 1
    with session_factory() as session:
        assert _restriction_counts(session) == (2, 2, 2)


def test_unregistered_source_and_replacement_fail_without_revision_gap(
    service: SourceRestrictionService,
    create_request: RestrictionMutation,
    session_factory: sessionmaker[Session],
) -> None:
    unregistered_source = _mutation_from(create_request, source_id=uuid4())
    unregistered_replacement = _mutation_from(
        create_request,
        request_id=uuid4(),
        replacement_ref=uuid4(),
    )

    for invalid in (unregistered_source, unregistered_replacement):
        with pytest.raises(
            RestrictionMutationConflict,
            match="^restriction mutation conflict$",
        ):
            service.apply(invalid)

    created = service.apply(_mutation_from(create_request, request_id=uuid4()))
    assert created.restriction_revision == 1
    with session_factory() as session:
        assert _restriction_counts(session) == (1, 1, 1)


def test_concurrent_same_key_create_returns_one_original_result(
    session_factory: sessionmaker[Session],
    create_request: RestrictionMutation,
) -> None:
    authority = SyntheticAuthority(barrier=Barrier(2))
    service = SourceRestrictionService(session_factory=session_factory, authority=authority)

    first, second = _concurrent_results(
        lambda: service.apply(create_request),
        lambda: service.apply(create_request),
    )

    assert first == second
    assert isinstance(first, SourceRestrictionSnapshot)
    assert first.restriction_revision == 1
    with session_factory() as session:
        assert _restriction_counts(session) == (1, 1, 1)
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 1


def test_concurrent_different_keys_same_source_allocate_gap_free_revisions(
    session_factory: sessionmaker[Session],
    create_request: RestrictionMutation,
) -> None:
    authority = SyntheticAuthority(barrier=Barrier(2))
    service = SourceRestrictionService(session_factory=session_factory, authority=authority)
    second_request = _mutation_from(create_request, request_id=uuid4())

    first, second = _concurrent_results(
        lambda: service.apply(create_request),
        lambda: service.apply(second_request),
    )

    assert isinstance(first, SourceRestrictionSnapshot)
    assert isinstance(second, SourceRestrictionSnapshot)
    assert first.restriction_id != second.restriction_id
    assert {first.restriction_revision, second.restriction_revision} == {1, 2}
    with session_factory() as session:
        assert _restriction_counts(session) == (2, 2, 2)
        events = session.scalars(select(OutboxEvent).order_by(OutboxEvent.aggregate_revision)).all()
    assert [event.aggregate_revision for event in events] == [1, 2]
    assert [event.payload["restriction_revision"] for event in events] == [1, 2]


def test_concurrent_same_key_different_source_conflicts_before_second_source_write(
    session_factory: sessionmaker[Session],
    create_request: RestrictionMutation,
) -> None:
    other_source_id = _register_source(session_factory)
    authority = SyntheticAuthority(barrier=Barrier(2))
    service = SourceRestrictionService(session_factory=session_factory, authority=authority)
    other_request = _mutation_from(create_request, source_id=other_source_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(service.apply, create_request),
            executor.submit(service.apply, other_request),
        ]
        results: list[SourceRestrictionSnapshot] = []
        failures: list[BaseException] = []
        for future in futures:
            try:
                results.append(future.result(timeout=15))
            except BaseException as error:
                failures.append(error)

    assert len(results) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], RestrictionMutationConflict)
    with session_factory() as session:
        assert _restriction_counts(session) == (1, 1, 1)
        stored_source_ids = set(
            session.scalars(select(persistence.SourceRestriction.source_id)).all()
        )
        assert stored_source_ids == {results[0].source_id}


def test_receipt_insert_failure_rolls_back_history_identity_and_revision(
    service: SourceRestrictionService,
    create_request: RestrictionMutation,
    session_factory: sessionmaker[Session],
) -> None:
    def fail_receipt_insert(*_args: object) -> None:
        raise IntegrityError(
            "INSERT receipt secret-payload",
            {"request": "secret-payload"},
            RuntimeError("secret-payload"),
        )

    event.listen(
        persistence.RestrictionMutationReceipt,
        "before_insert",
        fail_receipt_insert,
    )
    try:
        with pytest.raises(RestrictionMutationConflict) as captured:
            service.apply(create_request)
    finally:
        event.remove(
            persistence.RestrictionMutationReceipt,
            "before_insert",
            fail_receipt_insert,
        )

    assert str(captured.value) == "restriction mutation conflict"
    assert "secret-payload" not in str(captured.value)
    with session_factory() as session:
        assert _restriction_counts(session) == (0, 0, 0)

    retried = service.apply(create_request)
    assert retried.restriction_revision == 1
    with session_factory() as session:
        assert _restriction_counts(session) == (1, 1, 1)
