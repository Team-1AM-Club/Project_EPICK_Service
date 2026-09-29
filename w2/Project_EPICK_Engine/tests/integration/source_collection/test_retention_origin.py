from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command as alembic_command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, func, inspect, select, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from test_atomic_persistence import (
    NOW,
    _command,
    _complete_prepared,
    _grant,
    _locker,
    _seed_source,
    commit_prepared_collection,
)

from epick_engine.source_collection.contracts import (
    AccessClass,
    CollectionCommand,
    Permission,
    Policy,
    RetentionScope,
    SourceEnvelope,
    SourceEvent,
    SourceMetadata,
)
from epick_engine.source_collection.contracts import (
    Evidence as EvidenceValue,
)
from epick_engine.source_collection.persistence import (
    CollectionAttempt,
    Evidence,
    InvalidPreparedCollection,
    OutboxEvent,
    PersistenceConflict,
    PreparedCollectionCommit,
    PreparedRetainedBody,
    PreparedSourceOrigin,
    RetainedBody,
    Source,
    SourceOrigin,
    SourceOriginEvidence,
    SourcePolicyDecision,
    SourceVersion,
    StaleExecution,
    get_retained_body_for_reextraction,
    list_source_origin_evidence,
    list_source_origins,
)

pytestmark = pytest.mark.approved_postgres

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_HEAD = "0013_deletion_ack_confirmed"


@pytest.fixture
def database_engine(
    approved_postgres_url: URL,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Engine]:
    schema_name = f"epick_w2_retention_{uuid4().hex}"
    schema_url = approved_postgres_url.update_query_dict(
        {"options": f"-csearch_path={schema_name}"}
    )
    admin_engine = create_engine(approved_postgres_url, pool_pre_ping=True)
    engine = create_engine(schema_url, pool_pre_ping=True)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    monkeypatch.setenv("EPICK_DATABASE_URL", schema_url.render_as_string(hide_password=False))
    try:
        alembic_command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture
