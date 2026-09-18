from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.identity import User
from app.models.jobs import (
    InboxReceipt,
    Job,
    JobCommand,
    JobExecutionLease,
    JobInputRef,
    JobRequiredAction,
    OutboxMessage,
    OwnerExecutionSlot,
)
from app.models.lifecycle_operations import JobCheckpoint


class JobRepository:
    """Locked persistence primitives for Job acceptance and execution ownership."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_owner_for_update(self, *, owner_user_id: UUID) -> User | None:
        return self.session.scalar(select(User).where(User.id == owner_user_id).with_for_update())

    def get_job_for_update(self, *, job_id: UUID, owner_user_id: UUID) -> Job | None:
        statement = (
            select(Job)
            .where(Job.id == job_id, Job.owner_user_id == owner_user_id)
            .with_for_update()
        )
        return self.session.scalar(statement)

    def get_job_by_idempotency_record(self, *, idempotency_record_id: UUID) -> Job | None:
        return self.session.scalar(
            select(Job).where(Job.idempotency_record_id == idempotency_record_id)
        )

    def get_job(self, *, job_id: UUID, owner_user_id: UUID) -> Job | None:
        return self.session.scalar(
            select(Job).where(Job.id == job_id, Job.owner_user_id == owner_user_id)
        )

    def get_job_by_id(self, *, job_id: UUID) -> Job | None:
        """Read only enough ownership metadata to establish the User -> Job lock order.

        The inbound decision service treats this as a hint, then locks the owner and
        reloads the Job with the owner predicate before trusting any mutable state.
        """

        return self.session.scalar(select(Job).where(Job.id == job_id))

    def list_jobs(
        self, *, owner_user_id: UUID, offset: int, limit: int
    ) -> list[Job]:
        statement = (
            select(Job)
            .where(Job.owner_user_id == owner_user_id)
            .order_by(Job.updated_at.desc(), Job.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.session.scalars(statement))

    def list_input_refs(self, *, job_id: UUID, owner_user_id: UUID) -> list[JobInputRef]:
        statement = (
            select(JobInputRef)
            .where(JobInputRef.job_id == job_id, JobInputRef.owner_user_id == owner_user_id)
            .order_by(JobInputRef.created_at, JobInputRef.id)
        )
        return list(self.session.scalars(statement))

    def list_open_required_actions(
        self, *, job_id: UUID, owner_user_id: UUID
    ) -> list[JobRequiredAction]:
        statement = (
            select(JobRequiredAction)
            .where(
                JobRequiredAction.job_id == job_id,
                JobRequiredAction.owner_user_id == owner_user_id,
                JobRequiredAction.action_status == "OPEN",
                JobRequiredAction.resolved_at.is_(None),
            )
            .order_by(JobRequiredAction.created_at, JobRequiredAction.id)
        )
        return list(self.session.scalars(statement))

    def get_required_action_for_update(
        self, *, action_id: UUID, job_id: UUID, owner_user_id: UUID
    ) -> JobRequiredAction | None:
        statement = (
            select(JobRequiredAction)
            .where(
                JobRequiredAction.id == action_id,
                JobRequiredAction.job_id == job_id,
                JobRequiredAction.owner_user_id == owner_user_id,
            )
            .with_for_update()
        )
        return self.session.scalar(statement)

    def get_current_checkpoint(
        self, *, job: Job, for_update: bool = False
    ) -> JobCheckpoint | None:
        statement = (
            select(JobCheckpoint)
            .where(
                JobCheckpoint.job_id == job.id,
                JobCheckpoint.owner_user_id == job.owner_user_id,
                JobCheckpoint.execution_fence == job.execution_fence,
                JobCheckpoint.owner_deletion_epoch == job.owner_deletion_epoch,
                JobCheckpoint.analysis_input_version.is_not_distinct_from(job.analysis_input_version),
                JobCheckpoint.resumable.is_(True),
            )
            .order_by(JobCheckpoint.checkpoint_revision.desc())
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def get_latest_command(self, *, job_id: UUID) -> JobCommand | None:
        statement = (
            select(JobCommand)
            .where(JobCommand.job_id == job_id)
            .order_by(JobCommand.command_sequence.desc())
            .limit(1)
        )
        return self.session.scalar(statement)

    def get_latest_outbox_message(self, *, command_id: UUID) -> OutboxMessage | None:
        statement = (
            select(OutboxMessage)
            .where(OutboxMessage.command_id == command_id)
            .order_by(OutboxMessage.created_at.desc())
            .limit(1)
        )
        return self.session.scalar(statement)

    def get_latest_dispatch_outbox_message(self, *, command_id: UUID) -> OutboxMessage | None:
        """Return the command dispatch record, never a W2 commit-gate control message."""

        return self.session.scalar(
            select(OutboxMessage)
            .where(
                OutboxMessage.command_id == command_id,
                OutboxMessage.message_type != "w1.private.w2.commit-gate.v1",
            )
            .order_by(OutboxMessage.created_at.desc())
            .limit(1)
        )

    def add_job(self, job: Job) -> None:
        self.session.add(job)

    def add_input_ref(self, input_ref: JobInputRef) -> None:
        self.session.add(input_ref)

    def add_required_action(self, action: JobRequiredAction) -> None:
        self.session.add(action)

    def add_command(self, command: JobCommand) -> None:
        self.session.add(command)

    def add_outbox_message(self, message: OutboxMessage) -> None:
        self.session.add(message)

    def add_inbox_receipt(self, receipt: InboxReceipt) -> None:
        self.session.add(receipt)

    def get_inbox_receipt(
        self, *, consumer_name: str, event_id: UUID, for_update: bool = False
    ) -> InboxReceipt | None:
        statement = select(InboxReceipt).where(
            InboxReceipt.consumer_name == consumer_name,
            InboxReceipt.event_id == event_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def reserve_inbox_receipt(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        outcome_code: str,
        payload_digest: str,
        producer_name: str,
        schema_version: str,
    ) -> tuple[InboxReceipt, bool]:
        statement = (
            insert(InboxReceipt)
            .values(
                consumer_name=consumer_name,
                event_id=event_id,
                outcome_code=outcome_code,
                payload_digest=payload_digest,
                producer_name=producer_name,
                schema_version=schema_version,
            )
            .on_conflict_do_nothing(index_elements=["consumer_name", "event_id"])
            .returning(InboxReceipt.event_id)
        )
        inserted = self.session.execute(statement).scalar_one_or_none() is not None
        receipt = self.get_inbox_receipt(
            consumer_name=consumer_name,
            event_id=event_id,
            for_update=True,
        )
        if receipt is None:
            raise RuntimeError("inbox receipt reservation did not produce a readable row")
        return receipt, inserted

    def record_inbox_receipt(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        outcome_code: str,
        payload_digest: str | None = None,
        producer_name: str | None = None,
        schema_version: str | None = None,
    ) -> bool:
        statement = (
            insert(InboxReceipt)
            .values(
                consumer_name=consumer_name,
                event_id=event_id,
                outcome_code=outcome_code,
                payload_digest=payload_digest,
                producer_name=producer_name,
                schema_version=schema_version,
            )
            .on_conflict_do_nothing(index_elements=["consumer_name", "event_id"])
            .returning(InboxReceipt.event_id)
        )
        return self.session.execute(statement).scalar_one_or_none() is not None

    def update_inbox_receipt_outcome(
        self, *, consumer_name: str, event_id: UUID, outcome_code: str
    ) -> None:
        self.session.execute(
            update(InboxReceipt)
            .where(
                InboxReceipt.consumer_name == consumer_name,
                InboxReceipt.event_id == event_id,
            )
            .values(outcome_code=outcome_code)
        )

    def get_open_required_action_for_update(
        self, *, job_id: UUID, owner_user_id: UUID, action_code: str
    ) -> JobRequiredAction | None:
        statement = (
            select(JobRequiredAction)
            .where(
                JobRequiredAction.job_id == job_id,
                JobRequiredAction.owner_user_id == owner_user_id,
                JobRequiredAction.action_code == action_code,
                JobRequiredAction.resolved_at.is_(None),
            )
            .with_for_update()
        )
        return self.session.scalar(statement)

    def dismiss_open_required_actions(self, *, job_id: UUID, owner_user_id: UUID) -> None:
        now = datetime.now(UTC)
        self.session.execute(
            update(JobRequiredAction)
            .where(
                JobRequiredAction.job_id == job_id,
                JobRequiredAction.owner_user_id == owner_user_id,
                JobRequiredAction.action_status == "OPEN",
                JobRequiredAction.resolved_at.is_(None),
            )
            .values(action_status="DISMISSED", resolved_at=now)
        )

    def ensure_owner_slots(self, *, owner_user_id: UUID) -> list[OwnerExecutionSlot]:
        slots = list(
            self.session.scalars(
                select(OwnerExecutionSlot)
                .where(OwnerExecutionSlot.owner_user_id == owner_user_id)
                .order_by(OwnerExecutionSlot.slot_no)
                .with_for_update()
            )
        )
        existing_slot_numbers = {slot.slot_no for slot in slots}
        for slot_no in (1, 2, 3):
            if slot_no not in existing_slot_numbers:
                slot = OwnerExecutionSlot(owner_user_id=owner_user_id, slot_no=slot_no)
                self.session.add(slot)
                slots.append(slot)
        if len(existing_slot_numbers) != 3:
            self.session.flush()
            slots.sort(key=lambda slot: slot.slot_no)
        return slots

    def claim_available_slot(self, *, owner_user_id: UUID) -> OwnerExecutionSlot | None:
        statement = (
            select(OwnerExecutionSlot)
            .where(
                OwnerExecutionSlot.owner_user_id == owner_user_id,
                OwnerExecutionSlot.job_id.is_(None),
            )
            .order_by(OwnerExecutionSlot.slot_no)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        return self.session.scalar(statement)

    def get_slot_for_update(
        self, *, owner_user_id: UUID, slot_no: int
    ) -> OwnerExecutionSlot | None:
        statement = (
            select(OwnerExecutionSlot)
            .where(
                OwnerExecutionSlot.owner_user_id == owner_user_id,
                OwnerExecutionSlot.slot_no == slot_no,
            )
            .with_for_update()
        )
        return self.session.scalar(statement)

    def add_lease(self, lease: JobExecutionLease) -> None:
        self.session.add(lease)

    def get_lease_for_update(
        self, *, lease_id: UUID, owner_user_id: UUID
    ) -> JobExecutionLease | None:
        statement = (
            select(JobExecutionLease)
            .where(
                JobExecutionLease.id == lease_id,
                JobExecutionLease.owner_user_id == owner_user_id,
            )
            .with_for_update()
        )
        return self.session.scalar(statement)
