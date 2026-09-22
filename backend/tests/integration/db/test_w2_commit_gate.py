from __future__ import annotations

import json
from threading import Barrier, Event, Thread
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, event, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models.deletion import DeletionTarget
from app.models.jobs import Job, OutboxMessage
from app.models.lifecycle_operations import JobCheckpoint
from app.models.w2_commit_operations import W2CommitOperation
from app.runtime.outbox_relay import OutboxRelay, QueueUrlRegistry
from app.runtime.sqs import InMemorySqsPort
from app.runtime.w2_commit_gate_worker import (
    W2CommitGateAck,
    W2CommitGateAckWorker,
    W2CommitGateRecoveryWorker,
)
from app.runtime.workers import CollectionResultWorker, CommitReadyCollectionResult
from app.services.deletion import DeletionOrchestrationService
from app.services.job_actions import JobActionService
from app.services.w2_commit_gate import (
    LOCK_ORDER,
    W2CommitGateCurrentnessError,
    W2CommitGateService,
)

W2_COMMIT_GATE_QUEUE_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/w2-command"

pytestmark = pytest.mark.w1_isolated_commit_gate


class _OwnerScopedSession(Session):
    """Test-only worker session that behaves like the owner RLS context."""


@event.listens_for(_OwnerScopedSession, "after_begin")
def _apply_owner_scope(session: Session, transaction: object, connection: object) -> None:
    del transaction
    owner_user_id = session.info.get("owner_user_id")
    if owner_user_id is not None:
        connection.execute(
            text("SELECT set_config('app.current_user_id', :owner_id, true)"),
            {"owner_id": owner_user_id},
        )


def _owner_scoped_factory(migrated_engine: Engine, owner_id: UUID) -> sessionmaker:
    return sessionmaker(
        bind=migrated_engine,
        class_=_OwnerScopedSession,
        autoflush=False,
        expire_on_commit=False,
        info={"owner_user_id": str(owner_id)},
    )


@pytest.fixture(autouse=True)
def _clean_w2_commit_gate_tables(migrated_engine: Engine) -> None:
    """Keep the fake-gate stories independent of prior database test rows."""

    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))


def _seed_private_command(migrated_engine: Engine) -> tuple[UUID, UUID, UUID]:
    owner_id = uuid4()
    job_id = uuid4()
    command_id = uuid4()
    with migrated_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.current_user_id', :owner_id, true)"),
            {"owner_id": str(owner_id)},
        )
        connection.execute(
            text(
                "INSERT INTO users (id, display_name, locale, timezone) "
                "VALUES (:id, 'W2 commit gate owner', 'ko-KR', 'Asia/Seoul')"
            ),
            {"id": owner_id},
        )
        connection.execute(
            text(
                "INSERT INTO jobs (id, owner_user_id, job_type, owner_deletion_epoch) "
                "VALUES (:id, :owner_id, 'COLLECT_COMPANY_SOURCE', 0)"
            ),
            {"id": job_id, "owner_id": owner_id},
        )
        connection.execute(
            text(
                "INSERT INTO job_commands ("
                "id, job_id, owner_user_id, command_type, command_schema_version, "
                "command_sequence, execution_fence, owner_deletion_epoch, payload"
                ") VALUES ("
                ":id, :job_id, :owner_id, 'COLLECT_COMPANY_SOURCE', '1.0', 1, 1, 0, "
                "CAST(:payload AS jsonb)"
                ")"
            ),
            {
                "id": command_id,
                "job_id": job_id,
                "owner_id": owner_id,
                "payload": json.dumps({"command_type": "COLLECT_COMPANY_SOURCE"}),
            },
        )
    return owner_id, job_id, command_id


def _seed_active_w2_command(migrated_engine: Engine) -> tuple[UUID, UUID, UUID, UUID]:
    """Create the minimal current W2 child command and active lease for Phase 3."""

    owner_id = uuid4()
    job_id = uuid4()
    command_id = uuid4()
    lease_id = uuid4()
    with migrated_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.current_user_id', :owner_id, true)"),
            {"owner_id": str(owner_id)},
        )
        connection.execute(
            text(
                "INSERT INTO users (id, display_name, locale, timezone) "
                "VALUES (:id, 'W2 gate runtime owner', 'ko-KR', 'Asia/Seoul')"
            ),
            {"id": owner_id},
        )
        connection.execute(
            text(
                "INSERT INTO jobs ("
                "id, owner_user_id, job_type, status, dispatch_status, active_lease_id, "
                "owner_deletion_epoch"
                ") VALUES ("
                ":id, :owner_id, 'SOURCE_COLLECTION', 'RUNNING', 'CLAIMED', :lease_id, 0"
                ")"
            ),
            {"id": job_id, "owner_id": owner_id, "lease_id": lease_id},
        )
        connection.execute(
            text(
                "INSERT INTO owner_execution_slots ("
                "owner_user_id, slot_no, job_id, lease_id"
                ") VALUES (:owner_id, 1, :job_id, :lease_id)"
            ),
            {"owner_id": owner_id, "job_id": job_id, "lease_id": lease_id},
        )
        connection.execute(
            text(
                "INSERT INTO job_execution_leases ("
                "id, job_id, owner_user_id, slot_no, execution_fence, owner_deletion_epoch"
                ") VALUES (:id, :job_id, :owner_id, 1, 1, 0)"
            ),
            {"id": lease_id, "job_id": job_id, "owner_id": owner_id},
        )
        connection.execute(
            text(
                "INSERT INTO job_commands ("
                "id, job_id, owner_user_id, command_type, command_schema_version, "
                "command_sequence, execution_fence, owner_deletion_epoch, payload, status"
                ") VALUES ("
                ":id, :job_id, :owner_id, 'W2_SOURCE_COLLECTION', '1.0', 1, 1, 0, "
                "CAST(:payload AS jsonb), 'ENQUEUED'"
                ")"
            ),
            {
                "id": command_id,
                "job_id": job_id,
                "owner_id": owner_id,
                "payload": json.dumps({"command_type": "W2_SOURCE_COLLECTION"}),
            },
        )
    return owner_id, job_id, command_id, lease_id