def session_factory(database_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(database_engine, expire_on_commit=False)


def _allow_restricted_body_storage(
    session_factory: sessionmaker[Session],
    policy: SourcePolicyDecision,
) -> None:
    policy.access_class = AccessClass.RESTRICTED.value
    policy.collection_permission = Permission.ALLOWED.value
    policy.body_storage_permission = Permission.ALLOWED.value
    policy.redistribution_permission = Permission.DENIED.value
    with session_factory.begin() as session:
        persisted = session.get(SourcePolicyDecision, policy.policy_decision_id)
        assert persisted is not None
        persisted.access_class = policy.access_class
        persisted.collection_permission = policy.collection_permission
        persisted.body_storage_permission = policy.body_storage_permission
        persisted.redistribution_permission = policy.redistribution_permission


def _retained_body(
    prepared: PreparedCollectionCommit,
    policy: SourcePolicyDecision,
    *,
    body_id: UUID | None = None,
    body_text: str = "정규화된 본문",
    necessity_reason: str = "재현 가능한 재추출에 필요",
    normalization_version: str = "normalized-body.v1",
    retention_limit_bytes: int = 1024,
) -> PreparedRetainedBody:
    version = prepared.source_version
    assert version is not None
    return PreparedRetainedBody(
        body_id=body_id or uuid4(),
        source_version_id=version.source_version_id,
        source_id=version.source_id,
        normalization_version=normalization_version,
        body_text=body_text,
        necessity_reason=necessity_reason,
        policy_decision_id=policy.policy_decision_id,
        retained_at=NOW,
        retention_policy_version="retention-policy.test.v1",
        retention_limit_bytes=retention_limit_bytes,
    )


def _version_event_with_body(
    command: CollectionCommand,
    source: Source,
    prepared: PreparedCollectionCommit,
    policy: SourcePolicyDecision,
    body_id: UUID,
    *,
    event_id: UUID | None = None,
    aggregate_revision: int = 1,
) -> SourceEvent:
    version = prepared.source_version
    revision = prepared.extraction_revision
    assert version is not None and revision is not None
    envelope = SourceEnvelope(
        schema_version="w2.source.v1",
        source_id=source.source_id,
        source_version_id=version.source_version_id,
        extraction_revision_id=revision.extraction_revision_id,
        company_id=source.company_id,
        source_type=version.source_type,
        url_or_path=source.canonical_url,
        title=version.title,
        policy=Policy(
            policy_decision_id=policy.policy_decision_id,
            official_status=policy.official_status,
            access_class=policy.access_class,
            collection_permission=policy.collection_permission,
            excerpt_storage_permission=policy.excerpt_storage_permission,
            body_storage_permission=policy.body_storage_permission,
            redistribution_permission=policy.redistribution_permission,
            checked_at=policy.checked_at,
            policy_version=policy.policy_version,
        ),
        acquisition_status="AVAILABLE",
        extraction_status=revision.extraction_status,
        accuracy_status="unverified",
        freshness_status="current",
        published_at=version.published_at,
        collected_at=version.collected_at,
        checked_at=policy.checked_at,
        valid_from=version.valid_from,
        valid_to=version.valid_to,
        content_hash=version.content_hash,
        hash_profile_version=version.hash_profile_version,
        parser_version=revision.parser_version,
        language=version.language,
        evidence_spans=[
            EvidenceValue(
                evidence_id=evidence.evidence_id,
                source_version_id=evidence.source_version_id,
                section_title=evidence.section_title,
                text_excerpt=evidence.text_excerpt,
                locator=evidence.locator,
                chunk_order=evidence.chunk_order,
            )
            for evidence in prepared.evidence
        ],
        posting_sections=list(revision.posting_sections),
        retention_scope=RetentionScope.NORMALIZED_BODY,
        normalized_body_ref=body_id,
        limitations=list(revision.limitations),
        metadata=SourceMetadata(
            representation=version.representation,
            content_type="text/html",
            normalization_version=version.hash_profile_version,
        ),
    )
    return SourceEvent(
        event_id=event_id or uuid4(),
        event_type="source.version.available",
        schema_version="w2.source.v1",
        aggregate_id=source.source_id,
        aggregate_revision=aggregate_revision,
        occurred_at=revision.created_at,
        payload=envelope,
    )


def _prepared_with_body(
    command,
    source,
    policy: SourcePolicyDecision,
    *,
    event_id: UUID | None = None,
    aggregate_revision: int = 1,
    **body_changes: object,
) -> PreparedCollectionCommit:
    prepared = _complete_prepared(
        command,
        source,
        policy,
        aggregate_revision=aggregate_revision,
    )
    body = _retained_body(prepared, policy)
    if body_changes:
        body = replace(body, **body_changes)
    event = _version_event_with_body(
        command,
        source,
        prepared,
        policy,
        body.body_id,
        event_id=event_id,
        aggregate_revision=aggregate_revision,
    )
    return replace(prepared, retained_body=body, events=(event,))


def _origin(
    prepared: PreparedCollectionCommit,
    *,
    relation_id: UUID | None = None,
    evidence_ids: tuple[UUID, ...] | None = None,
    origin_source_id: UUID | None = None,
    origin_url: str | None = "https://origin.example.test/release/1",
) -> PreparedSourceOrigin:
    version = prepared.source_version
    assert version is not None
    return PreparedSourceOrigin(
        origin_relation_id=relation_id or uuid4(),
        source_id=version.source_id,
        origin_source_id=origin_source_id,
        origin_url=origin_url,
        relationship_kind="republication",
        verification_status="evidence_supported",
        evidence_ids=(
            evidence_ids if evidence_ids is not None else (prepared.evidence[0].evidence_id,)
        ),
    )


def _commit(
    session_factory: sessionmaker[Session],
    command,
    prepared: PreparedCollectionCommit,
) -> None:
    commit_prepared_collection(
        session_factory,
        command=command,
        prepared=prepared,
        lock_authority=_locker(command, pointer_eligible=True),
    )


def test_migration_head_has_retention_origin_constraints_without_cascade(
    database_engine: Engine,
) -> None:
    inspector = inspect(database_engine)
    scripts = ScriptDirectory.from_config(Config(str(PROJECT_ROOT / "alembic.ini")))
    with database_engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == MIGRATION_HEAD
    assert scripts.get_current_head() == MIGRATION_HEAD
    assert {
        "retained_bodies",
        "source_origins",
        "source_origin_evidence",
    } <= set(inspector.get_table_names())

    retained_checks = {item["name"] for item in inspector.get_check_constraints("retained_bodies")}
    assert {
        "ck_retained_bodies_nonempty_necessity_reason",
        "ck_retained_bodies_positive_retention_limit_bytes",
        "ck_retained_bodies_body_within_retention_limit",
    } <= retained_checks
    retained_unique = {item["name"] for item in inspector.get_unique_constraints("retained_bodies")}
    assert retained_unique == {"uq_retained_bodies_version_normalization"}
    assert {item["name"] for item in inspector.get_indexes("source_origins") if item["unique"]} == {
        "uq_source_origins_source_kind_origin_source",
        "uq_source_origins_source_kind_origin_url",
    }
    assert not [item for item in inspector.get_indexes("retained_bodies") if not item["unique"]]

    for table_name in ("retained_bodies", "source_origins", "source_origin_evidence"):
        for foreign_key in inspector.get_foreign_keys(table_name):
            assert foreign_key["options"].get("ondelete") in {None, "NO ACTION"}
    public_columns = {
        column["name"]
        for table_name in ("retained_bodies", "source_origins", "source_origin_evidence")
        for column in inspector.get_columns(table_name)
    }
    assert public_columns.isdisjoint({"owner_user_id", "job_id", "project_id", "attempt_id"})
    with database_engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_partitioned_table p "
                    "JOIN pg_class c ON c.oid = p.partrelid "
                    "WHERE c.relname IN "
                    "('retained_bodies', 'source_origins', 'source_origin_evidence')"
                )
            )
            == 0
        )


