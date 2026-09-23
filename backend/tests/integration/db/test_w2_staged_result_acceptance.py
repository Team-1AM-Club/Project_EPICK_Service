from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, event, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.models.jobs import Job, JobCommand, OutboxMessage
from app.models.w2_commit_operations import W2CommitOperation, W2StagedResult
from app.runtime.outbox_relay import OutboxRelay, QueueUrlRegistry
from app.runtime.sqs import InMemorySqsPort
from app.runtime.w2_commit_gate_contracts import parse_w2_staged_result, staged_result_digest
from app.runtime.w2_commit_gate_worker import (
    W2CommitGateAck,
    W2CommitGateAckWorker,
    W2CommitGateInboundWorker,
    W2CommitGateRecoveryWorker,
)
from app.runtime.workers import CollectionResultWorker
from app.services.deletion import DeletionOrchestrationService
from app.services.w2_commit_gate import W2CommitGateService

FIXTURE_ROOT = Path(__file__).parents[3] / "contracts" / "fixtures/v1/w2_commit_gate"

pytestmark = [pytest.mark.postgres, pytest.mark.w1_isolated_commit_gate]


class _OwnerScopedSession(Session):
    pass


@event.listens_for(_OwnerScopedSession, "after_begin")
def _apply_owner_scope(session: Session, transaction: object, connection: object) -> None:
    del transaction
    owner_user_id = session.info.get("owner_user_id")
    if owner_user_id is not None:
        connection.execute(
            text("SELECT set_config('app.current_user_id', :owner_id, true)"),
            {"owner_id": owner_user_id},
        )


@pytest.fixture(autouse=True)
def _clean_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))


def _factory(migrated_engine: Engine, owner_id: UUID) -> sessionmaker:
    return sessionmaker(
        bind=migrated_engine,
        class_=_OwnerScopedSession,
        autoflush=False,
        expire_on_commit=False,
        info={"owner_user_id": str(owner_id)},
    )


def _seed_current_w2_command(migrated_engine: Engine) -> tuple[UUID, UUID, UUID]:
    owner_id, job_id, command_id, lease_id = uuid4(), uuid4(), uuid4(), uuid4()
    with migrated_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.current_user_id', :owner_id, true)"),
            {"owner_id": str(owner_id)},
        )
        connection.execute(
            text(
                "INSERT INTO users (id, display_name, locale, timezone) "
                "VALUES (:id, 'W2 staged owner', 'ko-KR', 'Asia/Seoul')"
            ),
            {"id": owner_id},
        )
        connection.execute(
            text(
                "INSERT INTO jobs (id, owner_user_id, job_type, status, dispatch_status, "
                "active_lease_id, owner_deletion_epoch) VALUES "
                "(:id, :owner_id, 'SOURCE_COLLECTION', 'RUNNING', 'CLAIMED', :lease_id, 0)"
            ),
            {"id": job_id, "owner_id": owner_id, "lease_id": lease_id},
        )
        connection.execute(
            text(
                "INSERT INTO owner_execution_slots (owner_user_id, slot_no, job_id, lease_id) "
                "VALUES (:owner_id, 1, :job_id, :lease_id)"
            ),
            {"owner_id": owner_id, "job_id": job_id, "lease_id": lease_id},
        )
        connection.execute(
            text(
                "INSERT INTO job_execution_leases "
                "(id, job_id, owner_user_id, slot_no, execution_fence, owner_deletion_epoch) "
                "VALUES (:id, :job_id, :owner_id, 1, 1, 0)"
            ),
            {"id": lease_id, "job_id": job_id, "owner_id": owner_id},
        )
        connection.execute(
            text(
                "INSERT INTO job_commands (id, job_id, owner_user_id, command_type, "
                "command_schema_version, command_sequence, execution_fence, owner_deletion_epoch, "
                "payload, status) VALUES (:id, :job_id, :owner_id, 'W2_SOURCE_COLLECTION', "
                "'1.0', 1, 1, 0, CAST(:payload AS jsonb), 'ENQUEUED')"
            ),
            {
                "id": command_id,
                "job_id": job_id,
                "owner_id": owner_id,
                "payload": json.dumps(
                    {
                        "runtime": {"lease_id": str(lease_id), "input_version": 1},
                        "w2_command": {"input_version": 1},
                    }
                ),
            },
        )
    return owner_id, job_id, command_id