def _commit_ready(
    *,
    owner_id: UUID,
    job_id: UUID,
    command_id: UUID,
    lease_id: UUID,
    message_id: UUID | None = None,
    execution_fence: int = 1,
    owner_deletion_epoch: int = 0,
    result_digest: str = "sha256:" + "d" * 64,
) -> CommitReadyCollectionResult:
    return CommitReadyCollectionResult(
        message_id=message_id or uuid4(),
        owner_user_id=owner_id,
        job_id=job_id,
        command_id=command_id,
        execution_lease_id=lease_id,
        execution_fence=execution_fence,
        owner_deletion_epoch=owner_deletion_epoch,
        result_digest=result_digest,
    )


def _insert_operation(
    connection: object,
    *,
    owner_id: UUID,
    job_id: UUID,
    command_id: UUID,
    result_digest: str = "sha256:" + "a" * 64,
    operation_revision: int = 1,
    state: str = "PREPARE_PENDING",
    purge_owner_deletion_epoch: int | None = None,
) -> UUID:
    operation_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO w2_commit_operations ("
            "id, command_id, job_id, owner_user_id, execution_fence, "
            "owner_deletion_epoch, purge_owner_deletion_epoch, result_digest, "
            "operation_revision, state"
            ") VALUES ("
            ":id, :command_id, :job_id, :owner_id, 1, 0, :purge_owner_deletion_epoch, "
            ":result_digest, :operation_revision, :state"
            ")"
        ),
        {
            "id": operation_id,
            "command_id": command_id,
            "job_id": job_id,
            "owner_id": owner_id,
            "purge_owner_deletion_epoch": purge_owner_deletion_epoch,
            "result_digest": result_digest,
            "operation_revision": operation_revision,
            "state": state,
        },
    )
    return operation_id


@pytest.mark.postgres
def test_w2_commit_operation_schema_has_required_inventory(migrated_engine: Engine) -> None:
    inspector = inspect(migrated_engine)
    assert "w2_commit_operations" in inspector.get_table_names()

    columns = {column["name"] for column in inspector.get_columns("w2_commit_operations")}
    assert {
        "id",
        "command_id",
        "job_id",
        "owner_user_id",
        "execution_fence",
        "owner_deletion_epoch",
        "purge_owner_deletion_epoch",
        "result_digest",
        "operation_revision",
        "state",
    } <= columns

    unique_constraints = inspector.get_unique_constraints("w2_commit_operations")
    assert any(constraint["column_names"] == ["command_id"] for constraint in unique_constraints)

    foreign_keys = inspector.get_foreign_keys("w2_commit_operations")
    assert {
        ("users", ("owner_user_id",)),
        ("jobs", ("job_id", "owner_user_id")),
        ("job_commands", ("command_id", "job_id", "owner_user_id")),
    } <= {
        (foreign_key["referred_table"], tuple(foreign_key["constrained_columns"]))
        for foreign_key in foreign_keys
    }


@pytest.mark.postgres
def test_w2_commit_operation_enforces_command_binding_and_value_constraints(
    migrated_engine: Engine,
) -> None:
    owner_id, job_id, command_id = _seed_private_command(migrated_engine)
    with migrated_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.current_user_id', :owner_id, true)"),
            {"owner_id": str(owner_id)},
        )
        _insert_operation(
            connection,
            owner_id=owner_id,
            job_id=job_id,
            command_id=command_id,
        )

    invalid_cases = (
        {"result_digest": "sha256:not-a-valid-digest"},
        {"operation_revision": 0},
        {"state": "INVENTED_STATE"},
        {"purge_owner_deletion_epoch": 0},
    )
    for values in invalid_cases:
        other_owner_id, other_job_id, other_command_id = _seed_private_command(migrated_engine)
        with migrated_engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(
                    text("SELECT set_config('app.current_user_id', :owner_id, true)"),
                    {"owner_id": str(other_owner_id)},
                )
                with pytest.raises(IntegrityError):
                    _insert_operation(
                        connection,
                        owner_id=other_owner_id,
                        job_id=other_job_id,
                        command_id=other_command_id,
                        **values,
                    )
            finally:
                transaction.rollback()

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text("SELECT set_config('app.current_user_id', :owner_id, true)"),
                {"owner_id": str(owner_id)},
            )
            with pytest.raises(IntegrityError):
                _insert_operation(
                    connection,
                    owner_id=owner_id,
                    job_id=job_id,
                    command_id=command_id,
                    result_digest="sha256:" + "b" * 64,
                )
        finally:
            transaction.rollback()


@pytest.mark.postgres
def test_w2_commit_gate_locks_in_order_and_rejects_stale_currentness(
    migrated_engine: Engine,
) -> None:
    owner_id, job_id, command_id = _seed_private_command(migrated_engine)
    factory = sessionmaker(bind=migrated_engine, expire_on_commit=False)
    holder = factory()
    try:
        with holder.begin():
            holder.execute(
                text("SELECT set_config('app.current_user_id', :owner_id, true)"),
                {"owner_id": str(owner_id)},
            )
            service = W2CommitGateService(holder)
            operation = service.create_prepare_operation(
                owner_user_id=owner_id,
                job_id=job_id,
                command_id=command_id,
                execution_fence=1,
                owner_deletion_epoch=0,
                result_digest="sha256:" + "c" * 64,
            )
            assert operation.operation_revision == 1
            assert service.create_prepare_operation(
                owner_user_id=owner_id,
                job_id=job_id,
                command_id=command_id,
                execution_fence=1,
                owner_deletion_epoch=0,
                result_digest="sha256:" + "c" * 64,
            ).id == operation.id
            with pytest.raises(W2CommitGateCurrentnessError):
                service.create_prepare_operation(
                    owner_user_id=owner_id,
                    job_id=job_id,
                    command_id=command_id,
                    execution_fence=2,
                    owner_deletion_epoch=0,
                    result_digest="sha256:" + "c" * 64,
                )

        with holder.begin():
            holder.execute(
                text("SELECT set_config('app.current_user_id', :owner_id, true)"),
                {"owner_id": str(owner_id)},
            )
            operation = W2CommitGateService(holder).transition_operation(
                owner_user_id=owner_id,
                job_id=job_id,
                command_id=command_id,
                operation_id=operation.id,
                expected_revision=1,
                target_state="PREPARED",
            )
            assert operation.operation_revision == 2
            assert operation.state == "PREPARED"
            with pytest.raises(W2CommitGateCurrentnessError):
                W2CommitGateService(holder).transition_operation(
                    owner_user_id=owner_id,
                    job_id=job_id,
                    command_id=command_id,
                    operation_id=operation.id,
                    expected_revision=1,
                    target_state="W1_COMMITTED",
                )
            assert operation.operation_revision == 2
            assert operation.state == "PREPARED"

        with holder.begin():
            holder.execute(
                text("SELECT set_config('app.current_user_id', :owner_id, true)"),
                {"owner_id": str(owner_id)},
            )
            W2CommitGateService(holder).lock_operation_context(
                owner_user_id=owner_id,
                job_id=job_id,
                command_id=command_id,
            )
            with migrated_engine.connect() as contender:
                transaction = contender.begin()
                try:
                    contender.execute(
                        text("SELECT set_config('app.current_user_id', :owner_id, true)"),
                        {"owner_id": str(owner_id)},
                    )
                    contender.execute(text("SET LOCAL lock_timeout = '100ms'"))
                    with pytest.raises(DBAPIError):
                        contender.execute(
                            text(
                                "SELECT id FROM w2_commit_operations "
                                "WHERE command_id = :command_id FOR UPDATE"
                            ),
                            {"command_id": command_id},
                        )
                finally:
                    transaction.rollback()
    finally:
        holder.close()

    assert LOCK_ORDER == (
        "User",
        "Job",
        "W2 child JobCommand",
        "W2CommitOperation",
        "active lease",
        "owner slot",
    )