def test_allowed_restricted_body_is_atomic_and_only_internal_reader_returns_it(
    session_factory: sessionmaker[Session],
) -> None:
    _, source, policy = _seed_source(session_factory)
    _allow_restricted_body_storage(session_factory, policy)
    command = _command(source)
    prepared = _prepared_with_body(command, source, policy)

    _commit(session_factory, command, prepared)

    body = prepared.retained_body
    assert body is not None
    with session_factory() as session:
        stored = get_retained_body_for_reextraction(
            session,
            source_id=source.source_id,
            source_version_id=body.source_version_id,
            normalization_version=body.normalization_version,
            current_policy_decision_id=policy.policy_decision_id,
        )
        assert stored is not None
        assert stored.body_text == body.body_text
        event = session.scalar(select(OutboxEvent))
        assert event is not None
        assert body.body_text not in str(event.payload)
        assert body.body_text not in str(prepared.result.model_dump(mode="json"))


@pytest.mark.parametrize(
    ("case", "changes"),
    [
        ("blank necessity", {"necessity_reason": "  "}),
        ("zero limit", {"retention_limit_bytes": 0}),
        ("utf8 oversize", {"body_text": "한글", "retention_limit_bytes": 5}),
    ],
)
def test_invalid_retained_body_payload_is_rejected_before_writes(
    session_factory: sessionmaker[Session],
    case: str,
    changes: dict[str, object],
) -> None:
    _, source, policy = _seed_source(session_factory)
    _allow_restricted_body_storage(session_factory, policy)
    command = _command(source)
    prepared = _prepared_with_body(command, source, policy, **changes)

    with pytest.raises(InvalidPreparedCollection):
        _commit(session_factory, command, prepared)

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RetainedBody)) == 0, case