def _staged_fixture(*, owner_id: UUID, job_id: UUID, command_id: UUID) -> dict[str, object]:
    payload = json.loads((FIXTURE_ROOT / "staged-result.json").read_text(encoding="utf-8"))
    payload["message_id"] = str(uuid4())
    command = payload["command"]
    result = payload["result"]
    assert isinstance(command, dict) and isinstance(result, dict)
    command.update(
        command_id=str(command_id),
        job_id=str(job_id),
        authenticated_owner_ref=str(owner_id),
    )
    result.update(
        command_id=str(command_id),
        job_id=str(job_id),
        checkpoint_ref="private://w1/checkpoint",
        resume_stage="persist",
    )
    payload["result_digest"] = staged_result_digest(command, result)
    return payload


def _ack_fixture(
    *,
    operation: W2CommitOperation,
    message_id: UUID,
    owner_id: UUID,
    outcome: str = "APPLIED",
    result_digest: str | None = None,
) -> dict[str, object]:
    payload = json.loads((FIXTURE_ROOT / "ack-prepare-applied.json").read_text(encoding="utf-8"))
    payload.update(
        message_id=str(message_id),
        operation_id=str(operation.id),
        operation_revision=operation.operation_revision,
        command_id=str(operation.command_id),
        job_id=str(operation.job_id),
        authenticated_owner_ref=str(owner_id),
        execution_fence=operation.execution_fence,
        owner_deletion_epoch=operation.owner_deletion_epoch,
        result_digest=result_digest or operation.result_digest,
        outcome=outcome,
    )
    return payload


def test_staged_result_creates_prepare_only_then_prepare_ack_finalizes_once(
    migrated_engine: Engine,
) -> None:
    owner_id, job_id, command_id = _seed_current_w2_command(migrated_engine)
    factory = _factory(migrated_engine, owner_id)
    proposal_body = _staged_fixture(owner_id=owner_id, job_id=job_id, command_id=command_id)
    proposal = parse_w2_staged_result(proposal_body)
    sqs = InMemorySqsPort()
    sqs.inject_message(
        queue_url="w2-gate-inbound",
        body=json.dumps(proposal_body),
        sender_id="w2-sender-id:assumed-role-session",
    )

    result = W2CommitGateInboundWorker(
        session_factory=factory,
        sqs=sqs,
        queue_url="w2-gate-inbound",
        expected_sender_id="w2-sender-id",
    ).drain_once()

    assert (result.received, result.acknowledged, result.retry_scheduled) == (1, 1, 0)
    with factory.begin() as session:
        assert session.scalar(text("SELECT current_setting('app.current_user_id', true)")) == str(
            owner_id
        )
        operation = session.scalar(select(W2CommitOperation))
        staged = session.scalar(select(W2StagedResult))
        assert operation is not None and staged is not None
        assert (operation.state, operation.operation_revision) == ("PREPARE_PENDING", 1)
        assert staged.payload_state == "ACTIVE" and staged.result_payload is not None
        actions = [message.payload["action"] for message in session.scalars(select(OutboxMessage))]
        assert actions == ["PREPARE"]
        assert session.scalar(select(Job).where(Job.id == job_id)).status == "RUNNING"

    gate_sqs = InMemorySqsPort()
    relay = OutboxRelay(
        session_factory=factory,
        sqs=gate_sqs,
        queues=QueueUrlRegistry(
            w1_execution_queue_url="https://sqs.example/w1-execution",
            w2_collection_command_queue_url="https://sqs.example/w2-collection",
            w2_commit_gate_command_queue_url="https://sqs.example/w2-gate",
        ),
        relay_id="staged-result-gate-test",
    )
    assert relay.drain_once(limit=10).published == 1
    assert json.loads(gate_sqs.sent_messages[-1].body)["action"] == "PREPARE"

    ack_result = W2CommitGateAckWorker(session_factory=factory).apply_ack(
        ack=W2CommitGateAck(
            message_id=uuid4(),
            operation_id=operation.id,
            operation_revision=1,
            action="PREPARE",
            command_id=command_id,
            job_id=job_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            result_digest=proposal.result_digest,
        )
    )

    assert ack_result.outcome_code == "APPLIED"
    with factory.begin() as session:
        operation = session.scalar(select(W2CommitOperation))
        staged = session.scalar(select(W2StagedResult))
        job = session.scalar(select(Job).where(Job.id == job_id))
        command = session.scalar(select(JobCommand).where(JobCommand.id == command_id))
        assert all(item is not None for item in (operation, staged, job, command))
        assert (operation.state, operation.operation_revision) == ("FINALIZE_PENDING", 4)
        assert staged.payload_state == "CONSUMED" and staged.result_payload is None
        assert job.status == "WAITING_USER" and job.active_lease_id is None
        assert command.status == "CONSUMED"
        actions = [message.payload["action"] for message in session.scalars(select(OutboxMessage))]
        assert actions == ["PREPARE", "FINALIZE"]

    # Re-drive the same durable FINALIZE after a relay crash or terminal rejection.
    with factory.begin() as session:
        final_outbox = session.scalar(
            select(OutboxMessage).where(OutboxMessage.payload["action"].astext == "FINALIZE")
        )
        assert final_outbox is not None
        final_outbox.status = "FAILED_FINAL"
        final_outbox.last_error_code = "OUTBOX_COMMIT_GATE_BINDING_MISMATCH"
        final_outbox.last_error_at = datetime.now(UTC)
    recovery = W2CommitGateRecoveryWorker(session_factory=factory).recover_once(limit=10)
    assert recovery.requeued == 1

    final_delivery = relay.drain_once(limit=10)
    assert (final_delivery.published, final_delivery.failed_final) == (1, 0)
    assert json.loads(gate_sqs.sent_messages[-1].body)["action"] == "FINALIZE"
    final_ack = W2CommitGateAckWorker(session_factory=factory).apply_ack(
        ack=W2CommitGateAck(
            message_id=uuid4(),
            operation_id=operation.id,
            operation_revision=4,
            action="FINALIZE",
            command_id=command_id,
            job_id=job_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            result_digest=proposal.result_digest,
        )
    )
    assert (final_ack.outcome_code, final_ack.state) == ("APPLIED", "FINALIZED")