@pytest.mark.postgres
def test_commit_ready_only_reaches_finalized_after_prepare_and_finalize_acks(
    migrated_engine: Engine,
) -> None:
    owner_id, job_id, command_id, lease_id = _seed_active_w2_command(migrated_engine)
    factory = _owner_scoped_factory(migrated_engine, owner_id)
    collection_worker = CollectionResultWorker(
        session_factory=factory,
        sqs=InMemorySqsPort(),
        result_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/w2-result",
    )
    ready = _commit_ready(
        owner_id=owner_id,
        job_id=job_id,
        command_id=command_id,
        lease_id=lease_id,
    )

    prepared = collection_worker.accept_commit_ready(staged_result=ready)
    assert prepared.outcome_code == "PREPARE_CREATED"
    assert prepared.operation_id is not None
    assert prepared.prepare_outbox_id is not None

    with factory.begin() as session:
        operation = session.get(W2CommitOperation, prepared.operation_id)
        assert operation is not None
        assert (operation.state, operation.operation_revision) == ("PREPARE_PENDING", 1)
        assert session.scalars(
            select(OutboxMessage).where(
                OutboxMessage.aggregate_id == operation.id,
                OutboxMessage.message_type == "w1.private.w2.commit-gate.v1",
            )
        ).all()[0].payload["action"] == "PREPARE"
        assert session.scalar(
            select(OutboxMessage).where(
                OutboxMessage.aggregate_id == operation.id,
                OutboxMessage.payload["action"].astext == "FINALIZE",
            )
        ) is None

    relay_sqs = InMemorySqsPort()
    relay = OutboxRelay(
        session_factory=factory,
        sqs=relay_sqs,
        queues=QueueUrlRegistry(
            w1_execution_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/w1-execution",
            w2_collection_command_queue_url=W2_COMMIT_GATE_QUEUE_URL,
        ),
        relay_id="w2-commit-gate-phase3-test",
    )
    assert relay.drain_once(limit=10).published == 1
    assert len(relay_sqs.sent_messages) == 1
    prepare_delivery = json.loads(relay_sqs.sent_messages[0].body)
    assert prepare_delivery["action"] == "PREPARE"
    assert prepare_delivery["message_id"] == str(prepared.prepare_outbox_id)

    ack_worker = W2CommitGateAckWorker(session_factory=factory)
    prepare_ack = ack_worker.apply_ack(
        ack=W2CommitGateAck(
            message_id=uuid4(),
            operation_id=prepared.operation_id,
            operation_revision=1,
            action="PREPARE",
            command_id=command_id,
            job_id=job_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            result_digest=ready.result_digest,
        )
    )
    assert (prepare_ack.outcome_code, prepare_ack.state, prepare_ack.operation_revision) == (
        "APPLIED",
        "PREPARED",
        2,
    )

    with factory.begin() as session:
        operation = session.get(W2CommitOperation, prepared.operation_id)
        assert operation is not None
        assert operation.state == "PREPARED"
        # W1 cannot ask W2 to make staging data visible before it has durably
        # committed its own checkpoint and created a FINALIZE command.
        assert session.scalar(
            select(OutboxMessage).where(
                OutboxMessage.aggregate_id == operation.id,
                OutboxMessage.payload["action"].astext == "FINALIZE",
            )
        ) is None

    with factory.begin() as session:
        finalization = W2CommitGateService(session).finalize_prepared_operation(
            owner_user_id=owner_id,
            job_id=job_id,
            command_id=command_id,
            operation_id=prepared.operation_id,
            expected_revision=2,
            apply_w1_owned=lambda context: session.add(
                JobCheckpoint(
                    job_id=context.job.id,
                    owner_user_id=context.owner.id,
                    checkpoint_revision=1,
                    checkpoint_schema_version="w1.commit-gate.v1",
                    analysis_input_version=context.job.analysis_input_version,
                    execution_fence=context.job.execution_fence,
                    owner_deletion_epoch=context.job.owner_deletion_epoch,
                    resume_stage="w1_commit_gate",
                    state_ref="w1:commit-gate:staged",
                    resume_payload={},
                    resumable=False,
                )
            ),
        )
        assert (finalization.operation.state, finalization.operation.operation_revision) == (
            "FINALIZE_PENDING",
            4,
        )
        assert finalization.operation.w1_committed_at is not None
        assert finalization.finalize_outbox.payload["action"] == "FINALIZE"
        assert finalization.finalize_outbox.payload["operation_revision"] == 4

    assert relay.drain_once(limit=10).published == 1
    finalize_delivery = json.loads(relay_sqs.sent_messages[-1].body)
    assert finalize_delivery["action"] == "FINALIZE"
    assert finalize_delivery["operation_revision"] == 4

    finalize_ack = ack_worker.apply_ack(
        ack=W2CommitGateAck(
            message_id=uuid4(),
            operation_id=prepared.operation_id,
            operation_revision=4,
            action="FINALIZE",
            command_id=command_id,
            job_id=job_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            result_digest=ready.result_digest,
        )
    )
    assert (finalize_ack.outcome_code, finalize_ack.state, finalize_ack.operation_revision) == (
        "APPLIED",
        "FINALIZED",
        5,
    )
    with factory.begin() as session:
        assert session.get(W2CommitOperation, prepared.operation_id).finalized_at is not None
        checkpoint = session.scalar(select(JobCheckpoint).where(JobCheckpoint.job_id == job_id))
        assert checkpoint is not None