@pytest.mark.parametrize(
    ("permission_axis", "permission"),
    [
        ("collection_permission", "denied"),
        ("collection_permission", "unknown"),
        ("body_storage_permission", "denied"),
        ("body_storage_permission", "unknown"),
    ],
)
def test_denied_or_unknown_collection_or_body_permission_is_rejected(
    session_factory: sessionmaker[Session],
    permission_axis: str,
    permission: str,
) -> None:
    _, source, policy = _seed_source(session_factory)
    _allow_restricted_body_storage(session_factory, policy)
    command = _command(source)
    prepared = _prepared_with_body(command, source, policy)
    with session_factory.begin() as session:
        persisted = session.get(SourcePolicyDecision, policy.policy_decision_id)
        assert persisted is not None
        setattr(persisted, permission_axis, permission)
    event = prepared.events[0]
    envelope = event.payload
    assert isinstance(envelope, SourceEnvelope)
    matching_policy = envelope.policy.model_copy(update={permission_axis: Permission(permission)})
    prepared = replace(
        prepared,
        events=(
            event.model_copy(
                update={"payload": envelope.model_copy(update={"policy": matching_policy})}
            ),
        ),
    )

    with pytest.raises(InvalidPreparedCollection, match="collection and storage"):
        _commit(session_factory, command, prepared)


def test_retained_body_event_policy_must_match_database_decision(
    session_factory: sessionmaker[Session],
) -> None:
    _, source, policy = _seed_source(session_factory)
    _allow_restricted_body_storage(session_factory, policy)
    command = _command(source)
    prepared = _prepared_with_body(command, source, policy)
    event = prepared.events[0]
    envelope = event.payload
    assert isinstance(envelope, SourceEnvelope)
    mismatched_policy = envelope.policy.model_copy(update={"access_class": AccessClass.PUBLIC})
    prepared = replace(
        prepared,
        events=(
            event.model_copy(
                update={"payload": envelope.model_copy(update={"policy": mismatched_policy})}
            ),
        ),
    )

    with pytest.raises(InvalidPreparedCollection, match="envelope policy"):
        _commit(session_factory, command, prepared)


