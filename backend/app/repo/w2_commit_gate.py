from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.identity import User
from app.models.jobs import Job, JobCommand, JobExecutionLease, OutboxMessage, OwnerExecutionSlot
from app.models.w2_commit_operations import W2CommitOperation


class W2CommitGateRepository:
    """Persistence primitives for the W1-owned W2 commit gate.

    Callers must use the locked accessors in the documented order.  Keeping the
    individual lock queries here makes that order auditable and reusable by the
    worker, cancellation, and deletion transactions.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_owner_for_update(self, *, owner_user_id: UUID) -> User | None:
        return self.session.scalar(select(User).where(User.id == owner_user_id).with_for_update())

    def get_job_for_update(self, *, job_id: UUID, owner_user_id: UUID) -> Job | None:
        return self.session.scalar(
            select(Job)
            .where(Job.id == job_id, Job.owner_user_id == owner_user_id)
            .with_for_update()
        )

    def get_command_for_update(
        self, *, command_id: UUID, job_id: UUID, owner_user_id: UUID
    ) -> JobCommand | None:
        return self.session.scalar(
            select(JobCommand)
            .where(
                JobCommand.id == command_id,
                JobCommand.job_id == job_id,
                JobCommand.owner_user_id == owner_user_id,
            )
            .with_for_update()
        )

    def get_operation_for_update(self, *, command_id: UUID) -> W2CommitOperation | None:
        return self.session.scalar(
            select(W2CommitOperation)
            .where(W2CommitOperation.command_id == command_id)
            .with_for_update()
        )

    def get_operation_identity(self, *, operation_id: UUID) -> W2CommitOperation | None:
        """Read only the stable IDs needed to acquire the ordered lock set.

        The caller must re-read this operation through ``get_operation_for_update``
        before making a state change.  This lookup therefore never authorizes an
        ACK by itself and does not weaken the shared lock order.
        """

        return self.session.get(W2CommitOperation, operation_id)

    def list_open_operation_identities_for_job(
        self, *, owner_user_id: UUID, job_id: UUID
    ) -> list[W2CommitOperation]:
        """Read candidate operations before acquiring each complete ordered lock set.

        This is deliberately an identity lookup, not an authorization decision.
        Each returned row must be read again through ``get_operation_for_update``
        after User -> Job -> JobCommand has been locked.  It mirrors
        ``get_operation_identity`` and avoids ever locking an operation before
        its parent command.
        """

        return list(
            self.session.scalars(
                select(W2CommitOperation)
                .where(
                    W2CommitOperation.owner_user_id == owner_user_id,
                    W2CommitOperation.job_id == job_id,
                    W2CommitOperation.state.in_(
                        (
                            "PREPARE_PENDING",
                            "PREPARED",
                            "W1_COMMITTED",
                            "FINALIZE_PENDING",
                            "FINALIZED",
                        )
                    ),
                )
                .order_by(W2CommitOperation.command_id, W2CommitOperation.id)
            )
        )

    def list_recovery_operation_identities(self, *, limit: int) -> list[W2CommitOperation]:
        """Return non-terminal operation identities for the recovery boundary.

        This deliberately does not lock the operation rows.  Recovery must
        acquire the full User -> Job -> JobCommand -> operation lock set again
        before it acts, just as inbound ACK handling does.
        """

        return list(
            self.session.scalars(
                select(W2CommitOperation)
                .where(
                    W2CommitOperation.state.in_(
                        (
                            "PREPARE_PENDING",
                            "PREPARED",
                            "W1_COMMITTED",
                            "FINALIZE_PENDING",
                            "ABORT_PENDING",
                            "PURGE_PENDING",
                        )
                    )
                )
                .order_by(W2CommitOperation.updated_at, W2CommitOperation.id)
                .limit(limit)
            )
        )

    def get_gate_outbox_for_update(
        self, *, operation_id: UUID, operation_revision: int, action: str
    ) -> OutboxMessage | None:
        """Lock the one immutable gate command eligible for an exact replay."""

        return self.session.scalar(
            select(OutboxMessage)
            .where(
                OutboxMessage.message_type == "w1.private.w2.commit-gate.v1",
                OutboxMessage.aggregate_type == "W2_COMMIT_OPERATION",
                OutboxMessage.aggregate_id == operation_id,
                OutboxMessage.aggregate_revision == operation_revision,
                OutboxMessage.payload["action"].astext == action,
            )
            .with_for_update()
        )

    def get_lease_for_update(
        self, *, lease_id: UUID, owner_user_id: UUID
    ) -> JobExecutionLease | None:
        return self.session.scalar(
            select(JobExecutionLease)
            .where(
                JobExecutionLease.id == lease_id,
                JobExecutionLease.owner_user_id == owner_user_id,
            )
            .with_for_update()
        )

    def get_slot_for_update(
        self, *, owner_user_id: UUID, slot_no: int
    ) -> OwnerExecutionSlot | None:
        return self.session.scalar(
            select(OwnerExecutionSlot)
            .where(
                OwnerExecutionSlot.owner_user_id == owner_user_id,
                OwnerExecutionSlot.slot_no == slot_no,
            )
            .with_for_update()
        )

    def add_operation(self, operation: W2CommitOperation) -> None:
        self.session.add(operation)