@pytest.mark.postgres
def test_finalizer_rolls_back_w1_owned_write_and_finalize_outbox_together(
    migrated_engine: Engine,
) -> None:
    owner_id, job_id, command_id, lease_id = _seed_active_w2_command(migrated_engine)
    factory = _owner_scoped_factory(migrated_engine, owner_id)
    worker = CollectionResultWorker(
        session_factory=factory,
        sqs=InMemorySqsPort(),
        result_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/w2-result",
    )
    ready = _commit_ready(
        owner_id=owner_id,
        job_id=job_id,
        command_id=command_id,
        lease_id=lease_id,
    )
    prepared = worker.accept_commit_ready(staged_result=ready)
    assert prepared.operation_id is not None
    ack = W2CommitGateAckWorker(session_factory=factory).apply_ack(
        ack=W2CommitGateAck(
            message_id=uuid4(),
            operation_id=prepared.operation_id,
            operation_revision=1,
            action="PREPARE",
            command_id=command_id,
            job_id=job_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            result_digest=ready.result_digest,
        )
    )
    assert ack.outcome_code == "APPLIED"

    def fail_w1_owned_writer(context: object) -> None:
        del context
        raise RuntimeError("writer failed")

    with pytest.raises(RuntimeError), factory.begin() as session:
        W2CommitGateService(session).finalize_prepared_operation(
            owner_user_id=owner_id,
            job_id=job_id,
            command_id=command_id,
            operation_id=prepared.operation_id,
            expected_revision=2,
            apply_w1_owned=fail_w1_owned_writer,
        )

    with factory.begin() as session:
        operation = session.get(W2CommitOperation, prepared.operation_id)
        assert operation is not None
        assert (operation.state, operation.operation_revision) == ("PREPARED", 2)
        assert session.scalar(
            select(OutboxMessage).where(
                OutboxMessage.aggregate_id == operation.id,
                OutboxMessage.payload["action"].astext == "FINALIZE",
            )
        ) is None


@pytest.mark.postgres
def test_commit_ready_stale_or_duplicate_delivery_cannot_create_a_visibility_command(
    migrated_engine: Engine,
) -> None:
    owner_id, job_id, command_id, lease_id = _seed_active_w2_command(migrated_engine)
    factory = _owner_scoped_factory(migrated_engine, owner_id)
    worker = CollectionResultWorker(
        session_factory=factory,
        sqs=InMemorySqsPort(),
        result_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/w2-result",
    )
    stale = worker.accept_commit_ready(
        staged_result=_commit_ready(
            owner_id=owner_id,
            job_id=job_id,
            command_id=command_id,
            lease_id=lease_id,
            execution_fence=2,
        )
    )
    assert stale.outcome_code == "STALE_REJECTED"
    with factory.begin() as session:
        assert session.scalar(
            select(W2CommitOperation).where(W2CommitOperation.command_id == command_id)
        ) is None
        assert session.scalar(
            select(OutboxMessage).where(OutboxMessage.command_id == command_id)
        ) is None

    ready = _commit_ready(
        owner_id=owner_id,
        job_id=job_id,
        command_id=command_id,
        lease_id=lease_id,
    )
    assert worker.accept_commit_ready(staged_result=ready).outcome_code == "PREPARE_CREATED"
    assert worker.accept_commit_ready(staged_result=ready).outcome_code == "DUPLICATE"
    with factory.begin() as session:
        assert len(session.scalars(select(W2CommitOperation)).all()) == 1
        messages = session.scalars(
            select(OutboxMessage).where(
                OutboxMessage.message_type == "w1.private.w2.commit-gate.v1"
            )
        ).all()
        assert len(messages) == 1
        assert messages[0].payload["action"] == "PREPARE"


@pytest.mark.postgres
def test_commit_gate_ack_rejects_binding_mismatch_before_operation_advances(
    migrated_engine: Engine,
) -> None:
    owner_id, job_id, command_id, lease_id = _seed_active_w2_command(migrated_engine)
    _, other_job_id, other_command_id, _ = _seed_active_w2_command(migrated_engine)
    factory = _owner_scoped_factory(migrated_engine, owner_id)
    worker = CollectionResultWorker(
        session_factory=factory,
        sqs=InMemorySqsPort(),
        result_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/w2-result",
    )
    ready = _commit_ready(
        owner_id=owner_id,
        job_id=job_id,
        command_id=command_id,
        lease_id=lease_id,
    )
    prepared = worker.accept_commit_ready(staged_result=ready)
    assert prepared.operation_id is not None

    ack_worker = W2CommitGateAckWorker(session_factory=factory)
    bad_ack = W2CommitGateAck(
        message_id=uuid4(),
        operation_id=prepared.operation_id,
        operation_revision=1,
        action="PREPARE",
        # An ACK cannot repoint an operation to a command from another owner.
        # W1 derives the owner through the operation's immutable command/job FK
        # rather than trusting an ACK-provided owner value.
        command_id=other_command_id,
        job_id=other_job_id,
        execution_fence=1,
        owner_deletion_epoch=0,
        result_digest=ready.result_digest,
    )
    rejected = ack_worker.apply_ack(ack=bad_ack)
    assert rejected.outcome_code == "STALE_REJECTED"
    assert ack_worker.apply_ack(ack=bad_ack).outcome_code == "DUPLICATE"
    with factory.begin() as session:
        operation = session.get(W2CommitOperation, prepared.operation_id)
        assert operation is not None
        assert (operation.state, operation.operation_revision) == ("PREPARE_PENDING", 1)
        assert session.scalar(
            select(OutboxMessage).where(
                OutboxMessage.aggregate_id == operation.id,
                OutboxMessage.payload["action"].astext == "FINALIZE",
            )
        ) is None