def test_bodyless_source_envelope_policy_must_match_database_decision(
    session_factory: sessionmaker[Session],
) -> None:
    _, source, policy = _seed_source(session_factory)
    _allow_restricted_body_storage(session_factory, policy)
    command = _command(source)
    prepared = _complete_prepared(command, source, policy, aggregate_revision=1)
    event = _version_event_with_body(
        command,
        source,
        prepared,
        policy,
        uuid4(),
    )
    envelope = event.payload
    assert isinstance(envelope, SourceEnvelope)
    mismatched_policy = envelope.policy.model_copy(
        update={"redistribution_permission": Permission.ALLOWED}
    )
    bodyless_envelope = envelope.model_copy(
        update={
            "policy": mismatched_policy,
            "retention_scope": RetentionScope.EXCERPTS_ONLY,
            "normalized_body_ref": None,
        }
    )
    prepared = replace(
        prepared,
        events=(event.model_copy(update={"payload": bodyless_envelope}),),
    )

    with pytest.raises(InvalidPreparedCollection, match="source version policy"):
        _commit(session_factory, command, prepared)

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(SourceVersion)) == 0
        assert session.scalar(select(func.count()).select_from(Evidence)) == 0
        assert session.scalar(select(func.count()).select_from(CollectionAttempt)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0


def test_other_source_policy_is_rejected_for_retained_body(
    session_factory: sessionmaker[Session],
) -> None:
    _, source, policy = _seed_source(session_factory)
    _, _, other_policy = _seed_source(session_factory)
    _allow_restricted_body_storage(session_factory, policy)
    command = _command(source)
    prepared = _prepared_with_body(command, source, policy)
    assert prepared.retained_body is not None
    prepared = replace(
        prepared,
        retained_body=replace(
            prepared.retained_body,
            policy_decision_id=other_policy.policy_decision_id,
        ),
    )

    with pytest.raises(InvalidPreparedCollection, match="wrong policy decision"):
        _commit(session_factory, command, prepared)


def test_natural_body_replay_canonicalizes_event_reference_and_conflicts_on_change(
    session_factory: sessionmaker[Session],
) -> None:
    _, source, policy = _seed_source(session_factory)
    _allow_restricted_body_storage(session_factory, policy)
    first_command = _command(source)
    first = _prepared_with_body(first_command, source, policy)
    _commit(session_factory, first_command, first)
    assert first.retained_body is not None
    first_body = first.retained_body

    replay_policy = SourcePolicyDecision(
        policy_decision_id=uuid4(),
        source_id=source.source_id,
        revision=2,
        official_status="verified",
        access_class="restricted",
        collection_permission="allowed",
        excerpt_storage_permission="allowed",
        body_storage_permission="allowed",
        redistribution_permission="denied",
        evidence_refs=["synthetic://policy-evidence-v2"],
        checked_at=NOW + timedelta(seconds=1),
        policy_version="atomic-policy-v2",
    )
    with session_factory.begin() as session:
        session.add(replay_policy)

    replay_command = _command(source).model_copy(update={"policy_revision": 2})
    replay = _prepared_with_body(
        replay_command,
        source,
        replay_policy,
        retained_at=NOW + timedelta(seconds=1),
        necessity_reason="new collection still needs reproducible extraction",
        retention_policy_version="retention-policy.test.v2",
        retention_limit_bytes=2048,
    )
    _commit(session_factory, replay_command, replay)
    with session_factory() as session:
        stored_body = session.scalar(select(RetainedBody))
        latest_event = session.scalar(
            select(OutboxEvent).order_by(OutboxEvent.aggregate_revision.desc())
        )
        assert stored_body is not None and latest_event is not None
        assert stored_body.necessity_reason == first_body.necessity_reason
        assert stored_body.policy_decision_id == first_body.policy_decision_id
        assert stored_body.retained_at == first_body.retained_at
        assert stored_body.retention_policy_version == first_body.retention_policy_version
        assert stored_body.retention_limit_bytes == first_body.retention_limit_bytes
        assert latest_event.payload["normalized_body_ref"] == str(stored_body.body_id)
        assert session.scalar(select(func.count()).select_from(RetainedBody)) == 1

    conflict_command = _command(source).model_copy(update={"policy_revision": 2})
    conflict = _prepared_with_body(
        conflict_command,
        source,
        replay_policy,
        body_text="different normalized body",
    )
    with pytest.raises(PersistenceConflict, match="retained body"):
        _commit(session_factory, conflict_command, conflict)


def test_source_envelope_cannot_claim_body_without_prepared_retention(
    session_factory: sessionmaker[Session],
) -> None:
    _, source, policy = _seed_source(session_factory)
    _allow_restricted_body_storage(session_factory, policy)
    command = _command(source)
    prepared = _complete_prepared(command, source, policy, aggregate_revision=1)
    claimed_body_id = uuid4()
    event = _version_event_with_body(
        command,
        source,
        prepared,
        policy,
        claimed_body_id,
    )
    prepared = replace(prepared, events=(event,))

    with pytest.raises(InvalidPreparedCollection, match="unprepared retained body"):
        _commit(session_factory, command, prepared)


@pytest.mark.parametrize(
    "case",
    ["missing target", "self target", "no evidence", "duplicate evidence", "foreign evidence"],
)
def test_invalid_source_origin_is_rejected(
    session_factory: sessionmaker[Session],
    case: str,
) -> None:
    _, source, policy = _seed_source(session_factory)
    command = _command(source)
    prepared = _complete_prepared(command, source, policy, aggregate_revision=1)
    origin = _origin(prepared)
    if case == "missing target":
        origin = replace(origin, origin_url=None)
    elif case == "self target":
        origin = replace(origin, origin_source_id=source.source_id, origin_url=None)
    elif case == "no evidence":
        origin = replace(origin, evidence_ids=())
    elif case == "duplicate evidence":
        origin = replace(origin, evidence_ids=(prepared.evidence[0].evidence_id,) * 2)
    else:
        origin = replace(origin, evidence_ids=(uuid4(),))
    prepared = replace(prepared, source_origins=(origin,))

    with pytest.raises(InvalidPreparedCollection):
        _commit(session_factory, command, prepared)


def test_source_origin_natural_replay_is_idempotent_and_one_relation_has_many_evidence(
    session_factory: sessionmaker[Session],
) -> None:
    _, source, policy = _seed_source(session_factory)
    _, other_source, _ = _seed_source(session_factory)
    first_command = _command(source)
    first = _complete_prepared(first_command, source, policy, aggregate_revision=1)
    version = first.source_version
    revision = first.extraction_revision
    assert version is not None and revision is not None
    second_evidence = replace(
        first.evidence[0],
        evidence_id=uuid4(),
        evidence_key="required:1",
        text_excerpt="SQL experience",
        chunk_order=1,
    )
    first = replace(
        first,
        evidence=(*first.evidence, second_evidence),
        extraction_revision=replace(
            revision,
            evidence_ids=(*revision.evidence_ids, second_evidence.evidence_id),
        ),
    )
    first_origin = _origin(
        first,
        evidence_ids=tuple(item.evidence_id for item in first.evidence),
    )
    first = replace(first, source_origins=(first_origin,))
    _commit(session_factory, first_command, first)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(SourceOrigin)) == 1
        assert session.scalar(select(func.count()).select_from(SourceOriginEvidence)) == 2
        origins = list_source_origins(session, source_id=source.source_id, limit=10)
        assert [origin.origin_relation_id for origin in origins] == [
            first_origin.origin_relation_id
        ]
        linked_evidence = list_source_origin_evidence(
            session,
            source_id=source.source_id,
            origin_relation_id=first_origin.origin_relation_id,
            limit=10,
        )
        assert linked_evidence is not None
        assert [item.chunk_order for item in linked_evidence] == [0, 1]
        assert (
            list_source_origin_evidence(
                session,
                source_id=other_source.source_id,
                origin_relation_id=first_origin.origin_relation_id,
                limit=10,
            )
            is None
        )

    replay_command = _command(source)
    replay = _complete_prepared(replay_command, source, policy, aggregate_revision=1)
    replay_revision = replay.extraction_revision
    assert replay_revision is not None
    replay_second_evidence = replace(
        replay.evidence[0],
        evidence_id=uuid4(),
        evidence_key="required:1",
        text_excerpt="SQL experience",
        chunk_order=1,
    )
    replay = replace(
        replay,
        evidence=(*replay.evidence, replay_second_evidence),
        extraction_revision=replace(
            replay_revision,
            evidence_ids=(
                *replay_revision.evidence_ids,
                replay_second_evidence.evidence_id,
            ),
        ),
    )
    replay = replace(
        replay,
        source_origins=(
            _origin(
                replay,
                evidence_ids=tuple(item.evidence_id for item in replay.evidence),
            ),
        ),
    )
    _commit(session_factory, replay_command, replay)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(SourceOrigin)) == 1
        assert session.scalar(select(func.count()).select_from(SourceOriginEvidence)) == 2


