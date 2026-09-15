from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.identity import IdempotencyRecord
from app.models.jobs import (
    Job,
    JobCommand,
    JobExecutionLease,
    JobInputRef,
    JobRequiredAction,
    OutboxMessage,
)
from app.repo.identity import IdentityRepository
from app.repo.jobs import JobRepository
from app.services.idempotency import IdempotencyService


class JobError(Exception):
    pass


class JobNotFoundError(JobError):
    pass


class JobTransitionError(JobError):
    pass


class JobIdempotencyReplayIncompleteError(JobError):
    pass


@dataclass(frozen=True)
class JobInputReference:
    """A typed P0 input reference; raw source content is deliberately not accepted here."""

    project_version_id: UUID | None = None
    question_version_id: UUID | None = None
    episode_version_id: UUID | None = None
    snapshot_id: UUID | None = None
    policy_name: str | None = None
    policy_version: str | None = None


@dataclass(frozen=True)
class JobAcceptance:
    job: Job
    command: JobCommand | None
    outbox_message: OutboxMessage | None
    replayed: bool


class JobService:
    """One-transaction Job state machine and execution-fence orchestrator.

    The caller owns the surrounding transaction and its RLS owner context.  A successful return
    from ``accept_job`` means Job, command, and Outbox rows have been flushed together; it never
    claims a worker or implies a broker delivery.
    """

    _IDEMPOTENCY_TTL = timedelta(days=1)
    _PRIVATE_COMMAND_SCHEMA_VERSION = "1.0"
    _PRIVATE_DISPATCH_MESSAGE_TYPE = "job.command.dispatch"

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = JobRepository(session)
        self.idempotency = IdempotencyService(IdentityRepository(session))

    def accept_job(
        self,
        *,
        owner_user_id: UUID,
        job_type: str,
        idempotency_key: str,
        request_hash: str,
        project_id: UUID | None = None,
        analysis_input_version: str | None = None,
        input_refs: Iterable[JobInputReference] = (),
        method: str = "POST",
        path_scope: str = "/jobs",
    ) -> JobAcceptance:
        self._require_nonempty(job_type, "job type")
        self._require_nonempty(idempotency_key, "idempotency key")
        self._require_nonempty(request_hash, "request hash")
        owner = self.repository.get_owner_for_update(owner_user_id=owner_user_id)
        if owner is None:
            raise JobNotFoundError("job owner does not exist")
        record, replayed = self.idempotency.reserve(
            owner_user_id=owner_user_id,
            method=method,
            path_scope=path_scope,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            expires_at=datetime.now(UTC) + self._IDEMPOTENCY_TTL,
        )
        if replayed:
            return self._replayed_acceptance(record)

        self.repository.ensure_owner_slots(owner_user_id=owner_user_id)
        job = Job(
            owner_user_id=owner_user_id,
            project_id=project_id,
            job_type=job_type,
            owner_deletion_epoch=owner.deletion_epoch,
            analysis_input_version=analysis_input_version,
            idempotency_record_id=record.id,
        )
        self.repository.add_job(job)
        self.session.flush()
        for input_reference in input_refs:
            self.repository.add_input_ref(self._to_model_input_ref(job, input_reference))
        command, outbox_message = self._create_private_command_and_outbox(
            job=job,
            command_type="EXECUTE_JOB",
            command_sequence=1,
        )
        self.session.flush()
        IdempotencyService.complete(record, response_status=202, response_ref=f"job:{job.id}")
        self.session.flush()
        return JobAcceptance(
            job=job,
            command=command,
            outbox_message=outbox_message,
            replayed=False,
        )

    def mark_dispatch_enqueued(self, *, owner_user_id: UUID, job_id: UUID) -> Job:
        """Record a committed relay handoff without claiming a worker execution slot."""
        job = self._require_job_for_update(owner_user_id=owner_user_id, job_id=job_id)
        if job.status != "QUEUED" or job.dispatch_status != "OUTBOX_PENDING":
            raise JobTransitionError("only a newly accepted Job can be marked enqueued")
        command = self.repository.get_latest_command(job_id=job.id)
        if command is None or command.status != "PENDING":
            raise JobTransitionError("Job has no pending dispatch command")
        outbox_message = self.repository.get_latest_outbox_message(command_id=command.id)
        if outbox_message is None or outbox_message.status != "PENDING":
            raise JobTransitionError("Job has no pending dispatch outbox message")
        command.status = "ENQUEUED"
        outbox_message.status = "PUBLISHED"
        outbox_message.published_at = datetime.now(UTC)
        job.dispatch_status = "ENQUEUED"
        job.updated_at = datetime.now(UTC)
        self.session.flush()
        return job

    def claim_execution(
        self, *, owner_user_id: UUID, job_id: UUID, worker_ref: str | None = None
    ) -> JobExecutionLease | None:
        job = self._require_job_for_update(owner_user_id=owner_user_id, job_id=job_id)
        if job.status != "QUEUED" or job.dispatch_status != "ENQUEUED":
            return None
        slot = self.repository.claim_available_slot(owner_user_id=owner_user_id)
        if slot is None:
            return None
        now = datetime.now(UTC)
        lease = JobExecutionLease(
            job_id=job.id,
            owner_user_id=owner_user_id,
            slot_no=slot.slot_no,
            execution_fence=job.execution_fence,
            owner_deletion_epoch=job.owner_deletion_epoch,
            worker_ref=worker_ref,
            claimed_at=now,
        )
        self.repository.add_lease(lease)
        self.session.flush()
        slot.job_id = job.id
        slot.lease_id = lease.id
        slot.claimed_at = now
        slot.updated_at = now
        job.active_lease_id = lease.id
        job.status = "RUNNING"
        job.dispatch_status = "CLAIMED"
        job.started_at = now
        job.updated_at = now
        command = self.repository.get_latest_command(job_id=job.id)
        if command is None or command.status != "ENQUEUED":
            raise JobTransitionError("the claimed Job has no enqueued command")
        command.status = "CLAIMED"
        self.session.flush()
        return lease

    def request_cancellation(self, *, owner_user_id: UUID, job_id: UUID) -> Job:
        job = self._require_job_for_update(owner_user_id=owner_user_id, job_id=job_id)
        if job.status == "RUNNING":
            self._invalidate_current_command(job)
            job.status = "CANCEL_REQUESTED"
            job.execution_fence += 1
            job.dispatch_status = "OUTBOX_PENDING"
            self._create_private_command_and_outbox(
                job=job,
                command_type="CANCEL_JOB",
                command_sequence=self._next_command_sequence(job_id=job.id),
            )
        elif job.status in {"QUEUED", "WAITING_USER", "PAUSED_RATE_LIMIT", "FAILED_RETRYABLE"}:
            self._invalidate_current_command(job)
            job.status = "CANCELLED"
            job.execution_fence += 1
            job.dispatch_status = "INVALIDATED"
            job.completed_at = datetime.now(UTC)
        else:
            raise JobTransitionError("Job cannot be cancelled from its current status")
        job.updated_at = datetime.now(UTC)
        self.session.flush()
        return job

    def retry_job(
        self,
        *,
        owner_user_id: UUID,
        job_id: UUID,
        idempotency_key: str,
        request_hash: str,
        method: str = "POST",
        path_scope: str | None = None,
        checkpoint_id: UUID | None = None,
    ) -> JobAcceptance:
        """Accept one explicit retry without treating it as immediate worker execution."""
        self._require_nonempty(idempotency_key, "idempotency key")
        self._require_nonempty(request_hash, "request hash")
        owner = self.repository.get_owner_for_update(owner_user_id=owner_user_id)
        if owner is None:
            raise JobNotFoundError("job owner does not exist")
        job = self._require_job_for_update(owner_user_id=owner_user_id, job_id=job_id)
        record, replayed = self.idempotency.reserve(
            owner_user_id=owner_user_id,
            method=method,
            path_scope=path_scope or f"/jobs/{job_id}/retry",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            expires_at=datetime.now(UTC) + self._IDEMPOTENCY_TTL,
        )
        if replayed:
            return self._replayed_acceptance(record)
        if job.status not in {"FAILED_RETRYABLE", "PAUSED_RATE_LIMIT"}:
            raise JobTransitionError("only a retryable or rate-limited Job can be retried")
        if job.active_lease_id is not None or job.owner_deletion_epoch != owner.deletion_epoch:
            raise JobTransitionError("Job is not safe to retry for the current owner epoch")
        action = self.repository.get_open_required_action_for_update(
            job_id=job.id, owner_user_id=owner_user_id, action_code="RETRY"
        )
        if action is None:
            raise JobTransitionError("Job has no open retry action")
        job.execution_fence += 1
        job.status = "QUEUED"
        job.dispatch_status = "OUTBOX_PENDING"
        job.retryable = False
        job.retry_after = None
        job.failure_code = None
        job.safe_failure_message = None
        job.completed_at = None
        job.updated_at = datetime.now(UTC)
        command, outbox_message = self._create_private_command_and_outbox(
            job=job,
            command_type="EXECUTE_JOB",
            command_sequence=self._next_command_sequence(job_id=job.id),
            checkpoint_id=checkpoint_id,
        )
        action.action_status = "RESOLVED"
        action.resolved_at = datetime.now(UTC)
        self.session.flush()
        IdempotencyService.complete(record, response_status=202, response_ref=f"job:{job.id}")
        self.session.flush()
        return JobAcceptance(
            job=job,
            command=command,
            outbox_message=outbox_message,
            replayed=False,
        )

    def pause_execution(
        self,
        *,
        owner_user_id: UUID,
        job_id: UUID,
        lease_id: UUID,
        execution_fence: int,
        owner_deletion_epoch: int,
        status: str,
        action_code: str,
    ) -> bool:
        """Release a current worker lease when user input or a rate limit blocks the Job."""
        if status not in {"WAITING_USER", "PAUSED_RATE_LIMIT"}:
            raise JobTransitionError("only user-waiting or rate-limited states can pause a Job")
        owner = self.repository.get_owner_for_update(owner_user_id=owner_user_id)
        job = self._require_job_for_update(owner_user_id=owner_user_id, job_id=job_id)
        if not self._is_current_execution(
            owner=owner,
            job=job,
            lease_id=lease_id,
            execution_fence=execution_fence,
            owner_deletion_epoch=owner_deletion_epoch,
        ):
            return False
        lease = self.repository.get_lease_for_update(lease_id=lease_id, owner_user_id=owner_user_id)
        if lease is None:
            return False
        self._release_lease(job=job, lease=lease, reason=status)
        now = datetime.now(UTC)
        job.status = status
        job.dispatch_status = "BLOCKED"
        job.retryable = status == "PAUSED_RATE_LIMIT"
        job.updated_at = now
        self.repository.add_required_action(
            JobRequiredAction(
                job_id=job.id,
                owner_user_id=owner_user_id,
                action_code=action_code,
                action_status="OPEN",
            )
        )
        self.session.flush()
        return True

    def acknowledge_cancellation(self, *, owner_user_id: UUID, job_id: UUID) -> bool:
        job = self._require_job_for_update(owner_user_id=owner_user_id, job_id=job_id)
        if job.status != "CANCEL_REQUESTED" or job.active_lease_id is None:
            return False
        lease = self.repository.get_lease_for_update(
            lease_id=job.active_lease_id, owner_user_id=owner_user_id
        )
        if lease is None or lease.released_at is not None:
            return False
        self._release_lease(job=job, lease=lease, reason="CANCELLED")
        job.status = "CANCELLED"
        job.dispatch_status = "INVALIDATED"
        job.completed_at = datetime.now(UTC)
        job.updated_at = datetime.now(UTC)
        self.session.flush()
        return True

    def commit_result(
        self,
        *,
        owner_user_id: UUID,
        job_id: UUID,
        lease_id: UUID,
        execution_fence: int,
        owner_deletion_epoch: int,
        final_status: str = "SUCCEEDED",
        completeness: str = "complete",
    ) -> bool:
        """Commit a worker result only while its lease, fence, and owner epoch are current."""
        if final_status not in {"SUCCEEDED", "FAILED_RETRYABLE", "FAILED_FINAL"}:
            raise JobTransitionError("result status is not a terminal worker result")
        if completeness not in {"none", "partial", "complete"}:
            raise JobTransitionError("unknown result completeness")
        owner = self.repository.get_owner_for_update(owner_user_id=owner_user_id)
        job = self._require_job_for_update(owner_user_id=owner_user_id, job_id=job_id)
        if not self._is_current_execution(
            owner=owner,
            job=job,
            lease_id=lease_id,
            execution_fence=execution_fence,
            owner_deletion_epoch=owner_deletion_epoch,
        ):
            return False
        lease = self.repository.get_lease_for_update(lease_id=lease_id, owner_user_id=owner_user_id)
        if (
            lease is None
            or lease.released_at is not None
            or lease.execution_fence != execution_fence
            or lease.owner_deletion_epoch != owner_deletion_epoch
        ):
            return False
        self._release_lease(job=job, lease=lease, reason=final_status)
        now = datetime.now(UTC)
        job.status = final_status
        job.completeness = completeness
        job.dispatch_status = "BLOCKED" if final_status == "FAILED_RETRYABLE" else "ENQUEUED"
        job.completed_at = None if final_status == "FAILED_RETRYABLE" else now
        job.retryable = final_status == "FAILED_RETRYABLE"
        job.updated_at = now
        if final_status == "FAILED_RETRYABLE":
            self.repository.add_required_action(
                JobRequiredAction(
                    job_id=job.id,
                    owner_user_id=owner_user_id,
                    action_code="RETRY",
                    action_status="OPEN",
                )
            )
        self.session.flush()
        return True

    def record_inbox_receipt(
        self, *, consumer_name: str, event_id: UUID, outcome_code: str
    ) -> bool:
        """Return false for an at-least-once redelivery already processed by this consumer."""
        self._require_nonempty(consumer_name, "consumer name")
        self._require_nonempty(outcome_code, "outcome code")
        recorded = self.repository.record_inbox_receipt(
            consumer_name=consumer_name,
            event_id=event_id,
            outcome_code=outcome_code,
        )
        self.session.flush()
        return recorded

    def invalidate_for_owner_deletion(
        self, *, owner_user_id: UUID, job_id: UUID, owner_deletion_epoch: int
    ) -> Job:
        """Fence a Job after an externally committed owner deletion-epoch increment.

        The deletion workflow itself is introduced later; this primitive makes the Job side
        explicit so that a stale worker cannot write after that workflow advances the epoch.
        """
        job = self._require_job_for_update(owner_user_id=owner_user_id, job_id=job_id)
        if owner_deletion_epoch <= job.owner_deletion_epoch:
            raise JobTransitionError("deletion epoch must advance monotonically")
        job.execution_fence += 1
        job.owner_deletion_epoch = owner_deletion_epoch
        if job.active_lease_id is None:
            job.status = "CANCELLED"
            job.completed_at = datetime.now(UTC)
            job.dispatch_status = "INVALIDATED"
        else:
            job.status = "CANCEL_REQUESTED"
            job.dispatch_status = "OUTBOX_PENDING"
            self._create_private_command_and_outbox(
                job=job,
                command_type="INVALIDATE_JOB",
                command_sequence=self._next_command_sequence(job_id=job.id),
            )
        job.updated_at = datetime.now(UTC)
        self.session.flush()
        return job

    def _replayed_acceptance(self, record: IdempotencyRecord) -> JobAcceptance:
        job = self._job_from_idempotency_response(record)
        if job is None:
            raise JobIdempotencyReplayIncompleteError("replayed idempotency record has no Job")
        command = self.repository.get_latest_command(job_id=job.id)
        outbox_message = (
            self.repository.get_latest_outbox_message(command_id=command.id)
            if command is not None
            else None
        )
        return JobAcceptance(
            job=job,
            command=command,
            outbox_message=outbox_message,
            replayed=True,
        )

    def _require_job_for_update(self, *, owner_user_id: UUID, job_id: UUID) -> Job:
        job = self.repository.get_job_for_update(job_id=job_id, owner_user_id=owner_user_id)
        if job is None:
            raise JobNotFoundError("Job does not exist for this owner")
        return job

    def _job_from_idempotency_response(self, record: IdempotencyRecord) -> Job | None:
        if record.response_ref is not None and record.response_ref.startswith("job:"):
            try:
                job_id = UUID(record.response_ref.removeprefix("job:"))
            except ValueError:
                return None
            return self.repository.get_job(job_id=job_id, owner_user_id=record.owner_user_id)
        return self.repository.get_job_by_idempotency_record(idempotency_record_id=record.id)

    def _create_private_command_and_outbox(
        self,
        *,
        job: Job,
        command_type: str,
        command_sequence: int,
        checkpoint_id: UUID | None = None,
    ) -> tuple[JobCommand, OutboxMessage]:
        command_payload: dict[str, object] = {"command_type": command_type}
        outbox_payload: dict[str, object] = {
            "command_id": None,
            "job_id": str(job.id),
            "execution_fence": job.execution_fence,
            "owner_deletion_epoch": job.owner_deletion_epoch,
        }
        if checkpoint_id is not None:
            command_payload["checkpoint_id"] = str(checkpoint_id)
            outbox_payload["checkpoint_id"] = str(checkpoint_id)
        command = JobCommand(
            job_id=job.id,
            owner_user_id=job.owner_user_id,
            command_type=command_type,
            command_schema_version=self._PRIVATE_COMMAND_SCHEMA_VERSION,
            command_sequence=command_sequence,
            execution_fence=job.execution_fence,
            owner_deletion_epoch=job.owner_deletion_epoch,
            analysis_input_version=job.analysis_input_version,
            payload=command_payload,
        )
        self.repository.add_command(command)
        self.session.flush()
        outbox_message = OutboxMessage(
            message_type=self._PRIVATE_DISPATCH_MESSAGE_TYPE,
            schema_version=self._PRIVATE_COMMAND_SCHEMA_VERSION,
            visibility_scope="PRIVATE",
            aggregate_type="JOB",
            aggregate_id=job.id,
            aggregate_revision=command_sequence,
            command_id=command.id,
            job_id=job.id,
            owner_user_id=job.owner_user_id,
            execution_fence=job.execution_fence,
            owner_deletion_epoch=job.owner_deletion_epoch,
            payload={**outbox_payload, "command_id": str(command.id)},
        )
        self.repository.add_outbox_message(outbox_message)
        return command, outbox_message

    def _next_command_sequence(self, *, job_id: UUID) -> int:
        command = self.repository.get_latest_command(job_id=job_id)
        return 1 if command is None else command.command_sequence + 1

    def _invalidate_current_command(self, job: Job) -> None:
        command = self.repository.get_latest_command(job_id=job.id)
        if command is None or command.status in {"CONSUMED", "INVALIDATED", "FAILED"}:
            return
        command.status = "INVALIDATED"
        outbox_message = self.repository.get_latest_outbox_message(command_id=command.id)
        if outbox_message is not None and outbox_message.status == "PENDING":
            outbox_message.status = "FAILED"

    @staticmethod
    def _is_current_execution(
        *,
        owner: object,
        job: Job,
        lease_id: UUID,
        execution_fence: int,
        owner_deletion_epoch: int,
    ) -> bool:
        owner_epoch = getattr(owner, "deletion_epoch", None)
        return (
            owner is not None
            and job.status == "RUNNING"
            and job.active_lease_id == lease_id
            and job.execution_fence == execution_fence
            and job.owner_deletion_epoch == owner_deletion_epoch
            and owner_epoch == owner_deletion_epoch
        )

    @staticmethod
    def _to_model_input_ref(job: Job, reference: JobInputReference) -> JobInputRef:
        JobService._validate_input_reference(reference)
        return JobInputRef(
            job_id=job.id,
            owner_user_id=job.owner_user_id,
            project_version_id=reference.project_version_id,
            question_version_id=reference.question_version_id,
            episode_version_id=reference.episode_version_id,
            snapshot_id=reference.snapshot_id,
            policy_name=reference.policy_name,
            policy_version=reference.policy_version,
        )

    @staticmethod
    def _validate_input_reference(reference: JobInputReference) -> None:
        version_count = sum(
            value is not None
            for value in (
                reference.project_version_id,
                reference.question_version_id,
                reference.episode_version_id,
                reference.snapshot_id,
            )
        )
        has_complete_policy = (
            reference.policy_name is not None and reference.policy_version is not None
        )
        if version_count + int(has_complete_policy) != 1:
            raise JobError("a Job input reference must identify exactly one typed input")
        if (reference.policy_name is None) != (reference.policy_version is None):
            raise JobError("policy name and version must be supplied together")

    @staticmethod
    def _require_nonempty(value: str, label: str) -> None:
        if not value.strip():
            raise JobError(f"{label} must be present")

    def _release_lease(self, *, job: Job, lease: JobExecutionLease, reason: str) -> None:
        slot = self.repository.get_slot_for_update(
            owner_user_id=lease.owner_user_id, slot_no=lease.slot_no
        )
        if slot is None or slot.lease_id != lease.id or slot.job_id != job.id:
            raise JobTransitionError("active lease does not own its execution slot")
        now = datetime.now(UTC)
        lease.released_at = now
        lease.release_reason = reason
        slot.job_id = None
        slot.lease_id = None
        slot.claimed_at = None
        slot.updated_at = now
        job.active_lease_id = None