@pytest.mark.postgres
def test_cancellation_holds_the_gate_lock_then_aborts_and_rejects_late_prepare_ack(
    migrated_engine: Engine,
) -> None:
    """CT15-02/03: cancellation linearizes before a late W2 PREPARE acknowledgement."""

    owner_id, job_id, command_id, lease_id = _seed_active_w2_command(migrated_engine)
    factory = _owner_scoped_factory(migrated_engine, owner_id)
    result_digest = "sha256:" + "e" * 64
    ready = _commit_ready(
        owner_id=owner_id,
        job_id=job_id,
        command_id=command_id,
        lease_id=lease_id,
        result_digest=result_digest,
    )
    collection_worker = CollectionResultWorker(
        session_factory=factory,
        sqs=InMemorySqsPort(),
        result_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/w2-result",
    )
    accepted = collection_worker.accept_commit_ready(staged_result=ready)
    assert accepted.operation_id is not None
    ack_worker = W2CommitGateAckWorker(session_factory=factory)
    assert ack_worker.apply_ack(
        ack=W2CommitGateAck(
            message_id=uuid4(),
            operation_id=accepted.operation_id,
            operation_revision=1,
            action="PREPARE",
            command_id=command_id,
            job_id=job_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            result_digest=result_digest,
        )
    ).state == "PREPARED"

    # Keep the cancellation transaction open and prove a competing mutation
    # cannot take the operation row lock.  The actual API action service is the
    # path under test, not a test-only service shortcut.
    cancelling = factory()
    try:
        with cancelling.begin():
            cancellation = JobActionService(cancelling).request_cancellation(
                owner_user_id=owner_id,
                job_id=job_id,
                idempotency_key="commit-gate-cancel",
                request_hash="a" * 64,
                path_scope=f"/api/v1/jobs/{job_id}/actions/cancel",
            )
            assert cancellation.response_status == 202
            operation = cancelling.get(W2CommitOperation, accepted.operation_id)
            assert operation is not None
            assert (operation.state, operation.operation_revision) == ("ABORT_PENDING", 3)
            abort = cancelling.scalar(
                select(OutboxMessage).where(
                    OutboxMessage.aggregate_id == accepted.operation_id,
                    OutboxMessage.payload["action"].astext == "ABORT",
                )
            )
            assert abort is not None
            assert abort.payload["operation_revision"] == 3

            with migrated_engine.connect() as contender:
                transaction = contender.begin()
                try:
                    contender.execute(
                        text("SELECT set_config('app.current_user_id', :owner_id, true)"),
                        {"owner_id": str(owner_id)},
                    )
                    contender.execute(text("SET LOCAL lock_timeout = '100ms'"))
                    with pytest.raises(DBAPIError):
                        contender.execute(
                            text(
                                "SELECT id FROM w2_commit_operations "
                                "WHERE id = :operation_id FOR UPDATE"
                            ),
                            {"operation_id": accepted.operation_id},
                        )
                finally:
                    transaction.rollback()
    finally:
        cancelling.close()

    relay_sqs = InMemorySqsPort()
    relay = OutboxRelay(
        session_factory=factory,
        sqs=relay_sqs,
        queues=QueueUrlRegistry(
            w1_execution_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/w1-execution",
            w2_collection_command_queue_url=W2_COMMIT_GATE_QUEUE_URL,
        ),
        relay_id="w2-commit-gate-cancel-test",
    )
    assert relay.drain_once(limit=10).published >= 1
    assert "ABORT" in {
        json.loads(delivery.body).get("action") for delivery in relay_sqs.sent_messages
    }

    late_prepare = ack_worker.apply_ack(
        ack=W2CommitGateAck(
            message_id=uuid4(),
            operation_id=accepted.operation_id,
            operation_revision=1,
            action="PREPARE",
            command_id=command_id,
            job_id=job_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            result_digest=result_digest,
        )
    )
    assert late_prepare.outcome_code == "STALE_REJECTED"
    abort_ack = ack_worker.apply_ack(
        ack=W2CommitGateAck(
            message_id=uuid4(),
            operation_id=accepted.operation_id,
            operation_revision=3,
            action="ABORT",
            command_id=command_id,
            job_id=job_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            result_digest=result_digest,
        )
    )
    assert (abort_ack.outcome_code, abort_ack.state, abort_ack.operation_revision) == (
        "APPLIED",
        "ABORTED",
        4,
    )
    with factory.begin() as session:
        assert session.scalar(
            select(JobCheckpoint).where(JobCheckpoint.job_id == job_id)
        ) is None


@pytest.mark.postgres
def test_owner_deletion_purges_finalized_result_on_succeeded_job(
    migrated_engine: Engine,
) -> None:
    """A terminal Job must not hide its still-visible private W2 result."""

    owner_id, job_id, command_id, lease_id = _seed_active_w2_command(migrated_engine)
    factory = _owner_scoped_factory(migrated_engine, owner_id)
    result_digest = "sha256:" + "e" * 64
    accepted = CollectionResultWorker(
        session_factory=factory,
        sqs=InMemorySqsPort(),
        result_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/w2-result",
    ).accept_commit_ready(
        staged_result=_commit_ready(
            owner_id=owner_id,
            job_id=job_id,
            command_id=command_id,
            lease_id=lease_id,
            result_digest=result_digest,
        )
    )
    assert accepted.operation_id is not None
    ack_worker = W2CommitGateAckWorker(session_factory=factory)
    assert ack_worker.apply_ack(
        ack=W2CommitGateAck(
            message_id=uuid4(), operation_id=accepted.operation_id,
            operation_revision=1, action="PREPARE", command_id=command_id,
            job_id=job_id, execution_fence=1, owner_deletion_epoch=0,
            result_digest=result_digest,
        )
    ).state == "PREPARED"
    with factory.begin() as session:
        W2CommitGateService(session).finalize_prepared_operation(
            owner_user_id=owner_id, job_id=job_id, command_id=command_id,
            operation_id=accepted.operation_id, expected_revision=2,
            apply_w1_owned=lambda _context: None,
        )
    assert ack_worker.apply_ack(
        ack=W2CommitGateAck(
            message_id=uuid4(), operation_id=accepted.operation_id,
            operation_revision=4, action="FINALIZE", command_id=command_id,
            job_id=job_id, execution_fence=1, owner_deletion_epoch=0,
            result_digest=result_digest,
        )
    ).state == "FINALIZED"
    with factory.begin() as session:
        job = session.get(Job, job_id)
        assert job is not None
        job.status = "SUCCEEDED"
    with factory.begin() as session:
        deletion = DeletionOrchestrationService(session)
        preview = deletion.create_account_deletion_preview(
            owner_user_id=owner_id, preview_token="ct15-terminal-job-delete"
        )
        deletion.confirm_and_start_account_deletion(
            owner_user_id=owner_id,
            deletion_request_id=preview.request.id,
            preview_token=preview.preview_token,
        )
        operation = session.get(W2CommitOperation, accepted.operation_id)
        assert operation is not None
        assert operation.state == "PURGE_PENDING"
        assert operation.purge_owner_deletion_epoch == 1