def test_private_attempt_delete_preserves_public_retention_and_origin(
    session_factory: sessionmaker[Session],
) -> None:
    _, source, policy = _seed_source(session_factory)
    _allow_restricted_body_storage(session_factory, policy)
    command = _command(source)
    prepared = _prepared_with_body(command, source, policy)
    prepared = replace(prepared, source_origins=(_origin(prepared),))
    _commit(session_factory, command, prepared)

    with session_factory.begin() as session:
        attempt = session.get(CollectionAttempt, prepared.attempt_id)
        assert attempt is not None
        session.delete(attempt)
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RetainedBody)) == 1
        assert session.scalar(select(func.count()).select_from(SourceOrigin)) == 1
        assert session.scalar(select(func.count()).select_from(SourceOriginEvidence)) == 1


def test_late_outbox_conflict_rolls_back_retention_and_origin(
    session_factory: sessionmaker[Session],
) -> None:
    _, source, policy = _seed_source(session_factory)
    _allow_restricted_body_storage(session_factory, policy)
    first_command = _command(source)
    first = _complete_prepared(first_command, source, policy, aggregate_revision=1)
    _commit(session_factory, first_command, first)
    conflicting_event_id = first.events[0].event_id

    conflict_command = _command(source)
    conflict = _prepared_with_body(
        conflict_command,
        source,
        policy,
        event_id=conflicting_event_id,
    )
    conflict = replace(conflict, source_origins=(_origin(conflict),))
    with pytest.raises(PersistenceConflict, match="outbox event"):
        _commit(session_factory, conflict_command, conflict)

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RetainedBody)) == 0
        assert session.scalar(select(func.count()).select_from(SourceOrigin)) == 0
        assert session.scalar(select(func.count()).select_from(SourceOriginEvidence)) == 0


