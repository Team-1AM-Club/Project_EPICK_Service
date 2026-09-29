"""Collection FINALIZE pointer promotion against explicitly approved PostgreSQL."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from epick_engine.source_collection.commit_gate_contracts import (
    CommitGateCommand,
    parse_commit_gate_command,
    staged_result_digest,
)
from epick_engine.source_collection.commit_gate_store import (
    CommitGateRejected,
    PrivateCommitGateAck,
    PrivateCommitStage,
    PrivateStagedOutbox,
    read_finalized_result,
)
from epick_engine.source_collection.commit_gate_store import (
    stage_private_result as _stage_private_result,
)
from epick_engine.source_collection.contracts import CollectionCommand, CollectionResult
from epick_engine.source_collection.persistence import (
    Base,
    CollectionRuntimeAttempt,
    Source,
    SourceObservation,
    SourcePolicyDecision,
    SourceVersion,
    append_source_policy_decision,
)
from epick_engine.source_collection.persistence import (
    commit_collection_candidate as _commit_collection_candidate,
)
from epick_engine.source_collection.private_deletion_v2 import PrivateDeletionScope
from epick_engine.source_collection.private_scope import (
    PrivateGateAuthority,
    PrivateWriteAuthorityDecision,
    PrivateWriteScope,
)
from epick_engine.source_collection.source_runtime_gate import (
    apply_collection_commit_gate as _apply_collection_commit_gate,
)
from epick_engine.source_collection.source_runtime_store import (
    claim_collection_attempt,
    reserve_collection_attempt,
)
from epick_engine.source_collection.w1_private_authority_contracts import GateAuthorityResponse

pytestmark = pytest.mark.approved_postgres
NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _trusted_scope(command: CollectionCommand | CommitGateCommand) -> PrivateWriteScope:
    return PrivateWriteScope(
        PrivateWriteAuthorityDecision(
            owner_user_id=command.authenticated_owner_ref,
            owner_deletion_epoch=command.owner_deletion_epoch,
            scope=PrivateDeletionScope(kind="ACCOUNT", project_id=None),
            authority_ref="w1:test-finalize-authority",
            command_id=command.command_id,
            job_id=command.job_id,
        )
    )


def _trusted_gate_authority(gate: CommitGateCommand) -> PrivateGateAuthority:
    response = GateAuthorityResponse.model_validate(
        {
            "schema_version": "w1.private.w2-gate-authority.v1",
            "owner_user_id": str(gate.authenticated_owner_ref),
            "owner_deletion_epoch": gate.owner_deletion_epoch,
            "command_id": str(gate.command_id),
            "job_id": str(gate.job_id),
            "execution_fence": gate.execution_fence,
            "scope": {"type": "ACCOUNT"},
            "operation_id": str(gate.operation_id),
            "operation_revision": gate.operation_revision,
            "action": gate.action,
            "phase": "APPLY",
            "result_digest": gate.result_digest,
            "purge_owner_deletion_epoch": gate.purge_owner_deletion_epoch,
            "authority_ref": "w1:test-finalize-gate-authority",
        }
    )
    return PrivateGateAuthority.from_w1_response(response)


def commit_collection_candidate(session_factory, dispatch, *args, **kwargs):
    kwargs.setdefault("private_scope", _trusted_scope(dispatch.payload))
    return _commit_collection_candidate(session_factory, dispatch, *args, **kwargs)


def stage_private_result(session, command, result, **kwargs):
    kwargs.setdefault("private_scope", _trusted_scope(command))
    return _stage_private_result(session, command, result, **kwargs)


def apply_collection_commit_gate(session, gate, **kwargs):
    kwargs.setdefault("private_gate_authority", _trusted_gate_authority(gate))
    return _apply_collection_commit_gate(session, gate, **kwargs)


@pytest.fixture
def database_engine(approved_postgres_url) -> Iterator[Engine]:
    admin = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema = f"epick_w2_collection_finalize_{uuid4().hex}"
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


def _gate(
    command: CollectionCommand,
    result: CollectionResult,
    action: str,
    *,
    operation_id: UUID,
    revision: int,
    epoch: int | None = None,
) -> CommitGateCommand:
    raw = json.loads(
        (FIXTURES / f"w1_private_contract/private-w2-commit-gate-{action.lower()}.json").read_text(
            encoding="utf-8"
        )
    )
    raw.update(
        message_id=str(uuid4()),
        operation_id=str(operation_id),
        operation_revision=revision,
        command_id=str(command.command_id),
        job_id=str(command.job_id),
        authenticated_owner_ref=str(command.authenticated_owner_ref),
        execution_fence=int(command.execution_fence),
        owner_deletion_epoch=command.owner_deletion_epoch,
        result_digest=staged_result_digest(command, result),
    )
    if action == "PURGE":
        raw["purge_owner_deletion_epoch"] = epoch or command.owner_deletion_epoch + 1
    return parse_commit_gate_command(raw)


def _persisted_collection_stage(session_factory: sessionmaker[Session]):
    from tests.integration.source_collection.test_collection_runtime_storage import (
        _claimed_candidate_inputs,
    )

    dispatch, effective_command, prepared, claim_token = _claimed_candidate_inputs(session_factory)
    proposal = commit_collection_candidate(
        session_factory,
        dispatch,
        effective_command,
        prepared,
        claim_token=claim_token,
        staged_message_id=uuid4(),
        occurred_at=NOW,
    )
    command = dispatch.payload
    operation = uuid4()
    with session_factory.begin() as session:
        apply_collection_commit_gate(
            session,
            _gate(command, proposal.result, "PREPARE", operation_id=operation, revision=1),
            ack_message_id=uuid4(),
            occurred_at=NOW,
        )
    return command, proposal.result, operation, prepared


def _snapshot(session: Session, command: CollectionCommand) -> tuple:
    candidate = session.get(CollectionRuntimeAttempt, command.command_id)
    assert candidate is not None
    stage = session.get(PrivateCommitStage, command.command_id)
    source = session.get(Source, candidate.source_id)
    assert stage is not None and source is not None
    ack_count = session.scalar(
        select(func.count())
        .select_from(PrivateCommitGateAck)
        .where(PrivateCommitGateAck.command_id == command.command_id)
    )
    return candidate, stage, source, int(ack_count or 0)


def _finalize(
    session_factory: sessionmaker[Session],
    command: CollectionCommand,
    result: CollectionResult,
    operation: UUID,
) -> CommitGateCommand:
    gate = _gate(command, result, "FINALIZE", operation_id=operation, revision=2)
    with session_factory.begin() as session:
        apply_collection_commit_gate(session, gate, ack_message_id=uuid4(), occurred_at=NOW)
    return gate


def test_prepare_keeps_collection_candidate_and_source_pointer_unchanged(session_factory) -> None:
    command, _result, _operation, _prepared = _persisted_collection_stage(session_factory)

    with session_factory() as session:
        candidate, stage, source, ack_count = _snapshot(session, command)
        assert candidate.state == "PERSISTED"
        assert stage.state == "PREPARED"
        assert source.latest_observation_id is None
        assert source.current_source_version_id is None
        assert source.last_promoted_observation_order == 0
        assert ack_count == 1


def test_finalize_promotes_verified_public_candidate_and_clears_claim(session_factory) -> None:
    command, result, operation, prepared = _persisted_collection_stage(session_factory)

    _finalize(session_factory, command, result, operation)

    with session_factory() as session:
        candidate, stage, source, ack_count = _snapshot(session, command)
        assert candidate.state == "FINALIZED"
        assert candidate.claim_token is None
        assert candidate.claim_expires_at is None
        assert source.latest_observation_id == prepared.observation.snapshot.observation_id
        assert source.current_source_version_id == prepared.source_version.source_version_id
        assert source.last_promoted_observation_order == candidate.observation_order
        assert source.first_collected_at == prepared.observation.snapshot.observed_at
        assert source.last_collected_at == prepared.observation.snapshot.observed_at
        assert stage.state == "FINALIZED"
        assert ack_count == 2


def test_exact_finalize_replay_does_not_repromote_or_create_another_ack(session_factory) -> None:
    command, result, operation, _prepared = _persisted_collection_stage(session_factory)
    gate = _finalize(session_factory, command, result, operation)

    with session_factory() as session:
        _candidate, _stage, source, ack_count = _snapshot(session, command)
        before = (
            source.latest_observation_id,
            source.current_source_version_id,
            source.first_collected_at,
            source.last_collected_at,
            source.last_promoted_observation_order,
            ack_count,
        )
    with session_factory.begin() as session:
        replay = apply_collection_commit_gate(
            session,
            gate,
            ack_message_id=uuid4(),
            occurred_at=NOW,
        )
    with session_factory() as session:
        candidate, stage, source, ack_count = _snapshot(session, command)
        assert replay.command_id == command.command_id
        assert candidate.state == "FINALIZED"
        assert stage.state == "FINALIZED"
        assert (
            source.latest_observation_id,
            source.current_source_version_id,
            source.first_collected_at,
            source.last_collected_at,
            source.last_promoted_observation_order,
            ack_count,
        ) == before


def test_finalize_with_stale_cached_source_does_not_rewind_a_newer_order(session_factory) -> None:
    command, result, operation, _prepared = _persisted_collection_stage(session_factory)
    gate = _gate(command, result, "FINALIZE", operation_id=operation, revision=2)
    stale_session = session_factory()
    try:
        stale_source = stale_session.get(Source, command.source_id)
        assert stale_source is not None and stale_source.last_promoted_observation_order == 0
        with session_factory.begin() as other_session:
            candidate = other_session.get(CollectionRuntimeAttempt, command.command_id)
            source = other_session.get(Source, command.source_id)
            assert candidate is not None and source is not None
            source.last_promoted_observation_order = candidate.observation_order
        apply_collection_commit_gate(stale_session, gate, ack_message_id=uuid4(), occurred_at=NOW)
        stale_session.commit()
    finally:
        stale_session.close()

    with session_factory() as session:
        candidate, stage, source, _ack_count = _snapshot(session, command)
        assert candidate.state == "FINALIZED"
        assert stage.state == "FINALIZED"
        assert source.last_promoted_observation_order == candidate.observation_order
        assert source.latest_observation_id is None
        assert source.current_source_version_id is None


def test_finalize_with_advanced_policy_finalizes_without_pointer_promotion(session_factory) -> None:
    command, result, operation, _prepared = _persisted_collection_stage(session_factory)
    with session_factory.begin() as session:
        candidate = session.get(CollectionRuntimeAttempt, command.command_id)
        assert candidate is not None
        session.add(
            SourcePolicyDecision(
                policy_decision_id=uuid4(),
                source_id=candidate.source_id,
                revision=candidate.effective_policy_revision + 1,
                official_status="verified",
                access_class="public",
                collection_permission="allowed",
                excerpt_storage_permission="allowed",
                body_storage_permission="denied",
                redistribution_permission="unknown",
                evidence_refs=["synthetic:advanced-policy"],
                checked_at=NOW,
                policy_version="advanced-policy",
            )
        )

    _finalize(session_factory, command, result, operation)

    with session_factory() as session:
        candidate, stage, source, ack_count = _snapshot(session, command)
        assert candidate.state == "FINALIZED"
        assert stage.state == "FINALIZED"
        assert source.latest_observation_id is None
        assert source.current_source_version_id is None
        assert ack_count == 2


def test_policy_append_committing_first_blocks_finalize_then_prevents_promotion(
    session_factory,
) -> None:
    """Both writers must serialize through the Source policy scope."""

    command, result, operation, _prepared = _persisted_collection_stage(session_factory)
    with session_factory() as session:
        candidate = session.get(CollectionRuntimeAttempt, command.command_id)
        assert candidate is not None
        next_revision = candidate.effective_policy_revision + 1

    append_lock_held = Event()
    allow_append_commit = Event()
    append_committed = Event()
    finalize_started = Event()
    finalize_finished = Event()
    errors: list[BaseException] = []
    outcome: dict[str, CommitGateCommand] = {}

    def append_policy() -> None:
        try:
            with session_factory.begin() as session:
                append_source_policy_decision(
                    session,
                    SourcePolicyDecision(
                        policy_decision_id=uuid4(),
                        source_id=command.source_id,
                        revision=next_revision,
                        official_status="verified",
                        access_class="public",
                        collection_permission="allowed",
                        excerpt_storage_permission="allowed",
                        body_storage_permission="denied",
                        redistribution_permission="unknown",
                        evidence_refs=["synthetic:race-policy"],
                        checked_at=NOW,
                        policy_version="race-policy",
                    ),
                )
                append_lock_held.set()
                if not allow_append_commit.wait(timeout=10):
                    raise TimeoutError("test did not release policy append transaction")
            append_committed.set()
        except BaseException as error:
            errors.append(error)
            append_lock_held.set()

    def finalize() -> None:
        finalize_started.set()
        try:
            with session_factory.begin() as session:
                outcome["ack"] = apply_collection_commit_gate(
                    session,
                    _gate(command, result, "FINALIZE", operation_id=operation, revision=2),
                    ack_message_id=uuid4(),
                    occurred_at=NOW,
                )
        except BaseException as error:
            errors.append(error)
        finally:
            finalize_finished.set()

    append_thread = Thread(target=append_policy)
    finalize_thread = Thread(target=finalize)
    append_thread.start()
    try:
        assert append_lock_held.wait(timeout=10)
        assert not errors
        finalize_thread.start()
        assert finalize_started.wait(timeout=10)
        assert not finalize_finished.wait(timeout=0.3)

        allow_append_commit.set()
        append_thread.join(timeout=10)
        finalize_thread.join(timeout=10)
    finally:
        allow_append_commit.set()
        append_thread.join(timeout=10)
        finalize_thread.join(timeout=10)

    assert not append_thread.is_alive()
    assert not finalize_thread.is_alive()
    assert not errors
    assert append_committed.is_set()
    assert outcome["ack"].command_id == command.command_id

    with session_factory() as session:
        candidate, stage, source, ack_count = _snapshot(session, command)
        current_revision = session.scalar(
            select(func.max(SourcePolicyDecision.revision)).where(
                SourcePolicyDecision.source_id == command.source_id
            )
        )
        assert current_revision == next_revision
        assert candidate.state == "FINALIZED"
        assert stage.state == "FINALIZED"
        assert source.latest_observation_id is None
        assert source.current_source_version_id is None
        assert source.last_promoted_observation_order == 0
        assert ack_count == 2


def test_finalize_rejects_non_finalize_gate_source_without_private_ack(session_factory) -> None:
    command, result, operation, _prepared = _persisted_collection_stage(session_factory)
    with session_factory.begin() as session:
        candidate = session.get(CollectionRuntimeAttempt, command.command_id)
        source = session.get(Source, command.source_id)
        assert candidate is not None and source is not None
        source.pointer_update_mode = "LEGACY_SAME_DB"

    with session_factory() as session:
        with pytest.raises(CommitGateRejected, match="FINALIZE_GATE"):
            apply_collection_commit_gate(
                session,
                _gate(command, result, "FINALIZE", operation_id=operation, revision=2),
                ack_message_id=uuid4(),
                occurred_at=NOW,
            )
        session.commit()

    with session_factory() as session:
        candidate, stage, source, ack_count = _snapshot(session, command)
        assert candidate.state == "PERSISTED"
        assert stage.state == "PREPARED"
        assert source.latest_observation_id is None
        assert ack_count == 1


def test_finalize_rejects_missing_candidate_observation_without_private_ack(
    session_factory,
) -> None:
    command, result, operation, _prepared = _persisted_collection_stage(session_factory)
    with session_factory.begin() as session:
        candidate = session.get(CollectionRuntimeAttempt, command.command_id)
        assert candidate is not None
        candidate.observation_id = None

    with session_factory() as session:
        with pytest.raises(CommitGateRejected, match="candidate observation is unavailable"):
            apply_collection_commit_gate(
                session,
                _gate(command, result, "FINALIZE", operation_id=operation, revision=2),
                ack_message_id=uuid4(),
                occurred_at=NOW,
            )
        session.commit()

    with session_factory() as session:
        candidate, stage, source, ack_count = _snapshot(session, command)
        assert candidate.state == "PERSISTED"
        assert stage.state == "PREPARED"
        assert source.latest_observation_id is None
        assert source.current_source_version_id is None
        assert ack_count == 1


@pytest.mark.parametrize("non_integer", [1.0, True], ids=["integral_float", "boolean"])
def test_finalize_rejects_coercible_staged_integer_and_rolls_back_gate_state(
    session_factory,
    non_integer: float | bool,
) -> None:
    """JSONB must not turn float/bool into a valid staged command integer during FINALIZE."""

    command, result, operation, _prepared = _persisted_collection_stage(session_factory)
    with session_factory.begin() as session:
        outbox = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        assert outbox is not None and outbox.payload is not None
        payload = json.loads(json.dumps(outbox.payload))
        payload["command"]["input_version"] = non_integer
        payload["result"]["input_version"] = non_integer
        # Keep the original durable hashes.  A coercing parser would normalize
        # these values back to the original canonical integer and falsely pass
        # both equality and hash checks.
        outbox.payload = payload
        flag_modified(outbox, "payload")

    with session_factory() as session:
        persisted_outbox = session.scalar(
            select(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id == command.command_id)
        )
        assert persisted_outbox is not None and persisted_outbox.payload is not None
        assert type(persisted_outbox.payload["command"]["input_version"]) is type(non_integer)
        assert type(persisted_outbox.payload["result"]["input_version"]) is type(non_integer)

    with session_factory() as session:
        with pytest.raises(CommitGateRejected, match="invalid collection staged proposal payload"):
            apply_collection_commit_gate(
                session,
                _gate(command, result, "FINALIZE", operation_id=operation, revision=2),
                ack_message_id=uuid4(),
                occurred_at=NOW,
            )
        session.commit()

    with session_factory() as session:
        candidate, stage, source, ack_count = _snapshot(session, command)
        assert candidate.state == "PERSISTED"
        assert stage.state == "PREPARED"
        assert source.latest_observation_id is None
        assert source.current_source_version_id is None
        assert ack_count == 1


def test_post_finalize_purge_invalidates_candidate_without_deleting_public_pointer(
    session_factory,
) -> None:
    command, result, operation, _prepared = _persisted_collection_stage(session_factory)
    _finalize(session_factory, command, result, operation)

    with session_factory.begin() as session:
        apply_collection_commit_gate(
            session,
            _gate(
                command,
                result,
                "PURGE",
                operation_id=operation,
                revision=3,
                epoch=command.owner_deletion_epoch + 1,
            ),
            ack_message_id=uuid4(),
            occurred_at=NOW,
        )

    with session_factory() as session:
        candidate, stage, source, ack_count = _snapshot(session, command)
        assert candidate.state == "INVALIDATED"
        assert candidate.observation_id is None
        assert candidate.source_version_id is None
        assert stage.state == "PURGED"
        assert source.latest_observation_id is not None
        assert source.current_source_version_id is not None
        assert session.get(SourceObservation, source.latest_observation_id) is not None
        assert session.get(SourceVersion, source.current_source_version_id) is not None
        assert ack_count == 3


def test_abort_before_finalize_invalidates_candidate_and_rejects_later_finalize(
    session_factory,
) -> None:
    command, result, operation, _prepared = _persisted_collection_stage(session_factory)
    with session_factory.begin() as session:
        apply_collection_commit_gate(
            session,
            _gate(command, result, "ABORT", operation_id=operation, revision=2),
            ack_message_id=uuid4(),
            occurred_at=NOW,
        )

    with session_factory() as session:
        candidate, stage, source, ack_count = _snapshot(session, command)
        assert candidate.state == "INVALIDATED"
        assert candidate.observation_id is None
        assert candidate.source_version_id is None
        assert stage.state == "ABORTED"
        assert source.latest_observation_id is None
        assert source.current_source_version_id is None
        assert ack_count == 2
        with pytest.raises(CommitGateRejected):
            apply_collection_commit_gate(
                session,
                _gate(command, result, "FINALIZE", operation_id=operation, revision=3),
                ack_message_id=uuid4(),
                occurred_at=NOW,
            )
        session.commit()


def test_gate_first_abort_leaves_collection_tombstone_without_candidate(session_factory) -> None:
    from tests.integration.source_collection.test_private_commit_gate import _pair

    command, result = _pair()
    with session_factory.begin() as session:
        apply_collection_commit_gate(
            session,
            _gate(command, result, "ABORT", operation_id=uuid4(), revision=1),
            ack_message_id=uuid4(),
            occurred_at=NOW,
        )

    with session_factory() as session:
        stage = session.get(PrivateCommitStage, command.command_id)
        assert stage is not None
        assert stage.stage_kind == "COLLECTION"
        assert stage.state == "ABORTED"
        assert session.get(CollectionRuntimeAttempt, command.command_id) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("official_status", "unverified"),
        ("access_class", "restricted"),
        ("collection_permission", "denied"),
        ("excerpt_storage_permission", "denied"),
    ],
)
def test_finalize_promotes_observation_but_not_current_version_when_required_policy_axis_denies(
    session_factory,
    field: str,
    value: str,
) -> None:
    command, result, operation, prepared = _persisted_collection_stage(session_factory)
    with session_factory.begin() as session:
        candidate = session.get(CollectionRuntimeAttempt, command.command_id)
        assert candidate is not None and candidate.source_version_id is not None
        version = session.get(SourceVersion, candidate.source_version_id)
        assert version is not None
        policy = session.get(SourcePolicyDecision, version.policy_decision_id)
        assert policy is not None
        setattr(policy, field, value)

    _finalize(session_factory, command, result, operation)

    with session_factory() as session:
        candidate, stage, source, _ack_count = _snapshot(session, command)
        assert candidate.state == "FINALIZED"
        assert stage.state == "FINALIZED"
        assert source.latest_observation_id == prepared.observation.snapshot.observation_id
        assert source.current_source_version_id is None
        assert source.first_collected_at is None
        assert source.last_collected_at is None


def test_private_only_stage_remains_private_when_using_collection_gate_wrapper(
    session_factory,
) -> None:
    from tests.integration.source_collection.test_private_commit_gate import _pair

    command, result = _pair()
    operation = uuid4()
    with session_factory.begin() as session:
        stage_private_result(session, command, result, message_id=uuid4(), occurred_at=NOW)
        apply_collection_commit_gate(
            session,
            _gate(command, result, "PREPARE", operation_id=operation, revision=1),
            ack_message_id=uuid4(),
            occurred_at=NOW,
        )
        apply_collection_commit_gate(
            session,
            _gate(command, result, "FINALIZE", operation_id=operation, revision=2),
            ack_message_id=uuid4(),
            occurred_at=NOW,
        )

    with session_factory() as session:
        stage = session.get(PrivateCommitStage, command.command_id)
        assert stage is not None
        assert stage.stage_kind == "PRIVATE_ONLY"
        assert stage.state == "FINALIZED"
        assert (
            read_finalized_result(
                session,
                owner_ref=command.authenticated_owner_ref,
                command_id=command.command_id,
            )
            == result
        )


def test_failure_finalize_advances_latest_observation_without_replacing_current_version(
    session_factory,
) -> None:
    from tests.integration.source_collection.test_atomic_persistence import _failure_prepared
    from tests.integration.source_collection.test_collection_runtime_storage import _dispatch

    command, result, operation, _prepared = _persisted_collection_stage(session_factory)
    _finalize(session_factory, command, result, operation)
    with session_factory() as session:
        _candidate, _stage, source, _ack_count = _snapshot(session, command)
        policy = session.scalar(
            select(SourcePolicyDecision).where(
                SourcePolicyDecision.source_id == source.source_id,
                SourcePolicyDecision.revision == 3,
            )
        )
        assert policy is not None
        pointer_before = (
            source.current_source_version_id,
            source.first_collected_at,
            source.last_collected_at,
        )

    dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=source.company_id,
        source_id=source.source_id,
    )
    effective_command = dispatch.payload.model_copy(update={"policy_revision": policy.revision})
    private_scope = _trusted_scope(dispatch.payload)
    with session_factory.begin() as session:
        reserved = reserve_collection_attempt(
            session,
            dispatch,
            effective_policy_revision=policy.revision,
            now=NOW,
            uuid_factory=uuid4,
            private_scope=private_scope,
        )
    claim_token = uuid4()
    claim_collection_attempt(
        session_factory,
        dispatch.payload.command_id,
        claim_token=claim_token,
        lease_seconds=30,
        expected_dispatch_digest=reserved.dispatch_digest,
        private_scope=private_scope,
    )
    failure_prepared = _failure_prepared(
        effective_command,
        source,
        policy,
        attempt_id=reserved.attempt_id,
        aggregate_revision=999,
    )
    failure_proposal = commit_collection_candidate(
        session_factory,
        dispatch,
        effective_command,
        failure_prepared,
        claim_token=claim_token,
        staged_message_id=uuid4(),
        occurred_at=NOW,
    )
    failure_operation = uuid4()
    with session_factory.begin() as session:
        apply_collection_commit_gate(
            session,
            _gate(
                dispatch.payload,
                failure_proposal.result,
                "PREPARE",
                operation_id=failure_operation,
                revision=1,
            ),
            ack_message_id=uuid4(),
            occurred_at=NOW,
        )
    _finalize(session_factory, dispatch.payload, failure_proposal.result, failure_operation)

    with session_factory() as session:
        candidate, stage, persisted, _ack_count = _snapshot(session, dispatch.payload)
        assert candidate.state == "FINALIZED"
        assert candidate.source_version_id is None
        assert stage.state == "FINALIZED"
        assert (
            persisted.latest_observation_id == failure_prepared.observation.snapshot.observation_id
        )
        assert (
            persisted.current_source_version_id,
            persisted.first_collected_at,
            persisted.last_collected_at,
        ) == pointer_before
