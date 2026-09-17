"""W1-owned durable gate before a W2 private result can become visible.

This module intentionally contains no W2 adapter, ACK parser, or visibility
callback.  Those are Phase 3+ responsibilities.  Its only job is to make every
future commit, cancel, and deletion path use one row-lock ordering and one
immutable command binding.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.identity import User
from app.models.jobs import Job, JobCommand, JobExecutionLease, OutboxMessage, OwnerExecutionSlot
from app.models.w2_commit_operations import W2CommitOperation
from app.repo.w2_commit_gate import W2CommitGateRepository

LOCK_ORDER = (
    "User",
    "Job",
    "W2 child JobCommand",
    "W2CommitOperation",
    "active lease",
    "owner slot",
)
_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_FORWARD_TRANSITIONS = {
    "PREPARE_PENDING": {"PREPARED", "ABORT_PENDING", "FAILED_FINAL"},
    "PREPARED": {"W1_COMMITTED", "ABORT_PENDING", "FAILED_FINAL"},
    "W1_COMMITTED": {"FINALIZE_PENDING", "PURGE_PENDING", "FAILED_FINAL"},
    "FINALIZE_PENDING": {"FINALIZED", "PURGE_PENDING", "FAILED_FINAL"},
    # W2 may have confirmed its private finalization before the owner requests
    # deletion.  That still must create a deletion-epoch-bound PURGE command;
    # FINALIZED is not public-source visibility and is never a deletion escape.
    "FINALIZED": {"PURGE_PENDING", "FAILED_FINAL"},
    "ABORT_PENDING": {"ABORTED", "FAILED_FINAL"},
    "PURGE_PENDING": {"PURGED", "FAILED_FINAL"},
}
_EXECUTION_CURRENT_STATES = {"PREPARED", "W1_COMMITTED", "FINALIZE_PENDING", "FINALIZED"}
_STATE_TIMESTAMP_FIELDS = {
    "PREPARED": "prepared_at",
    "W1_COMMITTED": "w1_committed_at",
    "FINALIZED": "finalized_at",
    "ABORTED": "aborted_at",
    "PURGED": "purged_at",
}


class W2CommitGateError(Exception):
    """Base error for a rejected W2 commit-gate operation."""


class W2CommitOperationNotFoundError(W2CommitGateError):
    """The requested command has no durable commit operation."""


class W2CommitGateCurrentnessError(W2CommitGateError):
    """The immutable command binding is stale or has been forged."""


class W2CommitGateTransitionError(W2CommitGateError):
    """The requested state transition cannot advance the durable operation."""


@dataclass(frozen=True)
class LockedW2CommitGateContext:
    """Resources locked by one transaction in the globally required order."""

    owner: User
    job: Job
    command: JobCommand
    operation: W2CommitOperation | None
    lease: JobExecutionLease | None
    slot: OwnerExecutionSlot | None


@dataclass(frozen=True)
class PrepareOperationAcceptance:
    """The durable PREPARE state and its one outbound W1 command."""

    operation: W2CommitOperation
    prepare_outbox: OutboxMessage | None
    created: bool


@dataclass(frozen=True)
class W1CommitFinalization:
    """W1's committed private state and the subsequent FINALIZE command."""

    operation: W2CommitOperation
    finalize_outbox: OutboxMessage


@dataclass(frozen=True)
class W2CommitGateRecovery:
    """One W1-only recovery decision for a durable commit operation."""

    operation: W2CommitOperation
    action: str | None
    outbox: OutboxMessage | None
    requeued: bool