def test_stale_authority_rolls_back_retention_and_origin(
    session_factory: sessionmaker[Session],
) -> None:
    _, source, policy = _seed_source(session_factory)
    _allow_restricted_body_storage(session_factory, policy)
    command = _command(source)
    prepared = _prepared_with_body(command, source, policy)
    prepared = replace(prepared, source_origins=(_origin(prepared),))

    def stale_lock(
        session: Session,
        *,
        command,
        attempt_id: UUID,
    ):
        assert session.in_transaction()
        return replace(_grant(command, attempt_id, pointer_eligible=True), execution_fence="stale")

    with pytest.raises(StaleExecution):
        commit_prepared_collection(
            session_factory,
            command=command,
            prepared=prepared,
            lock_authority=stale_lock,
        )
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(RetainedBody)) == 0
        assert session.scalar(select(func.count()).select_from(SourceOrigin)) == 0


def test_current_denied_policy_hides_but_does_not_delete_retained_body(
    session_factory: sessionmaker[Session],
) -> None:
    _, source, policy = _seed_source(session_factory)
    _allow_restricted_body_storage(session_factory, policy)
    command = _command(source)
    prepared = _prepared_with_body(command, source, policy)
    _commit(session_factory, command, prepared)
    body = prepared.retained_body
    assert body is not None
    denied_policy = SourcePolicyDecision(
        policy_decision_id=uuid4(),
        source_id=source.source_id,
        revision=2,
        official_status="verified",
        access_class="restricted",
        collection_permission="allowed",
        excerpt_storage_permission="allowed",
        body_storage_permission="denied",
        redistribution_permission="allowed",
        evidence_refs=["synthetic://policy-evidence-v2"],
        checked_at=NOW,
        policy_version="atomic-policy-v2",
    )
    with session_factory.begin() as session:
        session.add(denied_policy)

    with session_factory() as session:
        assert (
            get_retained_body_for_reextraction(
                session,
                source_id=source.source_id,
                source_version_id=body.source_version_id,
                normalization_version=body.normalization_version,
                current_policy_decision_id=denied_policy.policy_decision_id,
            )
            is None
        )
        assert (
            get_retained_body_for_reextraction(
                session,
                source_id=source.source_id,
                source_version_id=body.source_version_id,
                normalization_version=body.normalization_version,
                current_policy_decision_id=policy.policy_decision_id,
            )
            is None
        )
        assert session.get(RetainedBody, body.body_id) is not None
