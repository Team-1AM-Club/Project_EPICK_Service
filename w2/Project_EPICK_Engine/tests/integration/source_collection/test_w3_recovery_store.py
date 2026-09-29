"""PostgreSQL coverage for the read-only W2-to-W3 recovery history store."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from epick_engine.source_collection.contracts import (
    SourceEvent,
    SourceObservationSnapshot,
    SourceRestrictionSnapshot,
)
from epick_engine.source_collection.persistence import Base, Company, OutboxEvent, Source
from epick_engine.source_collection.w3_recovery_payloads import make_snapshot
from epick_engine.source_collection.w3_recovery_store import (
    SqlAlchemyRecoveryHistoryStore,
    W3RecoveryHistoryError,
)

pytestmark = pytest.mark.approved_postgres

NOW = datetime(2026, 9, 21, tzinfo=UTC)


@pytest.fixture
def database_engine(approved_postgres_url) -> Iterator[Engine]:
    """Provide an isolated PostgreSQL schema for each recovery-store test."""

    admin_engine = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema = f"epick_w3_recovery_store_{uuid4().hex}"
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


def _seed_source(session_factory: sessionmaker[Session]) -> Source:
    company = Company(
        company_id=uuid4(),
        legal_name="Recovery Store Test Company Ltd.",
        aliases=["Recovery Store Test Company"],
        official_domains=["example.test"],
        legal_identifiers={"registration": "RECOVERY-STORE-001"},
        identity_status="verified",
        identity_evidence=["synthetic://company-evidence"],
    )
    source = Source(
        source_id=uuid4(),
        company_id=company.company_id,
        source_type="job_posting",
        canonical_url=f"https://jobs.example.test/opening/{uuid4().hex}",
        title="Recovery Store Platform Engineer",
    )
    with session_factory.begin() as session:
        session.add_all([company, source])
    return source


def _observation_event(
    source: Source,
    *,
    aggregate_revision: int,
    occurred_at: datetime = NOW,
) -> SourceEvent:
    return SourceEvent(
        event_id=uuid4(),
        event_type="source.observation.changed",
        schema_version="w2.source.v1",
        aggregate_id=source.source_id,
        aggregate_revision=aggregate_revision,
        occurred_at=occurred_at,
        payload=SourceObservationSnapshot(
            observation_id=uuid4(),
            source_id=source.source_id,
            source_version_id=None,
            policy_decision_id=None,
            observed_at=occurred_at,
            access_class="public",
            acquisition_status="AVAILABLE",
            http_status=200,
            checked_url=source.canonical_url,
            error_code=None,
            representation="static_html",
        ),
    )


def _restriction_event(
    source: Source,
    *,
    aggregate_revision: int,
    restriction_revision: int,
) -> SourceEvent:
    return SourceEvent(
        event_id=uuid4(),
        event_type="source.restriction.changed",
        schema_version="w2.source.v1",
        aggregate_id=source.source_id,
        aggregate_revision=aggregate_revision,
        occurred_at=NOW,
        payload=SourceRestrictionSnapshot(
            restriction_id=uuid4(),
            source_id=source.source_id,
            source_version_id=None,
            restriction_revision=restriction_revision,
            restriction_status="active",
            accuracy_status="error_confirmed",
            reason_code="CONFIRMED_ERROR",
            changed_at=NOW,
            replacement_ref=None,
        ),
    )


def _outbox_row(
    source_event: SourceEvent,
    *,
    delivery_state: str = "pending",
    payload: dict[str, object] | None = None,
) -> OutboxEvent:
    return OutboxEvent(
        event_id=source_event.event_id,
        aggregate_id=source_event.aggregate_id,
        aggregate_revision=source_event.aggregate_revision,
        event_type=source_event.event_type.value,
        schema_version=source_event.schema_version,
        payload=source_event.payload.model_dump(mode="json") if payload is None else payload,
        occurred_at=source_event.occurred_at,
        delivery_state=delivery_state,
    )


def _seed_outbox(
    session_factory: sessionmaker[Session],
    source_event: SourceEvent,
    *,
    delivery_state: str = "pending",
    payload: dict[str, object] | None = None,
) -> None:
    with session_factory.begin() as session:
        session.add(_outbox_row(source_event, delivery_state=delivery_state, payload=payload))


def _store(session_factory: sessionmaker[Session]) -> SqlAlchemyRecoveryHistoryStore:
    return SqlAlchemyRecoveryHistoryStore(session_factory=session_factory)


def test_load_history_returns_exact_contiguous_public_events_without_delivery_mutation(
    session_factory: sessionmaker[Session],
) -> None:
    """Changing aggregate ordering or touching delivery state must fail this test."""

    source = _seed_source(session_factory)
    seeded = (
        _observation_event(source, aggregate_revision=1),
        _restriction_event(source, aggregate_revision=2, restriction_revision=1),
        _observation_event(source, aggregate_revision=3),
    )
    for source_event, delivery_state in zip(
        seeded, ("pending", "delivered", "pending"), strict=True
    ):
        _seed_outbox(session_factory, source_event, delivery_state=delivery_state)

    history = _store(session_factory).load_history(source_id=source.source_id)

    assert history.source_id == source.source_id
    assert history.high_watermark == 3
    assert history.restriction_revision == 1
    assert [item.aggregate_revision for item in history.events] == [1, 2, 3]
    assert history.events == seeded
    assert history.as_of.tzinfo is UTC
    with session_factory() as session:
        delivery_states = list(
            session.scalars(
                select(OutboxEvent.delivery_state)
                .where(OutboxEvent.aggregate_id == source.source_id)
                .order_by(OutboxEvent.aggregate_revision)
            )
        )
    assert delivery_states == ["pending", "delivered", "pending"]


def test_load_history_as_of_uses_an_included_future_occurred_at(
    session_factory: sessionmaker[Session],
) -> None:
    """Returning only the database timestamp would make snapshot freshness regress."""

    source = _seed_source(session_factory)
    future_occurred_at = datetime(2099, 1, 1, tzinfo=UTC)
    _seed_outbox(
        session_factory,
        _observation_event(
            source,
            aggregate_revision=1,
            occurred_at=future_occurred_at,
        ),
    )

    history = _store(session_factory).load_history(source_id=source.source_id)

    assert history.as_of == future_occurred_at


def test_load_history_repeated_reads_produce_the_same_snapshot_for_unchanged_history(
    session_factory: sessionmaker[Session],
) -> None:
    """A fresh DB clock must not alter the snapshot at the same H and R."""

    source = _seed_source(session_factory)
    earlier = datetime(2020, 1, 1, tzinfo=UTC)
    later = datetime(2020, 1, 2, tzinfo=UTC)
    _seed_outbox(
        session_factory, _observation_event(source, aggregate_revision=1, occurred_at=earlier)
    )
    _seed_outbox(
        session_factory, _observation_event(source, aggregate_revision=2, occurred_at=later)
    )
    store = _store(session_factory)

    first = store.load_history(source_id=source.source_id)
    second = store.load_history(source_id=source.source_id)
    first_snapshot = make_snapshot(first)
    second_snapshot = make_snapshot(second)

    assert (first.high_watermark, first.restriction_revision) == (2, 0)
    assert (second.high_watermark, second.restriction_revision) == (2, 0)
    assert first.as_of == second.as_of == later
    assert first_snapshot["as_of"] == later.isoformat()
    assert first_snapshot == second_snapshot
    assert (
        json.dumps(
            first_snapshot, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
        == json.dumps(
            second_snapshot, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    )


def test_load_history_rejects_an_unregistered_source(
    session_factory: sessionmaker[Session],
) -> None:
    """Removing the Source registration must make recovery fail closed."""

    with pytest.raises(W3RecoveryHistoryError) as raised:
        _store(session_factory).load_history(source_id=uuid4())

    assert raised.value.code == "SOURCE_NOT_FOUND"


def test_load_history_rejects_a_registered_source_without_public_history(
    session_factory: sessionmaker[Session],
) -> None:
    """Dropping all outbox rows must prevent an empty complete recovery."""

    source = _seed_source(session_factory)

    with pytest.raises(W3RecoveryHistoryError) as raised:
        _store(session_factory).load_history(source_id=source.source_id)

    assert raised.value.code == "EMPTY_HISTORY"


def test_load_history_rejects_a_missing_aggregate_revision(
    session_factory: sessionmaker[Session],
) -> None:
    """Removing an intermediate aggregate event must prevent an F=0 declaration."""

    source = _seed_source(session_factory)
    _seed_outbox(session_factory, _observation_event(source, aggregate_revision=1))
    _seed_outbox(session_factory, _observation_event(source, aggregate_revision=3))

    with pytest.raises(W3RecoveryHistoryError) as raised:
        _store(session_factory).load_history(source_id=source.source_id)

    assert raised.value.code == "HISTORY_GAP"


def test_load_history_rejects_a_malformed_public_event_without_reflecting_payload(
    session_factory: sessionmaker[Session],
) -> None:
    """Corrupting the immutable public payload must fail closed without data reflection."""

    source = _seed_source(session_factory)
    private_value = "SYNTHETIC_PRIVATE_OUTBOX_VALUE"
    _seed_outbox(
        session_factory,
        _observation_event(source, aggregate_revision=1),
        payload={"body_text": private_value},
    )

    with pytest.raises(W3RecoveryHistoryError) as raised:
        _store(session_factory).load_history(source_id=source.source_id)

    assert raised.value.code == "INVALID_EVENT"
    assert private_value not in str(raised.value)


def test_load_history_rejects_a_gap_in_the_independent_restriction_revision_sequence(
    session_factory: sessionmaker[Session],
) -> None:
    """Changing restriction order independently of aggregate order must fail closed."""

    source = _seed_source(session_factory)
    _seed_outbox(
        session_factory,
        _restriction_event(source, aggregate_revision=1, restriction_revision=1),
    )
    _seed_outbox(
        session_factory,
        _restriction_event(source, aggregate_revision=2, restriction_revision=3),
    )

    with pytest.raises(W3RecoveryHistoryError) as raised:
        _store(session_factory).load_history(source_id=source.source_id)

    assert raised.value.code == "RESTRICTION_REVISION_GAP"


def test_load_history_excludes_an_append_committed_after_its_first_select(
    session_factory: sessionmaker[Session],
    database_engine: Engine,
) -> None:
    """Replacing repeatable-read with read-committed must expose the concurrent append."""

    source = _seed_source(session_factory)
    _seed_outbox(session_factory, _observation_event(source, aggregate_revision=1))
    appended = False

    def append_after_source_select(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        nonlocal appended
        if (
            appended
            or not statement.lstrip().lower().startswith("select")
            or "sources" not in statement.lower()
        ):
            return
        appended = True
        _seed_outbox(session_factory, _observation_event(source, aggregate_revision=2))

    event.listen(database_engine, "after_cursor_execute", append_after_source_select)
    try:
        history = _store(session_factory).load_history(source_id=source.source_id)
    finally:
        event.remove(database_engine, "after_cursor_execute", append_after_source_select)

    assert appended
    assert history.high_watermark == 1
    assert [item.aggregate_revision for item in history.events] == [1]
    with session_factory() as session:
        persisted_revisions = list(
            session.scalars(
                select(OutboxEvent.aggregate_revision)
                .where(OutboxEvent.aggregate_id == source.source_id)
                .order_by(OutboxEvent.aggregate_revision)
            )
        )
    assert persisted_revisions == [1, 2]


def test_load_history_ignores_an_outbox_append_that_was_rolled_back(
    session_factory: sessionmaker[Session],
) -> None:
    """Counting uncommitted rows would incorrectly advance the high watermark."""

    source = _seed_source(session_factory)
    _seed_outbox(session_factory, _observation_event(source, aggregate_revision=1))
    append_session = session_factory()
    try:
        append_session.begin()
        append_session.add(_outbox_row(_observation_event(source, aggregate_revision=2)))
        append_session.flush()
        append_session.rollback()
    finally:
        append_session.close()

    history = _store(session_factory).load_history(source_id=source.source_id)

    assert history.high_watermark == 1
    assert [item.aggregate_revision for item in history.events] == [1]
