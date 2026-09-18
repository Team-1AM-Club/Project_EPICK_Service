"""W1's normalized inbound boundary for future W2 commit-gate ACKs.

W2 owns the canonical ACK schema and the SQS/parser adapter.  This module
intentionally accepts only a normalized value object so it can enforce the W1
transaction, inbox-deduplication, and immutable-binding rules without creating
an unofficial W2 wire contract.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, object_session

from app.runtime.sqs import ReceivedSqsMessage, SqsPort, SqsRetryableError
from app.runtime.w2_commit_gate_contracts import (
    W2CommitGateAckProposal,
    W2CommitGateContractError,
    W2StagedResultProposal,
    canonical_json_digest,
    parse_w2_commit_gate_proposal,
)
from app.runtime.workers import QueueWorkerRunResult, apply_locked_collection_result
from app.services.jobs import JobService
from app.services.w2_commit_gate import (
    W2CommitGateCurrentnessError,
    W2CommitGateError,
    W2CommitGateRecovery,
    W2CommitGateService,
)

_W2_COMMIT_GATE_ACK_CONSUMER = "w1.w2-commit-gate-ack"
_W2_COMMIT_GATE_INBOUND_CONSUMER = "w1.w2-commit-gate-inbound"
_MAX_SQS_BATCH_SIZE = 10


@dataclass(frozen=True)
class W2CommitGateAck:
    """Fields W1 needs after a W2 canonical ACK has been parsed.

    This is W1's normalized (not W2 wire) boundary.  Phase 4 accepts
    ``PREPARE``, ``FINALIZE``, ``ABORT``, and ``PURGE``; PURGE additionally
    carries the later owner deletion epoch that W1 originally dispatched.
    """

    message_id: UUID
    operation_id: UUID
    operation_revision: int
    action: str
    command_id: UUID
    job_id: UUID
    execution_fence: int
    owner_deletion_epoch: int
    result_digest: str
    purge_owner_deletion_epoch: int | None = None
    outcome: str = "APPLIED"
    authenticated_owner_user_id: UUID | None = None
    occurred_at: datetime | None = None
    producer_name: str | None = None
    schema_version: str | None = None
    payload_digest: str | None = None

    @classmethod
    def from_wire(cls, proposal: W2CommitGateAckProposal) -> W2CommitGateAck:
        """Retain the W2 delivery identity/outcome instead of normalizing it."""

        return cls(
            message_id=proposal.message_id,
            operation_id=proposal.operation_id,
            operation_revision=proposal.operation_revision,
            action=proposal.action,
            command_id=proposal.command_id,
            job_id=proposal.job_id,
            execution_fence=proposal.execution_fence,
            owner_deletion_epoch=proposal.owner_deletion_epoch,
            result_digest=proposal.result_digest,
            purge_owner_deletion_epoch=proposal.purge_owner_deletion_epoch,
            outcome=proposal.outcome,
            authenticated_owner_user_id=proposal.authenticated_owner_ref,
            occurred_at=proposal.occurred_at,
            producer_name=proposal.producer,
            schema_version=proposal.schema_version,
            payload_digest=canonical_json_digest(proposal.model_dump(mode="json")),
        )


@dataclass(frozen=True)
class W2CommitGateAckResult:
    outcome_code: str
    operation_id: UUID | None = None
    operation_revision: int | None = None
    state: str | None = None


@dataclass(frozen=True)
class W2CommitGateRecoveryRunResult:
    """Summary of one W1-only recovery scan; no raw W2 message is parsed here."""

    scanned: int
    requeued: int
    transitioned: int
    skipped: int


class W2CommitGateAckWorker:
    """Apply one normalized W2 ACK exactly once under W1's ordered locks."""

    def __init__(self, *, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def apply_ack(self, *, ack: W2CommitGateAck) -> W2CommitGateAckResult:
        """Persist a gate terminal/progress state only when it still matches W1 state.

        A stale or malformed binding is durably consumed as ``STALE_REJECTED``
        so an at-least-once replay cannot later become valid by accident.
        """

        with self._session_factory.begin() as session:
            inbox = JobService(session)
            # Reserve before a state transition.  A concurrent or redelivered
            # ACK with the same W2 message ID becomes a no-op before it can
            # observe or advance a newer operation revision.
            reservation_outcome = self._reserve_ack_receipt(inbox=inbox, ack=ack)
            if reservation_outcome != "INSERTED":
                return W2CommitGateAckResult(outcome_code=reservation_outcome)
            if ack.outcome != "APPLIED":
                outcome = W2CommitGateAckResult(outcome_code=f"ACK_{ack.outcome}")
                inbox.update_inbox_receipt_outcome(
                    consumer_name=_W2_COMMIT_GATE_ACK_CONSUMER,
                    event_id=ack.message_id,
                    outcome_code=outcome.outcome_code,
                )
                return outcome
            service = W2CommitGateService(session)
            try:
                operation = service.acknowledge_operation(
                    operation_id=ack.operation_id,
                    operation_revision=ack.operation_revision,
                    action=ack.action,
                    command_id=ack.command_id,
                    job_id=ack.job_id,
                    execution_fence=ack.execution_fence,
                    owner_deletion_epoch=ack.owner_deletion_epoch,
                    result_digest=ack.result_digest,
                    authenticated_owner_user_id=ack.authenticated_owner_user_id,
                    purge_owner_deletion_epoch=ack.purge_owner_deletion_epoch,
                )
            except W2CommitGateError:
                outcome = W2CommitGateAckResult(outcome_code="STALE_REJECTED")
            else:
                staged = service.repository.get_staged_result_for_update(
                    operation_id=operation.id,
                    owner_user_id=operation.owner_user_id,
                )
                if ack.action == "PREPARE" and staged is not None:
                    finalization = service.finalize_prepared_operation(
                        owner_user_id=operation.owner_user_id,
                        job_id=operation.job_id,
                        command_id=operation.command_id,
                        operation_id=operation.id,
                        expected_revision=operation.operation_revision,
                        apply_w1_owned=lambda _context: None,
                        apply_staged_w1_owned=_apply_staged_result,
                    )
                    operation = finalization.operation
                outcome = W2CommitGateAckResult(
                    outcome_code="APPLIED",
                    operation_id=operation.id,
                    operation_revision=operation.operation_revision,
                    state=operation.state,
                )
            inbox.update_inbox_receipt_outcome(
                consumer_name=_W2_COMMIT_GATE_ACK_CONSUMER,
                event_id=ack.message_id,
                outcome_code=outcome.outcome_code,
            )
            return outcome

    @staticmethod
    def _reserve_ack_receipt(*, inbox: JobService, ack: W2CommitGateAck) -> str:
        if ack.payload_digest is None:
            return (
                "INSERTED"
                if inbox.record_inbox_receipt(
                    consumer_name=_W2_COMMIT_GATE_ACK_CONSUMER,
                    event_id=ack.message_id,
                    outcome_code="PROCESSING",
                )
                else "DUPLICATE"
            )
        reservation = inbox.reserve_digest_aware_inbox_receipt(
            consumer_name=_W2_COMMIT_GATE_ACK_CONSUMER,
            event_id=ack.message_id,
            outcome_code="PROCESSING",
            payload_digest=ack.payload_digest,
            producer_name=ack.producer_name or "w2",
            schema_version=ack.schema_version or "w2.private.commit-gate-ack.proposal.v1",
        )
        if reservation.id_conflict:
            return "ID_CONFLICT"
        return "INSERTED" if reservation.inserted else "DUPLICATE"


class W2CommitGateInboundWorker:
    """Consume staged results and ACKs from the dedicated W2 private queue.

    Successful work is deleted from SQS only after its database transaction
    commits. Contract/principal violations are terminal and acknowledged;
    database and SQS transport uncertainty is intentionally redelivered.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        sqs: SqsPort,
        queue_url: str,
        expected_sender_id: str,
        expected_producer: str = "w2",
        batch_size: int = 10,
        visibility_timeout_seconds: int = 120,
        wait_time_seconds: int = 20,
    ) -> None:
        if not queue_url.strip():
            raise ValueError("queue_url must be present")
        if not expected_sender_id.strip():
            raise ValueError("expected_sender_id must be present")
        if not expected_producer.strip():
            raise ValueError("expected_producer must be present")
        if not 1 <= batch_size <= _MAX_SQS_BATCH_SIZE:
            raise ValueError("batch_size must be between 1 and 10")
        if not 1 <= visibility_timeout_seconds <= 43_200:
            raise ValueError("visibility_timeout_seconds must be between 1 and 43200")
        if not 0 <= wait_time_seconds <= 20:
            raise ValueError("wait_time_seconds must be between 0 and 20")
        self._session_factory = session_factory
        self._sqs = sqs
        self._queue_url = queue_url
        self._expected_sender_id = expected_sender_id
        self._expected_producer = expected_producer
        self._batch_size = batch_size
        self._visibility_timeout_seconds = visibility_timeout_seconds
        self._wait_time_seconds = wait_time_seconds

    def drain_once(self) -> QueueWorkerRunResult:
        try:
            deliveries = self._sqs.receive_messages(
                queue_url=self._queue_url,
                max_messages=self._batch_size,
                visibility_timeout_seconds=self._visibility_timeout_seconds,
                wait_time_seconds=self._wait_time_seconds,
            )
        except SqsRetryableError:
            return QueueWorkerRunResult(0, 0, 0, 0, 0, 0, 0)

        acknowledged = retry_scheduled = stale_discarded = rejected_schema = duplicate = 0
        for delivery in deliveries:
            outcome = self._handle_delivery(delivery)
            if outcome.acknowledge:
                try:
                    self._sqs.delete_message(
                        queue_url=self._queue_url,
                        receipt_handle=delivery.receipt_handle,
                    )
                except SqsRetryableError:
                    retry_scheduled += 1
                    continue
                acknowledged += 1
            else:
                retry_scheduled += 1
            stale_discarded += int(outcome.stale)
            rejected_schema += int(outcome.rejected_schema)
            duplicate += int(outcome.duplicate)
        return QueueWorkerRunResult(
            received=len(deliveries),
            acknowledged=acknowledged,
            retry_scheduled=retry_scheduled,
            stale_discarded=stale_discarded,
            rejected_schema=rejected_schema,
            duplicate=duplicate,
            lease_recovered=0,
        )

    def _handle_delivery(self, delivery: ReceivedSqsMessage) -> _InboundDeliveryOutcome:
        if (
            delivery.sender_id is None
            or delivery.sender_id.split(":", maxsplit=1)[0] != self._expected_sender_id
        ):
            self._record_terminal_if_possible(delivery.body, outcome_code="REJECTED_PRINCIPAL")
            return _InboundDeliveryOutcome(acknowledge=True)
        try:
            proposal = parse_w2_commit_gate_proposal(delivery.body)
            if proposal.producer != self._expected_producer:
                raise W2CommitGateContractError("unexpected W2 commit-gate producer")
        except W2CommitGateContractError:
            self._record_terminal_if_possible(delivery.body, outcome_code="REJECTED_SCHEMA")
            return _InboundDeliveryOutcome(acknowledge=True, rejected_schema=True)

        try:
            if isinstance(proposal, W2StagedResultProposal):
                acceptance = self._accept_staged(proposal)
            else:
                acceptance = self._accept_ack(proposal)
        except W2CommitGateError:
            self._record_terminal(
                message_id=proposal.message_id,
                outcome_code="STALE_REJECTED",
            )
            return _InboundDeliveryOutcome(acknowledge=True, stale=True)
        except SQLAlchemyError:
            return _InboundDeliveryOutcome(acknowledge=False)

        if acceptance in {"DUPLICATE", "ACK_DUPLICATE"}:
            return _InboundDeliveryOutcome(acknowledge=True, duplicate=True)
        if acceptance in {"ID_CONFLICT", "STALE_REJECTED", "ACK_REJECTED"}:
            return _InboundDeliveryOutcome(acknowledge=True, stale=True)
        return _InboundDeliveryOutcome(acknowledge=True)

    def _accept_staged(self, proposal: W2StagedResultProposal) -> str:
        command = proposal.command
        try:
            owner_user_id = UUID(str(command["authenticated_owner_ref"]))
            job_id = UUID(str(command["job_id"]))
            command_id = UUID(str(command["command_id"]))
            execution_fence = int(str(command["execution_fence"]))
            owner_deletion_epoch = int(command["owner_deletion_epoch"])
        except (KeyError, TypeError, ValueError) as error:  # schema already guards this path
            raise W2CommitGateContractError("W2 staged proposal binding is invalid") from error
        with self._session_factory.begin() as session:
            result = W2CommitGateService(session).create_staged_prepare_with_outbox(
                origin_message_id=proposal.message_id,
                schema_version=proposal.schema_version,
                producer_name=proposal.producer,
                occurred_at=proposal.occurred_at,
                payload_digest=canonical_json_digest(proposal.model_dump(mode="json")),
                owner_user_id=owner_user_id,
                job_id=job_id,
                command_id=command_id,
                execution_fence=execution_fence,
                owner_deletion_epoch=owner_deletion_epoch,
                result_digest=proposal.result_digest,
                result_payload=proposal.result,
                inbox_consumer_name=_W2_COMMIT_GATE_INBOUND_CONSUMER,
            )
            return result.outcome_code

    def _accept_ack(self, proposal: W2CommitGateAckProposal) -> str:
        return W2CommitGateAckWorker(session_factory=self._session_factory).apply_ack(
            ack=W2CommitGateAck.from_wire(proposal)
        ).outcome_code

    def _record_terminal_if_possible(self, body: str, *, outcome_code: str) -> None:
        try:
            proposal = parse_w2_commit_gate_proposal(body)
        except W2CommitGateContractError:
            return
        self._record_terminal(message_id=proposal.message_id, outcome_code=outcome_code)

    def _record_terminal(self, *, message_id: UUID, outcome_code: str) -> None:
        with self._session_factory.begin() as session:
            JobService(session).record_inbox_receipt(
                consumer_name=_W2_COMMIT_GATE_INBOUND_CONSUMER,
                event_id=message_id,
                outcome_code=outcome_code,
            )


@dataclass(frozen=True)
class _InboundDeliveryOutcome:
    acknowledge: bool
    stale: bool = False
    rejected_schema: bool = False
    duplicate: bool = False


def _apply_staged_result(context: object, result: dict[str, object]) -> None:
    """Use the shared concrete writer without acquiring any lock a second time."""

    owner = getattr(context, "owner", None)
    job = getattr(context, "job", None)
    command = getattr(context, "command", None)
    lease = getattr(context, "lease", None)
    if job is None or command is None or lease is None:
        raise W2CommitGateCurrentnessError("prepared operation no longer has a current lease")
    # The W2 service owns the active session. SQLAlchemy exposes it from the
    # locked mapped object, avoiding a second factory/session or lock query.
    session = object_session(job)
    if session is None:
        raise W2CommitGateCurrentnessError("prepared operation session is unavailable")
    outcome_code, _ = apply_locked_collection_result(
        session=session,
        owner=owner,
        job=job,
        command=command,
        lease=lease,
        result=result,
    )
    if outcome_code != "APPLIED":
        raise W2CommitGateCurrentnessError("prepared staged result is no longer current")


class W2CommitGateRecoveryWorker:
    """Re-drive only W1's durable commit-gate messages after a crash/timeout.

    The worker intentionally has no raw W2 queue parser.  It either requeues an
    existing outbox row with the same ``message_id`` and operation revision, or
    makes the small, explicit ABORT/FINALIZE/PURGE transition selected by the
    W1 durable state machine.
    """

    def __init__(self, *, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def recover_once(self, *, limit: int) -> W2CommitGateRecoveryRunResult:
        with self._session_factory.begin() as session:
            decisions = W2CommitGateService(session).recover_due_operations(limit=limit)
            return self._summarize(decisions)

    @staticmethod
    def _summarize(
        decisions: tuple[W2CommitGateRecovery, ...],
    ) -> W2CommitGateRecoveryRunResult:
        return W2CommitGateRecoveryRunResult(
            scanned=len(decisions),
            requeued=sum(1 for decision in decisions if decision.requeued),
            transitioned=sum(
                1 for decision in decisions if decision.action is not None and not decision.requeued
            ),
            skipped=sum(1 for decision in decisions if decision.action is None),
        )