def test_wrong_sender_is_terminal_and_creates_no_staged_gate_effect(
    migrated_engine: Engine,
) -> None:
    owner_id, job_id, command_id = _seed_current_w2_command(migrated_engine)
    factory = _factory(migrated_engine, owner_id)
    body = _staged_fixture(owner_id=owner_id, job_id=job_id, command_id=command_id)
    sqs = InMemorySqsPort()
    sqs.inject_message(
        queue_url="w2-gate-inbound",
        body=json.dumps(body),
        sender_id="unexpected-sender",
    )

    result = W2CommitGateInboundWorker(
        session_factory=factory,
        sqs=sqs,
        queue_url="w2-gate-inbound",
        expected_sender_id="w2-sender-id",
    ).drain_once()

    assert (result.received, result.acknowledged, result.retry_scheduled) == (1, 1, 0)
    with factory.begin() as session:
        assert session.scalar(select(W2CommitOperation)) is None
        assert session.scalar(select(W2StagedResult)) is None
        assert session.scalar(select(OutboxMessage)) is None


def test_legacy_result_route_rejects_commit_gate_staged_message_without_effect(
    migrated_engine: Engine,
) -> None:
    owner_id, job_id, command_id = _seed_current_w2_command(migrated_engine)
    factory = _factory(migrated_engine, owner_id)
    sqs = InMemorySqsPort()
    sqs.inject_message(
        queue_url="legacy-w2-result",
        body=json.dumps(_staged_fixture(owner_id=owner_id, job_id=job_id, command_id=command_id)),
    )

    result = CollectionResultWorker(
        session_factory=factory,
        sqs=sqs,
        result_queue_url="legacy-w2-result",
    ).drain_once()

    assert (result.received, result.acknowledged, result.rejected_schema) == (1, 1, 0)
    with factory.begin() as session:
        assert session.scalar(select(W2CommitOperation)) is None
        assert session.scalar(select(W2StagedResult)) is None
        assert session.scalar(select(OutboxMessage)) is None