@pytest.mark.postgres
def test_owner_deletion_purges_w1_committed_gate_without_targeting_public_sources(
    migrated_engine: Engine,
) -> None:
    """CT15-08: deletion epoch fences a late FINALIZE and keeps public Source intact."""

    owner_id, job_id, command_id, lease_id = _seed_active_w2_command(migrated_engine)
    other_owner_id, other_job_id, _, _ = _seed_active_w2_command(migrated_engine)
    factory = _owner_scoped_factory(migrated_engine, owner_id)
    result_digest = "sha256:" + "f" * 64
    ready = _commit_ready(
        owner_id=owner_id,
        job_id=job_id,
        command_id=command_id,
        lease_id=lease_id,
        result_digest=result_digest,
    )
    worker = CollectionResultWorker(
        session_factory=factory,
        sqs=InMemorySqsPort(),
        result_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/w2-result",
    )
    accepted = worker.accept_commit_ready(staged_result=ready)
    assert accepted.operation_id is not None
    ack_worker = W2CommitGateAckWorker(session_factory=factory)
    assert ack_worker.apply_ack(
        ack=W2CommitGateAck(
            message_id=uuid4(),
            operation_id=accepted.operation_id,
            operation_revision=1,
            action="PREPARE",
            command_id=command_id,
            job_id=job_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            result_digest=result_digest,
        )
    ).state == "PREPARED"
    with factory.begin() as session:
        W2CommitGateService(session).finalize_prepared_operation(
            owner_user_id=owner_id,
            job_id=job_id,
            command_id=command_id,
            operation_id=accepted.operation_id,
            expected_revision=2,
            apply_w1_owned=lambda _context: None,
        )
        source_id = uuid4()
        company_id = uuid4()
        session.execute(
            text(
                "INSERT INTO companies (id, legal_name, display_name) "
                "VALUES (:id, 'Public Co', 'Public Co')"
            ),
            {"id": company_id},
        )
        session.execute(
            text(
                "INSERT INTO sources ("
                "id, company_id, source_type, canonical_url, canonical_url_hash, "
                "url_normalization_version, policy_version, policy_checked_at"
                ") VALUES ("
                ":id, :company_id, 'OFFICIAL', 'https://public.example/jobs', "
                "'public-source-for-deletion-test', 'v1', 'policy-v1', now()"
                ")"
            ),
            {"id": source_id, "company_id": company_id},
        )

    with factory.begin() as session:
        deletion = DeletionOrchestrationService(session)
        preview = deletion.create_account_deletion_preview(
            owner_user_id=owner_id,
            preview_token="commit-gate-deletion-token",
        )
        request = deletion.confirm_and_start_account_deletion(
            owner_user_id=owner_id,
            deletion_request_id=preview.request.id,
            preview_token=preview.preview_token,
        )
        operation = session.get(W2CommitOperation, accepted.operation_id)
        assert operation is not None
        assert (operation.state, operation.operation_revision) == ("PURGE_PENDING", 5)
        assert operation.purge_owner_deletion_epoch == 1
        targets = session.scalars(
            select(DeletionTarget).where(DeletionTarget.deletion_request_id == request.id)
        ).all()
        assert len(targets) == 5
        assert {(target.resource_type, target.resource_id) for target in targets} == {
            ("OWNER_PRIVATE_SCOPE", owner_id)
        }
        assert (
            session.scalar(
                text("SELECT count(*) FROM sources WHERE id = :id"), {"id": source_id}
            )
            == 1
        )

    relay_sqs = InMemorySqsPort()
    relay = OutboxRelay(
        session_factory=factory,
        sqs=relay_sqs,
        queues=QueueUrlRegistry(
            w1_execution_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/w1-execution",
            w2_collection_command_queue_url=W2_COMMIT_GATE_QUEUE_URL,
        ),
        relay_id="w2-commit-gate-deletion-test",
    )
    assert relay.drain_once(limit=20).published >= 1
    assert "PURGE" in {
        json.loads(delivery.body).get("action") for delivery in relay_sqs.sent_messages
    }
    purge_message_id = UUID(
        next(
            json.loads(delivery.body)["message_id"]
            for delivery in relay_sqs.sent_messages
            if json.loads(delivery.body).get("action") == "PURGE"
        )
    )
    recovery = W2CommitGateRecoveryWorker(session_factory=factory).recover_once(limit=10)
    assert (
        recovery.scanned,
        recovery.requeued,
        recovery.transitioned,
        recovery.skipped,
    ) == (1, 1, 0, 0)
    assert relay.drain_once(limit=20).published >= 1
    purge_message_ids = [
        UUID(json.loads(delivery.body)["message_id"])
        for delivery in relay_sqs.sent_messages
        if json.loads(delivery.body).get("action") == "PURGE"
    ]
    assert purge_message_ids == [purge_message_id, purge_message_id]

    late_commit_ready = worker.accept_commit_ready(
        staged_result=_commit_ready(
            owner_id=owner_id,
            job_id=job_id,
            command_id=command_id,
            lease_id=lease_id,
            result_digest=result_digest,
        )
    )
    assert late_commit_ready.outcome_code == "STALE_REJECTED"
    late_finalize = ack_worker.apply_ack(
        ack=W2CommitGateAck(
            message_id=uuid4(),
            operation_id=accepted.operation_id,
            operation_revision=4,
            action="FINALIZE",
            command_id=command_id,
            job_id=job_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            result_digest=result_digest,
        )
    )
    assert late_finalize.outcome_code == "STALE_REJECTED"
    bad_purge_epoch = ack_worker.apply_ack(
        ack=W2CommitGateAck(
            message_id=uuid4(),
            operation_id=accepted.operation_id,
            operation_revision=5,
            action="PURGE",
            command_id=command_id,
            job_id=job_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            purge_owner_deletion_epoch=2,
            result_digest=result_digest,
        )
    )
    assert bad_purge_epoch.outcome_code == "STALE_REJECTED"
    purge_ack = ack_worker.apply_ack(
        ack=W2CommitGateAck(
            message_id=uuid4(),
            operation_id=accepted.operation_id,
            operation_revision=5,
            action="PURGE",
            command_id=command_id,
            job_id=job_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            purge_owner_deletion_epoch=1,
            result_digest=result_digest,
        )
    )
    assert (purge_ack.outcome_code, purge_ack.state, purge_ack.operation_revision) == (
        "APPLIED",
        "PURGED",
        6,
    )
    with migrated_engine.begin() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM sources WHERE id = :id"), {"id": source_id}
            )
            == 1
        )
        assert connection.scalar(
            text("SELECT deletion_epoch FROM users WHERE id = :id"), {"id": other_owner_id}
        ) == 0
        assert connection.scalar(
            text("SELECT execution_fence FROM jobs WHERE id = :id"), {"id": other_job_id}
        ) == 1