class W2CommitGateService:
    """Own operation creation, currentness checks, and monotonic state changes.

    The caller owns the surrounding SQLAlchemy transaction.  Every public
    method locks in :data:`LOCK_ORDER` before it reads mutable fence, epoch, or
    operation state; no later W2 adapter may bypass these methods to make a
    private result visible.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = W2CommitGateRepository(session)

    def create_prepare_operation(
        self,
        *,
        owner_user_id: UUID,
        job_id: UUID,
        command_id: UUID,
        execution_fence: int,
        owner_deletion_epoch: int,
        result_digest: str,
    ) -> W2CommitOperation:
        """Create the one durable operation for a current W2 child command.

        A same-binding retry returns the original row.  A changed digest/fence/
        epoch never overwrites that row and must be discarded by the caller.
        """

        return self.create_or_reuse_prepare_operation(
            owner_user_id=owner_user_id,
            job_id=job_id,
            command_id=command_id,
            execution_fence=execution_fence,
            owner_deletion_epoch=owner_deletion_epoch,
            result_digest=result_digest,
        ).operation

    def create_or_reuse_prepare_operation(
        self,
        *,
        owner_user_id: UUID,
        job_id: UUID,
        command_id: UUID,
        execution_fence: int,
        owner_deletion_epoch: int,
        result_digest: str,
        execution_lease_id: UUID | None = None,
    ) -> PrepareOperationAcceptance:
        """Create/reuse a durable operation without duplicating PREPARE intent.

        ``execution_lease_id`` is mandatory for the runtime COMMIT_READY path.
        It remains optional here so the Phase 2 primitive can still be used by
        cancellation/deletion code before it has a runtime lease to validate.
        """

        self._validate_digest(result_digest)
        context = self._lock_context(
            owner_user_id=owner_user_id,
            job_id=job_id,
            command_id=command_id,
        )
        self._require_execution_current(
            context=context,
            execution_fence=execution_fence,
            owner_deletion_epoch=owner_deletion_epoch,
        )
        if execution_lease_id is not None:
            self._require_active_runtime_lease(
                context=context,
                execution_lease_id=execution_lease_id,
            )
        if context.operation is not None:
            self._require_existing_binding(
                operation=context.operation,
                context=context,
                execution_fence=execution_fence,
                owner_deletion_epoch=owner_deletion_epoch,
                result_digest=result_digest,
            )
            return PrepareOperationAcceptance(
                operation=context.operation,
                prepare_outbox=None,
                created=False,
            )

        operation = W2CommitOperation(
            command_id=command_id,
            job_id=job_id,
            owner_user_id=owner_user_id,
            execution_fence=execution_fence,
            owner_deletion_epoch=owner_deletion_epoch,
            result_digest=result_digest,
            operation_revision=1,
            state="PREPARE_PENDING",
        )
        self.repository.add_operation(operation)
        self.session.flush()
        return PrepareOperationAcceptance(operation=operation, prepare_outbox=None, created=True)

    def create_prepare_operation_with_outbox(
        self,
        *,
        owner_user_id: UUID,
        job_id: UUID,
        command_id: UUID,
        execution_fence: int,
        owner_deletion_epoch: int,
        execution_lease_id: UUID,
        result_digest: str,
    ) -> PrepareOperationAcceptance:
        """Persist PREPARE_PENDING and the PREPARE outbox in one transaction."""

        acceptance = self.create_or_reuse_prepare_operation(
            owner_user_id=owner_user_id,
            job_id=job_id,
            command_id=command_id,
            execution_fence=execution_fence,
            owner_deletion_epoch=owner_deletion_epoch,
            execution_lease_id=execution_lease_id,
            result_digest=result_digest,
        )
        if not acceptance.created:
            return acceptance
        prepare_outbox = self._add_commit_gate_outbox(
            operation=acceptance.operation,
            action="PREPARE",
        )
        return PrepareOperationAcceptance(
            operation=acceptance.operation,
            prepare_outbox=prepare_outbox,
            created=True,
        )

    def transition_operation(
        self,
        *,
        owner_user_id: UUID,
        job_id: UUID,
        command_id: UUID,
        operation_id: UUID,
        expected_revision: int,
        target_state: str,
        purge_owner_deletion_epoch: int | None = None,
    ) -> W2CommitOperation:
        """Advance one operation after all mutable command resources are locked."""

        context = self._lock_context(
            owner_user_id=owner_user_id,
            job_id=job_id,
            command_id=command_id,
        )
        operation = context.operation
        if operation is None or operation.id != operation_id:
            raise W2CommitOperationNotFoundError("W2 commit operation does not match the command")
        return self._advance_locked_operation(
            context=context,
            operation=operation,
            expected_revision=expected_revision,
            target_state=target_state,
            purge_owner_deletion_epoch=purge_owner_deletion_epoch,
        )

    def acknowledge_operation(
        self,
        *,
        operation_id: UUID,
        operation_revision: int,
        action: str,
        command_id: UUID,
        job_id: UUID,
        execution_fence: int,
        owner_deletion_epoch: int,
        result_digest: str,
        purge_owner_deletion_epoch: int | None = None,
    ) -> W2CommitOperation:
        """Apply a normalized W2 ACK after W1 re-locks the immutable binding.

        This intentionally accepts no raw queue payload.  The W2 canonical ACK
        schema is W2-owned and its future adapter must normalize that artifact
        into this boundary; W1 never invents or pins a W2 wire schema here.
        """

        self._validate_digest(result_digest)
        identity = self.repository.get_operation_identity(operation_id=operation_id)
        if identity is None:
            raise W2CommitOperationNotFoundError("W2 commit operation does not exist")
        context = self._lock_context(
            owner_user_id=identity.owner_user_id,
            job_id=identity.job_id,
            command_id=identity.command_id,
        )
        operation = context.operation
        if operation is None or operation.id != operation_id:
            raise W2CommitOperationNotFoundError("W2 commit operation does not match command")
        if (
            command_id != operation.command_id
            or job_id != operation.job_id
            or execution_fence != operation.execution_fence
            or owner_deletion_epoch != operation.owner_deletion_epoch
            or result_digest != operation.result_digest
        ):
            raise W2CommitGateCurrentnessError("W2 ACK does not match immutable operation binding")

        transitions = {
            "PREPARE": ("PREPARE_PENDING", "PREPARED"),
            "FINALIZE": ("FINALIZE_PENDING", "FINALIZED"),
            "ABORT": ("ABORT_PENDING", "ABORTED"),
            "PURGE": ("PURGE_PENDING", "PURGED"),
        }
        expected_state = transitions.get(action)
        if expected_state is None or operation.state != expected_state[0]:
            raise W2CommitGateTransitionError("W2 ACK action is not valid for operation state")
        if action == "PURGE":
            if (
                purge_owner_deletion_epoch is None
                or purge_owner_deletion_epoch != operation.purge_owner_deletion_epoch
                or context.owner.deletion_epoch != purge_owner_deletion_epoch
            ):
                raise W2CommitGateCurrentnessError(
                    "PURGE ACK does not match the committed deletion epoch"
                )
        elif purge_owner_deletion_epoch is not None:
            raise W2CommitGateCurrentnessError("only PURGE ACKs may carry a deletion epoch")
        return self._advance_locked_operation(
            context=context,
            operation=operation,
            expected_revision=operation_revision,
            target_state=expected_state[1],
        )

    def recover_operation(self, *, operation_id: UUID) -> W2CommitGateRecovery:
        """Recover exactly one non-terminal operation under the common lock order.

        Existing PREPARE/FINALIZE/ABORT/PURGE messages are only made pending
        again on their original Outbox row; the durable message ID and operation
        revision are therefore unchanged.  The two state-changing recovery
        choices are deliberately narrow: PREPARED becomes ABORT_PENDING, while
        a legacy/crash-persisted W1_COMMITTED row gains FINALIZE_PENDING.
        """

        identity = self.repository.get_operation_identity(operation_id=operation_id)
        if identity is None:
            raise W2CommitOperationNotFoundError("W2 commit operation does not exist")
        context = self._lock_context(
            owner_user_id=identity.owner_user_id,
            job_id=identity.job_id,
            command_id=identity.command_id,
        )
        operation = context.operation
        if operation is None or operation.id != operation_id:
            raise W2CommitOperationNotFoundError("W2 commit operation does not match command")
        return self._recover_locked_operation(context=context, operation=operation)

    def recover_due_operations(self, *, limit: int) -> tuple[W2CommitGateRecovery, ...]:
        """Recover at most ``limit`` due durable operations in deterministic order."""

        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("recovery limit must be positive")
        identities = self.repository.list_recovery_operation_identities(limit=limit)
        recovered: list[W2CommitGateRecovery] = []
        for identity in identities:
            try:
                recovered.append(self.recover_operation(operation_id=identity.id))
            except W2CommitGateError:
                # Another transaction may have terminally advanced a candidate
                # after the identity read.  It is safe to skip and retry later.
                continue
        return tuple(recovered)

    def abort_open_operations_for_cancellation(
        self, *, owner_user_id: UUID, job_id: UUID
    ) -> tuple[OutboxMessage, ...]:
        """Durably stage ABORT before a Job cancellation invalidates its command.

        Only work that has not crossed W1's own commit point is abortable.  The
        caller remains in the same transaction, so the ABORT outbox write and
        subsequent Job fence/cancellation either commit together or not at all.
        """

        messages: list[OutboxMessage] = []
        identities = self.repository.list_open_operation_identities_for_job(
            owner_user_id=owner_user_id,
            job_id=job_id,
        )
        for identity in identities:
            context = self._lock_context(
                owner_user_id=owner_user_id,
                job_id=job_id,
                command_id=identity.command_id,
            )
            operation = context.operation
            if (
                operation is None
                or operation.id != identity.id
                or operation.state not in {"PREPARE_PENDING", "PREPARED"}
            ):
                continue
            self._advance_locked_operation(
                context=context,
                operation=operation,
                expected_revision=operation.operation_revision,
                target_state="ABORT_PENDING",
            )
            messages.append(self._add_commit_gate_outbox(operation=operation, action="ABORT"))
        return tuple(messages)

    def reconcile_open_operations_for_owner_deletion(
        self,
        *,
        owner_user_id: UUID,
        job_id: UUID,
        purge_owner_deletion_epoch: int,
    ) -> tuple[OutboxMessage, ...]:
        """Stage ABORT/PURGE after an owner deletion epoch is advanced.

        PREPARE-stage work has no W1-owned result and is aborted.  Once W1 has
        committed (including a previously FINALIZED private result), the action
        is PURGE and binds the *new* deletion epoch while retaining the original
        command epoch for immutable W2 correlation.
        """

        messages: list[OutboxMessage] = []
        identities = self.repository.list_open_operation_identities_for_job(
            owner_user_id=owner_user_id,
            job_id=job_id,
        )
        for identity in identities:
            context = self._lock_context(
                owner_user_id=owner_user_id,
                job_id=job_id,
                command_id=identity.command_id,
            )
            operation = context.operation
            if operation is None or operation.id != identity.id:
                continue
            if operation.state in {"PREPARE_PENDING", "PREPARED"}:
                self._advance_locked_operation(
                    context=context,
                    operation=operation,
                    expected_revision=operation.operation_revision,
                    target_state="ABORT_PENDING",
                )
                messages.append(self._add_commit_gate_outbox(operation=operation, action="ABORT"))
            elif operation.state in {"W1_COMMITTED", "FINALIZE_PENDING", "FINALIZED"}:
                self._advance_locked_operation(
                    context=context,
                    operation=operation,
                    expected_revision=operation.operation_revision,
                    target_state="PURGE_PENDING",
                    purge_owner_deletion_epoch=purge_owner_deletion_epoch,
                )
                messages.append(self._add_commit_gate_outbox(operation=operation, action="PURGE"))
        return tuple(messages)

    def finalize_prepared_operation(
        self,
        *,
        owner_user_id: UUID,
        job_id: UUID,
        command_id: UUID,
        operation_id: UUID,
        expected_revision: int,
        apply_w1_owned: Callable[[LockedW2CommitGateContext], None],
    ) -> W1CommitFinalization:
        """Commit W1-owned result/checkpoint work before emitting FINALIZE.

        ``apply_w1_owned`` is invoked inside this transaction after the same
        currentness locks are held.  If it raises, neither W1 data, operation
        state, nor FINALIZE outbox survives.  The eventual W2 adapter supplies
        the concrete result/checkpoint writer after its staged-result contract
        is adopted.
        """

        context = self._lock_context(
            owner_user_id=owner_user_id,
            job_id=job_id,
            command_id=command_id,
        )
        operation = context.operation
        if operation is None or operation.id != operation_id:
            raise W2CommitOperationNotFoundError("W2 commit operation does not match command")
        if operation.state != "PREPARED" or operation.operation_revision != expected_revision:
            raise W2CommitGateCurrentnessError(
                "W2 commit operation is not prepared at expected revision"
            )
        self._require_execution_current(
            context=context,
            execution_fence=operation.execution_fence,
            owner_deletion_epoch=operation.owner_deletion_epoch,
        )
        self._require_active_runtime_lease(
            context=context,
            execution_lease_id=context.job.active_lease_id,
        )

        apply_w1_owned(context)
        self._advance_locked_operation(
            context=context,
            operation=operation,
            expected_revision=expected_revision,
            target_state="W1_COMMITTED",
        )
        self._advance_locked_operation(
            context=context,
            operation=operation,
            expected_revision=operation.operation_revision,
            target_state="FINALIZE_PENDING",
        )
        finalize_outbox = self._add_commit_gate_outbox(operation=operation, action="FINALIZE")
        return W1CommitFinalization(operation=operation, finalize_outbox=finalize_outbox)

    def _recover_locked_operation(
        self,
        *,
        context: LockedW2CommitGateContext,
        operation: W2CommitOperation,
    ) -> W2CommitGateRecovery:
        if operation.state == "PREPARE_PENDING":
            try:
                self._require_execution_current(
                    context=context,
                    execution_fence=operation.execution_fence,
                    owner_deletion_epoch=operation.owner_deletion_epoch,
                )
                self._require_active_runtime_lease(
                    context=context,
                    execution_lease_id=context.job.active_lease_id,
                )
            except W2CommitGateCurrentnessError:
                self._advance_locked_operation(
                    context=context,
                    operation=operation,
                    expected_revision=operation.operation_revision,
                    target_state="ABORT_PENDING",
                )
                outbox = self._add_commit_gate_outbox(operation=operation, action="ABORT")
                return W2CommitGateRecovery(
                    operation=operation,
                    action="ABORT",
                    outbox=outbox,
                    requeued=False,
                )
            outbox = self._requeue_gate_outbox(operation=operation, action="PREPARE")
            return W2CommitGateRecovery(
                operation=operation,
                action="PREPARE",
                outbox=outbox,
                requeued=True,
            )

        if operation.state == "PREPARED":
            self._advance_locked_operation(
                context=context,
                operation=operation,
                expected_revision=operation.operation_revision,
                target_state="ABORT_PENDING",
            )
            outbox = self._add_commit_gate_outbox(operation=operation, action="ABORT")
            return W2CommitGateRecovery(
                operation=operation,
                action="ABORT",
                outbox=outbox,
                requeued=False,
            )

        if operation.state == "W1_COMMITTED":
            if context.owner.deletion_epoch > operation.owner_deletion_epoch:
                self._advance_locked_operation(
                    context=context,
                    operation=operation,
                    expected_revision=operation.operation_revision,
                    target_state="PURGE_PENDING",
                    purge_owner_deletion_epoch=context.owner.deletion_epoch,
                )
                outbox = self._add_commit_gate_outbox(operation=operation, action="PURGE")
                return W2CommitGateRecovery(
                    operation=operation,
                    action="PURGE",
                    outbox=outbox,
                    requeued=False,
                )
            # W1's owned write is already durable.  A recovery after the old
            # W1_COMMITTED/FINALIZE outbox crash point must finish that protocol
            # without treating a later Job fence as permission to erase history.
            self._advance_locked_operation(
                context=context,
                operation=operation,
                expected_revision=operation.operation_revision,
                target_state="FINALIZE_PENDING",
                allow_post_commit_finalization=True,
            )
            outbox = self._add_commit_gate_outbox(operation=operation, action="FINALIZE")
            return W2CommitGateRecovery(
                operation=operation,
                action="FINALIZE",
                outbox=outbox,
                requeued=False,
            )

        if operation.state == "FINALIZE_PENDING":
            if context.owner.deletion_epoch > operation.owner_deletion_epoch:
                self._advance_locked_operation(
                    context=context,
                    operation=operation,
                    expected_revision=operation.operation_revision,
                    target_state="PURGE_PENDING",
                    purge_owner_deletion_epoch=context.owner.deletion_epoch,
                )
                outbox = self._add_commit_gate_outbox(operation=operation, action="PURGE")
                return W2CommitGateRecovery(
                    operation=operation,
                    action="PURGE",
                    outbox=outbox,
                    requeued=False,
                )
            outbox = self._requeue_gate_outbox(operation=operation, action="FINALIZE")
            return W2CommitGateRecovery(
                operation=operation,
                action="FINALIZE",
                outbox=outbox,
                requeued=True,
            )

        if operation.state in {"ABORT_PENDING", "PURGE_PENDING"}:
            action = "ABORT" if operation.state == "ABORT_PENDING" else "PURGE"
            if (
                action == "PURGE"
                and context.owner.deletion_epoch != operation.purge_owner_deletion_epoch
            ):
                raise W2CommitGateCurrentnessError("PURGE recovery epoch is no longer current")
            outbox = self._requeue_gate_outbox(operation=operation, action=action)
            return W2CommitGateRecovery(
                operation=operation,
                action=action,
                outbox=outbox,
                requeued=True,
            )

        # Terminal states have no replay command.  Returning a structured no-op
        # lets the worker report the skip without fabricating another message.
        return W2CommitGateRecovery(
            operation=operation,
            action=None,
            outbox=None,
            requeued=False,
        )

    def lock_operation_context(
        self, *, owner_user_id: UUID, job_id: UUID, command_id: UUID
    ) -> LockedW2CommitGateContext:
        """Expose the shared lock primitive for a future cancel/delete transaction."""

        return self._lock_context(
            owner_user_id=owner_user_id,
            job_id=job_id,
            command_id=command_id,
        )

    def _lock_context(
        self, *, owner_user_id: UUID, job_id: UUID, command_id: UUID
    ) -> LockedW2CommitGateContext:
        # Keep this exact sequence aligned with LOCK_ORDER.  Do not re-order a
        # subset in cancellation, deletion, result, or ACK code paths.
        owner = self.repository.get_owner_for_update(owner_user_id=owner_user_id)
        if owner is None:
            raise W2CommitGateCurrentnessError("commit gate owner does not exist")
        job = self.repository.get_job_for_update(job_id=job_id, owner_user_id=owner_user_id)
        if job is None:
            raise W2CommitGateCurrentnessError("commit gate Job does not match owner")
        command = self.repository.get_command_for_update(
            command_id=command_id,
            job_id=job_id,
            owner_user_id=owner_user_id,
        )
        if command is None:
            raise W2CommitGateCurrentnessError("commit gate command does not match Job")
        operation = self.repository.get_operation_for_update(command_id=command_id)

        lease = None
        slot = None
        if job.active_lease_id is not None:
            lease = self.repository.get_lease_for_update(
                lease_id=job.active_lease_id,
                owner_user_id=owner_user_id,
            )
            if lease is None:
                raise W2CommitGateCurrentnessError("Job references no active execution lease")
            slot = self.repository.get_slot_for_update(
                owner_user_id=owner_user_id,
                slot_no=lease.slot_no,
            )
            if slot is None or slot.lease_id != lease.id or slot.job_id != job.id:
                raise W2CommitGateCurrentnessError("active execution lease does not own its slot")

        return LockedW2CommitGateContext(
            owner=owner,
            job=job,
            command=command,
            operation=operation,
            lease=lease,
            slot=slot,
        )

    def _advance_locked_operation(
        self,
        *,
        context: LockedW2CommitGateContext,
        operation: W2CommitOperation,
        expected_revision: int,
        target_state: str,
        purge_owner_deletion_epoch: int | None = None,
        allow_post_commit_finalization: bool = False,
    ) -> W2CommitOperation:
        if operation.operation_revision != expected_revision:
            raise W2CommitGateCurrentnessError("W2 commit operation revision is stale")
        if target_state not in _FORWARD_TRANSITIONS.get(operation.state, set()):
            raise W2CommitGateTransitionError(
                f"cannot transition W2 commit operation from {operation.state} to {target_state}"
            )
        if target_state in _EXECUTION_CURRENT_STATES and not allow_post_commit_finalization:
            self._require_execution_current(
                context=context,
                execution_fence=operation.execution_fence,
                owner_deletion_epoch=operation.owner_deletion_epoch,
            )
        elif target_state == "PURGE_PENDING":
            self._require_purge_currentness(
                context=context,
                operation=operation,
                purge_owner_deletion_epoch=purge_owner_deletion_epoch,
            )
        else:
            self._require_immutable_command_binding(context=context, operation=operation)

        operation.state = target_state
        operation.operation_revision += 1
        operation.updated_at = datetime.now(UTC)
        timestamp_field = _STATE_TIMESTAMP_FIELDS.get(target_state)
        if timestamp_field is not None:
            setattr(operation, timestamp_field, operation.updated_at)
        if target_state == "PURGE_PENDING":
            operation.purge_owner_deletion_epoch = purge_owner_deletion_epoch
        self.session.flush()
        return operation

    def _requeue_gate_outbox(
        self, *, operation: W2CommitOperation, action: str
    ) -> OutboxMessage:
        """Return one prior command to PENDING without changing its ID or revision."""

        outbox = self.repository.get_gate_outbox_for_update(
            operation_id=operation.id,
            operation_revision=operation.operation_revision,
            action=action,
        )
        if outbox is None:
            raise W2CommitGateCurrentnessError(
                "recovery cannot fabricate a replacement gate message"
            )
        payload = outbox.payload
        if (
            not isinstance(payload, dict)
            or payload.get("message_id") != str(outbox.id)
            or payload.get("operation_id") != str(operation.id)
            or payload.get("operation_revision") != operation.operation_revision
            or payload.get("action") != action
        ):
            raise W2CommitGateCurrentnessError("recovery outbox binding is corrupt")
        outbox.status = "PENDING"
        # The relay claims due work with PostgreSQL's ``now()``.  Use the same
        # clock for a recovery requeue so an app/DB clock skew cannot postpone
        # a command that recovery intentionally made immediately deliverable.
        outbox.available_at = func.now()
        outbox.relay_claim_token = None
        outbox.relay_claimed_by = None
        outbox.relay_lease_expires_at = None
        outbox.last_error_code = None
        outbox.last_error_at = None
        self.session.flush()
        return outbox

    def _add_commit_gate_outbox(
        self, *, operation: W2CommitOperation, action: str
    ) -> OutboxMessage:
        message_id = uuid4()
        now = datetime.now(UTC)
        payload: dict[str, object] = {
            "schema_version": "w1.private.w2.commit-gate.v1",
            "message_id": str(message_id),
            "message_type": "w1.private.w2.commit-gate.v1",
            "producer": "w1",
            "visibility_scope": "PRIVATE",
            "operation_id": str(operation.id),
            "operation_revision": operation.operation_revision,
            "action": action,
            "command_id": str(operation.command_id),
            "job_id": str(operation.job_id),
            "authenticated_owner_ref": str(operation.owner_user_id),
            "execution_fence": operation.execution_fence,
            "owner_deletion_epoch": operation.owner_deletion_epoch,
            "result_digest": operation.result_digest,
            "issued_at": now.isoformat().replace("+00:00", "Z"),
        }
        if action == "PURGE":
            if operation.purge_owner_deletion_epoch is None:
                raise W2CommitGateCurrentnessError("PURGE outbox requires a deletion epoch")
            payload["purge_owner_deletion_epoch"] = operation.purge_owner_deletion_epoch
        message = OutboxMessage(
            id=message_id,
            message_type="w1.private.w2.commit-gate.v1",
            schema_version="w1.private.w2.commit-gate.v1",
            visibility_scope="PRIVATE",
            aggregate_type="W2_COMMIT_OPERATION",
            aggregate_id=operation.id,
            aggregate_revision=operation.operation_revision,
            command_id=operation.command_id,
            job_id=operation.job_id,
            owner_user_id=operation.owner_user_id,
            execution_fence=operation.execution_fence,
            owner_deletion_epoch=operation.owner_deletion_epoch,
            payload=payload,
        )
        self.session.add(message)
        self.session.flush()
        return message

    @staticmethod
    def _validate_digest(result_digest: str) -> None:
        if not _DIGEST_PATTERN.fullmatch(result_digest):
            raise W2CommitGateCurrentnessError("result digest must be a lowercase sha256 digest")

    @staticmethod
    def _require_immutable_command_binding(
        *, context: LockedW2CommitGateContext, operation: W2CommitOperation
    ) -> None:
        command = context.command
        if (
            operation.command_id != command.id
            or operation.job_id != context.job.id
            or operation.owner_user_id != context.owner.id
            or operation.execution_fence != command.execution_fence
            or operation.owner_deletion_epoch != command.owner_deletion_epoch
        ):
            raise W2CommitGateCurrentnessError(
                "commit operation no longer matches its immutable command"
            )

    def _require_execution_current(
        self,
        *,
        context: LockedW2CommitGateContext,
        execution_fence: int,
        owner_deletion_epoch: int,
    ) -> None:
        if (
            context.owner.account_status != "ACTIVE"
            or context.owner.deletion_epoch != owner_deletion_epoch
            or context.job.execution_fence != execution_fence
            or context.job.owner_deletion_epoch != owner_deletion_epoch
            or context.command.execution_fence != execution_fence
            or context.command.owner_deletion_epoch != owner_deletion_epoch
            or context.command.status in {"INVALIDATED", "FAILED"}
        ):
            raise W2CommitGateCurrentnessError("W2 commit gate command is no longer current")

    @staticmethod
    def _require_active_runtime_lease(
        *, context: LockedW2CommitGateContext, execution_lease_id: UUID | None
    ) -> None:
        lease = context.lease
        slot = context.slot
        if (
            execution_lease_id is None
            or lease is None
            or slot is None
            or context.job.status != "RUNNING"
            or context.job.active_lease_id != execution_lease_id
            or lease.id != execution_lease_id
            or lease.released_at is not None
            or lease.execution_fence != context.command.execution_fence
            or lease.owner_deletion_epoch != context.command.owner_deletion_epoch
            or context.command.status not in {"PENDING", "ENQUEUED"}
        ):
            raise W2CommitGateCurrentnessError(
                "W2 commit gate execution lease is no longer current"
            )

    @staticmethod
    def _require_existing_binding(
        *,
        operation: W2CommitOperation,
        context: LockedW2CommitGateContext,
        execution_fence: int,
        owner_deletion_epoch: int,
        result_digest: str,
    ) -> None:
        if (
            operation.job_id != context.job.id
            or operation.owner_user_id != context.owner.id
            or operation.execution_fence != execution_fence
            or operation.owner_deletion_epoch != owner_deletion_epoch
            or operation.result_digest != result_digest
        ):
            raise W2CommitGateCurrentnessError(
                "a different W2 commit binding already exists for this command"
            )

    @staticmethod
    def _require_purge_currentness(
        *,
        context: LockedW2CommitGateContext,
        operation: W2CommitOperation,
        purge_owner_deletion_epoch: int | None,
    ) -> None:
        if (
            purge_owner_deletion_epoch is None
            or purge_owner_deletion_epoch <= operation.owner_deletion_epoch
            or context.owner.deletion_epoch != purge_owner_deletion_epoch
        ):
            raise W2CommitGateCurrentnessError("PURGE must bind the owner's newer deletion epoch")