def test_failed_staged_finalizer_rolls_back_result_payload_clear_and_finalize_outbox(
    migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id, job_id, command_id = _seed_current_w2_command(migrated_engine)
    factory = _factory(migrated_engine, owner_id)
    body = _staged_fixture(owner_id=owner_id, job_id=job_id, command_id=command_id)
    proposal = parse_w2_staged_result(body)
    sqs = InMemorySqsPort()
    sqs.inject_message(
        queue_url="w2-gate-inbound",
        body=json.dumps(body),
        sender_id="w2-sender-id",
    )
    W2CommitGateInboundWorker(
        session_factory=factory,
        sqs=sqs,
        queue_url="w2-gate-inbound",
        expected_sender_id="w2-sender-id",
    ).drain_once()
    with factory.begin() as session:
        operation = session.scalar(select(W2CommitOperation))
        assert operation is not None

    def _fail_writer(_context: object, _result: dict[str, object]) -> None:
        raise RuntimeError("synthetic finalizer failure")

    monkeypatch.setattr("app.runtime.w2_commit_gate_worker._apply_staged_result", _fail_writer)
    with pytest.raises(RuntimeError, match="synthetic finalizer failure"):
        W2CommitGateAckWorker(session_factory=factory).apply_ack(
            ack=W2CommitGateAck(
                message_id=uuid4(),
                operation_id=operation.id,
                operation_revision=1,
                action="PREPARE",
                command_id=command_id,
                job_id=job_id,
                execution_fence=1,
                owner_deletion_epoch=0,
                result_digest=proposal.result_digest,
            )
        )

    with factory.begin() as session:
        operation = session.scalar(select(W2CommitOperation))
        staged = session.scalar(select(W2StagedResult))
        job = session.scalar(select(Job).where(Job.id == job_id))
        assert operation is not None and staged is not None and job is not None
        assert (operation.state, operation.operation_revision) == ("PREPARE_PENDING", 1)
        assert staged.payload_state == "ACTIVE" and staged.result_payload is not None
        assert job.status == "RUNNING"
        actions = [message.payload["action"] for message in session.scalars(select(OutboxMessage))]
        assert actions == ["PREPARE"]


def test_database_retry_is_redelivered_then_persists_once(
    migrated_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id, job_id, command_id = _seed_current_w2_command(migrated_engine)
    factory = _factory(migrated_engine, owner_id)
    sqs = InMemorySqsPort()
    sqs.inject_message(
        queue_url="w2-gate-inbound",
        body=json.dumps(_staged_fixture(owner_id=owner_id, job_id=job_id, command_id=command_id)),
        sender_id="w2-sender-id",
    )
    worker = W2CommitGateInboundWorker(
        session_factory=factory,
        sqs=sqs,
        queue_url="w2-gate-inbound",
        expected_sender_id="w2-sender-id",
    )
    original = W2CommitGateInboundWorker._accept_staged

    def _database_failure(_worker: object, _proposal: object) -> str:
        raise SQLAlchemyError("synthetic database outage")

    monkeypatch.setattr(W2CommitGateInboundWorker, "_accept_staged", _database_failure)
    first = worker.drain_once()
    assert (first.received, first.acknowledged, first.retry_scheduled) == (1, 0, 1)

    monkeypatch.setattr(W2CommitGateInboundWorker, "_accept_staged", original)
    sqs.redeliver_all()
    second = worker.drain_once()
    assert (second.received, second.acknowledged, second.retry_scheduled) == (1, 1, 0)
    with factory.begin() as session:
        assert session.scalar(select(W2CommitOperation)) is not None
        assert session.scalar(select(W2StagedResult)) is not None


def test_restart_recovery_aborts_prepared_staged_payload_and_scrubs_it(
    migrated_engine: Engine,
) -> None:
    """A restart can never later publish a private payload left at PREPARED."""

    owner_id, job_id, command_id = _seed_current_w2_command(migrated_engine)
    factory = _factory(migrated_engine, owner_id)
    body = _staged_fixture(owner_id=owner_id, job_id=job_id, command_id=command_id)
    proposal = parse_w2_staged_result(body)
    sqs = InMemorySqsPort()
    sqs.inject_message(
        queue_url="w2-gate-inbound",
        body=json.dumps(body),
        sender_id="w2-sender-id",
    )
    assert W2CommitGateInboundWorker(
        session_factory=factory,
        sqs=sqs,
        queue_url="w2-gate-inbound",
        expected_sender_id="w2-sender-id",
    ).drain_once().acknowledged == 1

    with factory.begin() as session:
        operation = session.scalar(select(W2CommitOperation))
        assert operation is not None
        W2CommitGateService(session).acknowledge_operation(
            operation_id=operation.id,
            operation_revision=operation.operation_revision,
            action="PREPARE",
            command_id=command_id,
            job_id=job_id,
            execution_fence=1,
            owner_deletion_epoch=0,
            result_digest=proposal.result_digest,
            authenticated_owner_user_id=owner_id,
        )

    with factory.begin() as session:
        operation = session.scalar(select(W2CommitOperation))
        assert operation is not None and operation.state == "PREPARED"
        recovery = W2CommitGateService(session).recover_operation(operation_id=operation.id)
        assert recovery.action == "ABORT"

    with factory.begin() as session:
        operation = session.scalar(select(W2CommitOperation))
        staged = session.scalar(select(W2StagedResult))
        assert operation is not None and staged is not None
        assert operation.state == "ABORT_PENDING"
        assert staged.payload_state == "CLEARED"
        assert staged.result_payload is None


def test_wire_ack_rejects_wrong_owner_and_id_conflict_then_deduplicates(
    migrated_engine: Engine,
) -> None:
    owner_id, job_id, command_id = _seed_current_w2_command(migrated_engine)
    factory = _factory(migrated_engine, owner_id)
    sqs = InMemorySqsPort()
    staged_body = _staged_fixture(owner_id=owner_id, job_id=job_id, command_id=command_id)
    sqs.inject_message(
        queue_url="w2-gate-inbound",
        body=json.dumps(staged_body),
        sender_id="w2-sender-id",
    )
    worker = W2CommitGateInboundWorker(
        session_factory=factory,
        sqs=sqs,
        queue_url="w2-gate-inbound",
        expected_sender_id="w2-sender-id",
    )
    assert worker.drain_once().acknowledged == 1
    with factory.begin() as session:
        operation = session.scalar(select(W2CommitOperation))
        assert operation is not None

    wrong_owner = _ack_fixture(
        operation=operation,
        message_id=uuid4(),
        owner_id=uuid4(),
    )
    sqs.inject_message(
        queue_url="w2-gate-inbound",
        body=json.dumps(wrong_owner),
        sender_id="w2-sender-id",
    )
    assert worker.drain_once().stale_discarded == 1
    with factory.begin() as session:
        operation = session.scalar(select(W2CommitOperation))
        assert operation is not None and operation.state == "PREPARE_PENDING"

    ack_message_id = uuid4()
    valid_ack = _ack_fixture(operation=operation, message_id=ack_message_id, owner_id=owner_id)
    sqs.inject_message(
        queue_url="w2-gate-inbound",
        body=json.dumps(valid_ack),
        sender_id="w2-sender-id",
    )
    assert worker.drain_once().acknowledged == 1
    sqs.inject_message(
        queue_url="w2-gate-inbound",
        body=json.dumps(valid_ack),
        sender_id="w2-sender-id",
    )
    assert worker.drain_once().duplicate == 1

    conflicting_ack = _ack_fixture(
        operation=operation,
        message_id=ack_message_id,
        owner_id=owner_id,
        result_digest="sha256:" + "f" * 64,
    )
    sqs.inject_message(
        queue_url="w2-gate-inbound",
        body=json.dumps(conflicting_ack),
        sender_id="w2-sender-id",
    )
    assert worker.drain_once().stale_discarded == 1
    with factory.begin() as session:
        operation = session.scalar(select(W2CommitOperation))
        staged = session.scalar(select(W2StagedResult))
        assert operation is not None and staged is not None
        assert operation.state == "FINALIZE_PENDING"
        assert staged.payload_state == "CONSUMED" and staged.result_payload is None


def test_cancelling_one_owner_clears_only_its_private_staged_payload(
    migrated_engine: Engine,
) -> None:
    owner_one, job_one, command_one = _seed_current_w2_command(migrated_engine)
    owner_two, job_two, command_two = _seed_current_w2_command(migrated_engine)
    factory_one = _factory(migrated_engine, owner_one)
    factory_two = _factory(migrated_engine, owner_two)
    for factory, owner_id, job_id, command_id in (
        (factory_one, owner_one, job_one, command_one),
        (factory_two, owner_two, job_two, command_two),
    ):
        sqs = InMemorySqsPort()
        staged_body = _staged_fixture(
            owner_id=owner_id,
            job_id=job_id,
            command_id=command_id,
        )
        sqs.inject_message(
            queue_url="w2-gate-inbound",
            body=json.dumps(staged_body),
            sender_id="w2-sender-id",
        )
        assert W2CommitGateInboundWorker(
            session_factory=factory,
            sqs=sqs,
            queue_url="w2-gate-inbound",
            expected_sender_id="w2-sender-id",
        ).drain_once().acknowledged == 1

    with factory_one.begin() as session:
        W2CommitGateService(session).abort_open_operations_for_cancellation(
            owner_user_id=owner_one,
            job_id=job_one,
        )
    with factory_one.begin() as session:
        staged_one = session.scalar(
            select(W2StagedResult).where(W2StagedResult.owner_user_id == owner_one)
        )
        operation_one = session.scalar(
            select(W2CommitOperation).where(W2CommitOperation.owner_user_id == owner_one)
        )
        assert staged_one is not None and operation_one is not None
        assert staged_one.payload_state == "CLEARED" and staged_one.result_payload is None
        assert operation_one.state == "ABORT_PENDING"
    with factory_two.begin() as session:
        staged_two = session.scalar(
            select(W2StagedResult).where(W2StagedResult.owner_user_id == owner_two)
        )
        operation_two = session.scalar(
            select(W2CommitOperation).where(W2CommitOperation.owner_user_id == owner_two)
        )
        assert staged_two is not None and operation_two is not None
        assert staged_two.payload_state == "ACTIVE" and staged_two.result_payload is not None
        assert operation_two.state == "PREPARE_PENDING"


def test_owner_deletion_aborts_and_scrubs_only_its_staged_payload(
    migrated_engine: Engine,
) -> None:
    """The real deletion service clears a pre-commit private payload atomically.

    The second owner's row deliberately shares the same public-source-independent
    fixture shape.  Deleting owner one must never alter owner two's in-flight
    private state.
    """

    owner_one, job_one, command_one = _seed_current_w2_command(migrated_engine)
    owner_two, job_two, command_two = _seed_current_w2_command(migrated_engine)
    factory_one = _factory(migrated_engine, owner_one)
    factory_two = _factory(migrated_engine, owner_two)
    for factory, owner_id, job_id, command_id in (
        (factory_one, owner_one, job_one, command_one),
        (factory_two, owner_two, job_two, command_two),
    ):
        sqs = InMemorySqsPort()
        sqs.inject_message(
            queue_url="w2-gate-inbound",
            body=json.dumps(
                _staged_fixture(
                    owner_id=owner_id,
                    job_id=job_id,
                    command_id=command_id,
                )
            ),
            sender_id="w2-sender-id",
        )
        assert W2CommitGateInboundWorker(
            session_factory=factory,
            sqs=sqs,
            queue_url="w2-gate-inbound",
            expected_sender_id="w2-sender-id",
        ).drain_once().acknowledged == 1

    with factory_one.begin() as session:
        deletion = DeletionOrchestrationService(session)
        preview = deletion.create_account_deletion_preview(
            owner_user_id=owner_one,
            preview_token="staged-owner-one-delete",
        )
        deletion.confirm_and_start_account_deletion(
            owner_user_id=owner_one,
            deletion_request_id=preview.request.id,
            preview_token=preview.preview_token,
        )

    with factory_one.begin() as session:
        operation = session.scalar(
            select(W2CommitOperation).where(W2CommitOperation.owner_user_id == owner_one)
        )
        staged = session.scalar(
            select(W2StagedResult).where(W2StagedResult.owner_user_id == owner_one)
        )
        assert operation is not None and staged is not None
        assert operation.state == "ABORT_PENDING"
        assert staged.payload_state == "CLEARED"
        assert staged.result_payload is None

    with factory_two.begin() as session:
        operation = session.scalar(
            select(W2CommitOperation).where(W2CommitOperation.owner_user_id == owner_two)
        )
        staged = session.scalar(
            select(W2StagedResult).where(W2StagedResult.owner_user_id == owner_two)
        )
        assert operation is not None and staged is not None
        assert operation.state == "PREPARE_PENDING"
        assert staged.payload_state == "ACTIVE"
        assert staged.result_payload is not None
