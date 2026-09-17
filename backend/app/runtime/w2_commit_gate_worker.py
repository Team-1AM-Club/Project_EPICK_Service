"""W1's normalized inbound boundary for future W2 commit-gate ACKs.

W2 owns the canonical ACK schema and the SQS/parser adapter.  This module
intentionally accepts only a normalized value object so it can enforce the W1
transaction, inbox-deduplication, and immutable-binding rules without creating
an unofficial W2 wire contract.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.jobs import JobService
from app.services.w2_commit_gate import (
    W2CommitGateError,
    W2CommitGateRecovery,
    W2CommitGateService,
)

_W2_COMMIT_GATE_ACK_CONSUMER = "w1.w2-commit-gate-ack"


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
            if not inbox.record_inbox_receipt(
                consumer_name=_W2_COMMIT_GATE_ACK_CONSUMER,
                event_id=ack.message_id,
                outcome_code="PROCESSING",
            ):
                return W2CommitGateAckResult(outcome_code="DUPLICATE")
            try:
                operation = W2CommitGateService(session).acknowledge_operation(
                    operation_id=ack.operation_id,
                    operation_revision=ack.operation_revision,
                    action=ack.action,
                    command_id=ack.command_id,
                    job_id=ack.job_id,
                    execution_fence=ack.execution_fence,
                    owner_deletion_epoch=ack.owner_deletion_epoch,
                    result_digest=ack.result_digest,
                    purge_owner_deletion_epoch=ack.purge_owner_deletion_epoch,
                )
            except W2CommitGateError:
                outcome = W2CommitGateAckResult(outcome_code="STALE_REJECTED")
            else:
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