@pytest.mark.postgres
def test_finalizer_and_cancel_barrier_linearizes_then_deletion_purges(
    migrated_engine: Engine,
) -> None:
    """CT15-03: the transaction holding the shared gate lock establishes the outcome."""

    owner_id, job_id, command_id, lease_id = _seed_active_w2_command(migrated_engine)
    factory = _owner_scoped_factory(migrated_engine, owner_id)
    result_digest = "sha256:" + "1" * 64
    ready = _commit_ready(
        owner_id=owner_id,
        job_id=job_id,
        command_id=command_id,
        lease_id=lease_id,
        result_digest=result_digest,
    )
    collection_worker = CollectionResultWorker(
        session_factory=factory,
        sqs=InMemorySqsPort(),
        result_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/w2-result",
    )
    accepted = collection_worker.accept_commit_ready(staged_result=ready)
    assert accepted.operation_id is not None
    assert W2CommitGateAckWorker(session_factory=factory).apply_ack(
        ack=W2CommitGateAck(
            message_id=uuid4(),
            operation_id=accepted.operation_id,
            operation_revision=1,
            action="PREPARE",
            command_id=command_id,
            job_id=job_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            result_digest=result_digest,
        )
    ).state == "PREPARED"

    start = Barrier(2)
    finalizer_holds_gate = Event()
    allow_finalizer_commit = Event()
    failures: list[BaseException] = []
    outcomes: dict[str, str] = {}

    def _finalize() -> None:
        try:
            with factory.begin() as session:
                start.wait(timeout=5)

                def _writer(_context: object) -> None:
                    finalizer_holds_gate.set()
                    assert allow_finalizer_commit.wait(timeout=5)

                finalization = W2CommitGateService(session).finalize_prepared_operation(
                    owner_user_id=owner_id,
                    job_id=job_id,
                    command_id=command_id,
                    operation_id=accepted.operation_id,
                    expected_revision=2,
                    apply_w1_owned=_writer,
                )
                outcomes["finalizer"] = finalization.operation.state
        except BaseException as error:  # pragma: no cover - asserted by the parent thread
            failures.append(error)

    def _cancel() -> None:
        try:
            with factory.begin() as session:
                start.wait(timeout=5)
                cancellation = JobActionService(session).request_cancellation(
                    owner_user_id=owner_id,
                    job_id=job_id,
                    idempotency_key="commit-gate-barrier-cancel",
                    request_hash="c" * 64,
                    path_scope=f"/api/v1/jobs/{job_id}/actions/cancel",
                )
                outcomes["cancel"] = cancellation.job.status
        except BaseException as error:  # pragma: no cover - asserted by the parent thread
            failures.append(error)

    finalizer_thread = Thread(target=_finalize)
    cancel_thread = Thread(target=_cancel)
    finalizer_thread.start()
    cancel_thread.start()
    assert finalizer_holds_gate.wait(timeout=5)
    allow_finalizer_commit.set()
    finalizer_thread.join(timeout=10)
    cancel_thread.join(timeout=10)
    assert not finalizer_thread.is_alive()
    assert not cancel_thread.is_alive()
    assert failures == []
    assert outcomes == {"finalizer": "FINALIZE_PENDING", "cancel": "CANCEL_REQUESTED"}

    # The finalizer won the common lock first.  A subsequent account deletion
    # must therefore PURGE the private result instead of attempting a stale
    # ABORT or allowing another FINALIZE.
    with factory.begin() as session:
        deletion = DeletionOrchestrationService(session)
        preview = deletion.create_account_deletion_preview(
            owner_user_id=owner_id,
            preview_token="commit-gate-barrier-delete",
        )
        deletion.confirm_and_start_account_deletion(
            owner_user_id=owner_id,
            deletion_request_id=preview.request.id,
            preview_token=preview.preview_token,
        )
        operation = session.get(W2CommitOperation, accepted.operation_id)
        assert operation is not None
        assert (operation.state, operation.operation_revision) == ("PURGE_PENDING", 5)


@pytest.mark.postgres
def test_ack_duplicate_and_out_of_order_delivery_cannot_reverse_an_abort(
    migrated_engine: Engine,
) -> None:
    """CT15-05/09: inbox reservation precedes mutation and old actions stay stale."""

    owner_id, job_id, command_id, lease_id = _seed_active_w2_command(migrated_engine)
    factory = _owner_scoped_factory(migrated_engine, owner_id)
    result_digest = "sha256:" + "2" * 64
    accepted = CollectionResultWorker(
        session_factory=factory,
        sqs=InMemorySqsPort(),
        result_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/w2-result",
    ).accept_commit_ready(
        staged_result=_commit_ready(
            owner_id=owner_id,
            job_id=job_id,
            command_id=command_id,
            lease_id=lease_id,
            result_digest=result_digest,
        )
    )
    assert accepted.operation_id is not None
    worker = W2CommitGateAckWorker(session_factory=factory)
    prepare_message_id = uuid4()
    prepare_ack = W2CommitGateAck(
        message_id=prepare_message_id,
        operation_id=accepted.operation_id,
        operation_revision=1,
        action="PREPARE",
        command_id=command_id,
        job_id=job_id,
        execution_fence=1,
        owner_deletion_epoch=0,
        result_digest=result_digest,
    )
    assert worker.apply_ack(ack=prepare_ack).state == "PREPARED"
    assert worker.apply_ack(ack=prepare_ack).outcome_code == "DUPLICATE"

    out_of_order_finalize = worker.apply_ack(
        ack=W2CommitGateAck(
            message_id=uuid4(),
            operation_id=accepted.operation_id,
            operation_revision=2,
            action="FINALIZE",
            command_id=command_id,
            job_id=job_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            result_digest=result_digest,
        )
    )
    assert out_of_order_finalize.outcome_code == "STALE_REJECTED"

    recovery = W2CommitGateRecoveryWorker(session_factory=factory).recover_once(limit=10)
    assert (
        recovery.scanned,
        recovery.requeued,
        recovery.transitioned,
        recovery.skipped,
    ) == (1, 0, 1, 0)
    with factory.begin() as session:
        operation = session.get(W2CommitOperation, accepted.operation_id)
        assert operation is not None
        assert (operation.state, operation.operation_revision) == ("ABORT_PENDING", 3)

    late_finalize = worker.apply_ack(
        ack=W2CommitGateAck(
            message_id=uuid4(),
            operation_id=accepted.operation_id,
            operation_revision=2,
            action="FINALIZE",
            command_id=command_id,
            job_id=job_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            result_digest=result_digest,
        )
    )
    assert late_finalize.outcome_code == "STALE_REJECTED"
    assert worker.apply_ack(
        ack=W2CommitGateAck(
            message_id=uuid4(),
            operation_id=accepted.operation_id,
            operation_revision=3,
            action="ABORT",
            command_id=command_id,
            job_id=job_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            result_digest=result_digest,
        )
    ).state == "ABORTED"


@pytest.mark.postgres
def test_recovery_requeues_same_message_and_repairs_w1_committed_crash_point(
    migrated_engine: Engine,
) -> None:
    """CT15-06/07: recovery never fabricates a retry ID for an existing command."""

    owner_id, job_id, command_id, lease_id = _seed_active_w2_command(migrated_engine)
    factory = _owner_scoped_factory(migrated_engine, owner_id)
    result_digest = "sha256:" + "3" * 64
    accepted = CollectionResultWorker(
        session_factory=factory,
        sqs=InMemorySqsPort(),
        result_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/w2-result",
    ).accept_commit_ready(
        staged_result=_commit_ready(
            owner_id=owner_id,
            job_id=job_id,
            command_id=command_id,
            lease_id=lease_id,
            result_digest=result_digest,
        )
    )
    assert accepted.operation_id is not None
    assert accepted.prepare_outbox_id is not None
    with factory.begin() as session:
        prepare_outbox = session.get(OutboxMessage, accepted.prepare_outbox_id)
        assert prepare_outbox is not None
        prepare_outbox.status = "PUBLISHED"

    recovery_worker = W2CommitGateRecoveryWorker(session_factory=factory)
    first = recovery_worker.recover_once(limit=10)
    assert (first.scanned, first.requeued, first.transitioned, first.skipped) == (1, 1, 0, 0)
    with factory.begin() as session:
        operation = session.get(W2CommitOperation, accepted.operation_id)
        prepare_outbox = session.get(OutboxMessage, accepted.prepare_outbox_id)
        assert operation is not None and prepare_outbox is not None
        assert (operation.state, operation.operation_revision) == ("PREPARE_PENDING", 1)
        assert prepare_outbox.status == "PENDING"

    ack_worker = W2CommitGateAckWorker(session_factory=factory)
    assert ack_worker.apply_ack(
        ack=W2CommitGateAck(
            message_id=uuid4(),
            operation_id=accepted.operation_id,
            operation_revision=1,
            action="PREPARE",
            command_id=command_id,
            job_id=job_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            result_digest=result_digest,
        )
    ).state == "PREPARED"
    with factory.begin() as session:
        W2CommitGateService(session).transition_operation(
            owner_user_id=owner_id,
            job_id=job_id,
            command_id=command_id,
            operation_id=accepted.operation_id,
            expected_revision=2,
            target_state="W1_COMMITTED",
        )

    repair = recovery_worker.recover_once(limit=10)
    assert (repair.scanned, repair.requeued, repair.transitioned, repair.skipped) == (1, 0, 1, 0)
    with factory.begin() as session:
        operation = session.get(W2CommitOperation, accepted.operation_id)
        assert operation is not None
        assert (operation.state, operation.operation_revision) == ("FINALIZE_PENDING", 4)
        finalize_outbox = session.scalar(
            select(OutboxMessage).where(
                OutboxMessage.aggregate_id == operation.id,
                OutboxMessage.aggregate_revision == 4,
                OutboxMessage.payload["action"].astext == "FINALIZE",
            )
        )
        assert finalize_outbox is not None
        finalize_outbox.status = "PUBLISHED"
        finalize_outbox_id = finalize_outbox.id

    replay = recovery_worker.recover_once(limit=10)
    assert (replay.scanned, replay.requeued, replay.transitioned, replay.skipped) == (1, 1, 0, 0)
    with factory.begin() as session:
        finalize_outbox = session.get(OutboxMessage, finalize_outbox_id)
        assert finalize_outbox is not None
        assert finalize_outbox.status == "PENDING"
        assert len(
            session.scalars(
                select(OutboxMessage).where(
                    OutboxMessage.aggregate_id == accepted.operation_id,
                    OutboxMessage.aggregate_revision == 4,
                    OutboxMessage.payload["action"].astext == "FINALIZE",
                )
            ).all()
        ) == 1

    assert ack_worker.apply_ack(
        ack=W2CommitGateAck(
            message_id=uuid4(),
            operation_id=accepted.operation_id,
            operation_revision=4,
            action="FINALIZE",
            command_id=command_id,
            job_id=job_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            result_digest=result_digest,
        )
    ).state == "FINALIZED"
    terminal = recovery_worker.recover_once(limit=10)
    assert (
        terminal.scanned,
        terminal.requeued,
        terminal.transitioned,
        terminal.skipped,
    ) == (0, 0, 0, 0)
