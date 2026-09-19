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
from app.repo.core_decisions import CoreDecisionRepository
from app.repo.identity import IdentityRepository
from app.repo.jobs import InboxReceiptReservation, JobRepository
from app.runtime.core_decision_binding import project_core_decision_pin
from app.runtime.question_core_binding import (
    QUESTION_MATCHING_SCOPE,
    W4_QUESTION_CORE_PRODUCER,
    QuestionCoreBindingError,
    resolve_question_collection_company,
)
from app.services.idempotency import IdempotencyService
from app.services.w2_commit_gate import W2CommitGateService


class JobError(Exception):
    pass


class JobNotFoundError(JobError):
    pass


class JobTransitionError(JobError):
    pass


class JobActionStaleError(JobError):
    """A stored required-action fence no longer matches the user's request."""


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
        # This must occur before the JobCommand can be invalidated below.  The
        # commit-gate service takes User -> Job -> W2 command -> operation locks
        # and writes ABORT into this same transaction.
        W2CommitGateService(self.session).abort_open_operations_for_cancellation(
            owner_user_id=owner_user_id,
            job_id=job_id,
        )
        job = self._require_job_for_update(owner_user_id=owner_user_id, job_id=job_id)
        if job.status in {"SUCCEEDED", "FAILED_FINAL", "CANCELLED", "CANCEL_REQUESTED"}:
            self.session.flush()
            return job
        self._request_cancellation_for_job(job)
        self.session.flush()
        return job

    def apply_required_action(
        self,
        *,
        owner_user_id: UUID,
        job_id: UUID,
        required_action_id: UUID,
        action_code: str,
        expected_input_version: str | None,
        expected_result_version: str | None,
        acknowledge_rate_limit: bool,
        checkpoint_id: UUID | None = None,
    ) -> JobAcceptance:
        """Accept one fenced user action without claiming or dispatching work.

        Idempotency is deliberately owned by the HTTP-facing action service.  This domain
        operation keeps the action fence, Job transition, private Command, and Outbox row in
        the caller's existing transaction.
        """

        if action_code == "STOP":
            # See ``request_cancellation``.  Do this before this method takes
            # the Job row lock, preserving the common commit-gate lock order.
            W2CommitGateService(self.session).abort_open_operations_for_cancellation(
                owner_user_id=owner_user_id,
                job_id=job_id,
            )
        owner = self.repository.get_owner_for_update(owner_user_id=owner_user_id)
        if owner is None:
            raise JobNotFoundError("job owner does not exist")
        job = self._require_job_for_update(owner_user_id=owner_user_id, job_id=job_id)
        action = self.repository.get_required_action_for_update(
            action_id=required_action_id,
            job_id=job_id,
            owner_user_id=owner_user_id,
        )
        self._validate_required_action(
            job=job,
            action=action,
            action_code=action_code,
            expected_input_version=expected_input_version,
            expected_result_version=expected_result_version,
        )

        command: JobCommand | None = None
        outbox_message: OutboxMessage | None = None
        if action_code == "RETRY":
            if job.status not in {"FAILED_RETRYABLE", "PAUSED_RATE_LIMIT", "WAITING_USER"}:
                raise JobTransitionError("only a retryable or rate-limited Job can be retried")
            if job.status == "PAUSED_RATE_LIMIT" and not acknowledge_rate_limit:
                raise JobTransitionError("rate-limited Jobs require explicit acknowledgement")
            if job.active_lease_id is not None or job.owner_deletion_epoch != owner.deletion_epoch:
                raise JobTransitionError("Job is not safe to retry for the current owner epoch")
            core_binding = None
            core_decision = None
            if job.status == "WAITING_USER":
                if action is None or action.context_code not in {
                    "CORE_DECISION_AVAILABLE",
                    "W4_QUESTION_CORE_AVAILABLE",
                }:
                    raise JobTransitionError(
                        "a user-waiting Job requires a current Core decision retry action"
                    )
                if job.analysis_input_version is None:
                    raise JobTransitionError("the waiting Job has no analysis input version")
                binding_history = CoreDecisionRepository(
                    self.session
                ).list_core_bindings_for_job(
                    job_id=job.id,
                    analysis_input_version=job.analysis_input_version,
                )
                current_by_source = {}
                for candidate in binding_history:
                    if action.context_code == "W4_QUESTION_CORE_AVAILABLE":
                        if (
                            candidate.origin_producer != W4_QUESTION_CORE_PRODUCER
                            or candidate.decision_scope != QUESTION_MATCHING_SCOPE
                            or candidate.question_version_id is None
                        ):
                            continue
                        key = (candidate.source_id, candidate.question_version_id)
                    else:
                        if candidate.decision_scope != "COMPANY_KNOWLEDGE":
                            continue
                        key = (candidate.source_id, None)
                    current_by_source.setdefault(key, candidate)
                if len(current_by_source) != 1:
                    raise JobTransitionError("the waiting Job has no unambiguous Core binding")
                core_binding = next(iter(current_by_source.values()))
                if core_binding.owner_deletion_epoch != owner.deletion_epoch:
                    raise JobTransitionError("the Core binding owner epoch is stale")
                core_decision = CoreDecisionRepository(self.session).get_decision(
                    decision_id=core_binding.analysis_source_decision_id
                )
                if core_decision is None:
                    raise JobTransitionError("the Core binding decision does not exist")
                if action.context_code == "W4_QUESTION_CORE_AVAILABLE":
                    try:
                        resolve_question_collection_company(
                            session=self.session,
                            owner=owner,
                            job=job,
                            decision=core_decision,
                            binding=core_binding,
                            pin=project_core_decision_pin(
                                binding=core_binding,
                                decision=core_decision,
                            ),
                            expected_command_id=None,
                            for_update=True,
                        )
                    except QuestionCoreBindingError as error:
                        raise JobTransitionError(
                            "the Question Core binding is no longer current"
                        ) from error
            command, outbox_message = self._resume_job(
                job=job,
                checkpoint_id=checkpoint_id,
            )
            if core_binding is not None and core_decision is not None:
                pin = project_core_decision_pin(
                    binding=core_binding,
                    decision=core_decision,
                )
                command.analysis_source_decision_id = core_decision.id
                command.payload = {**command.payload, "core_decision_pin": pin}
                outbox_message.payload = {
                    **outbox_message.payload,
                    "core_decision_pin": pin,
                }
        elif action_code == "CONTINUE_LIMITED":
            if job.status != "WAITING_USER":
                raise JobTransitionError("only a user-waiting Job can continue with limitations")
            if job.active_lease_id is not None or job.owner_deletion_epoch != owner.deletion_epoch:
                raise JobTransitionError("Job is not safe to continue for the current owner epoch")
            command, outbox_message = self._resume_job(job=job, checkpoint_id=checkpoint_id)
        elif action_code == "STOP":
            if job.status in {"SUCCEEDED", "FAILED_FINAL", "CANCELLED"}:
                raise JobTransitionError("terminal Jobs cannot be stopped")
            command, outbox_message = self._request_cancellation_for_job(job)
        else:
            raise JobTransitionError("unsupported user action")

        action.action_status = "RESOLVED"
        action.resolved_at = datetime.now(UTC)
        self.session.flush()
        return JobAcceptance(
            job=job,
            command=command,
            outbox_message=outbox_message,
            replayed=False,
        )

    def _request_cancellation_for_job(
        self, job: Job
    ) -> tuple[JobCommand | None, OutboxMessage | None]:
        command: JobCommand | None = None
        outbox_message: OutboxMessage | None = None
        if job.status == "RUNNING":
            self._invalidate_current_command(job)
            job.status = "CANCEL_REQUESTED"
            job.execution_fence += 1
            job.dispatch_status = "OUTBOX_PENDING"
            command, outbox_message = self._create_private_command_and_outbox(
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
        self.repository.dismiss_open_required_actions(
            job_id=job.id,
            owner_user_id=job.owner_user_id,
        )
        job.updated_at = datetime.now(UTC)
        return command, outbox_message

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
        context_code: str | None = None,
        expected_input_version: str | None = None,
        expected_result_version: str | None = None,
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
                context_code=context_code,
                expected_input_version=expected_input_version or job.analysis_input_version,
                expected_result_version=expected_result_version,
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
        expected_input_version: str | None = None,
        expected_result_version: str | None = None,
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
                    expected_input_version=expected_input_version or job.analysis_input_version,
                    expected_result_version=expected_result_version,
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

    def reserve_digest_aware_inbox_receipt(
        self,
        *,
        consumer_name: str,
        event_id: UUID,
        outcome_code: str,
        payload_digest: str,
        producer_name: str,
        schema_version: str,
    ) -> InboxReceiptReservation:
        """Reserve a delivery without confusing an ID conflict with replay."""

        self._require_nonempty(consumer_name, "consumer name")
        self._require_nonempty(outcome_code, "outcome code")
        self._require_nonempty(payload_digest, "payload digest")
        self._require_nonempty(producer_name, "producer name")
        self._require_nonempty(schema_version, "schema version")
        reservation = self.repository.reserve_digest_aware_inbox_receipt(
            consumer_name=consumer_name,
            event_id=event_id,
            outcome_code=outcome_code,
            payload_digest=payload_digest,
            producer_name=producer_name,
            schema_version=schema_version,
        )
        self.session.flush()
        return reservation

    def update_inbox_receipt_outcome(
        self, *, consumer_name: str, event_id: UUID, outcome_code: str
    ) -> None:
        """Finish an inbox receipt reserved before a state-changing consumer action."""

        self._require_nonempty(consumer_name, "consumer name")
        self._require_nonempty(outcome_code, "outcome code")
        self.repository.update_inbox_receipt_outcome(
            consumer_name=consumer_name,
            event_id=event_id,
            outcome_code=outcome_code,
        )
        self.session.flush()

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
        self.repository.dismiss_open_required_actions(
            job_id=job.id,
            owner_user_id=job.owner_user_id,
        )
        self.session.flush()
        return job

    def _resume_job(
        self, *, job: Job, checkpoint_id: UUID | None
    ) -> tuple[JobCommand, OutboxMessage]:
        """Create a new fenced execution command for an already validated user action."""

        job.execution_fence += 1
        job.status = "QUEUED"
        job.dispatch_status = "OUTBOX_PENDING"
        job.retryable = False
        job.retry_after = None
        job.failure_code = None
        job.safe_failure_message = None
        job.completed_at = None
        job.updated_at = datetime.now(UTC)
        return self._create_private_command_and_outbox(
            job=job,
            command_type="EXECUTE_JOB",
            command_sequence=self._next_command_sequence(job_id=job.id),
            checkpoint_id=checkpoint_id,
        )

    @staticmethod
    def _validate_required_action(
        *,
        job: Job,
        action: JobRequiredAction | None,
        action_code: str,
        expected_input_version: str | None,
        expected_result_version: str | None,
    ) -> None:
        if (
            action is None
            or action.action_status != "OPEN"
            or action.resolved_at is not None
            or action.action_code != action_code
        ):
            raise JobActionStaleError("the requested action is no longer open")
        if (
            action.expected_input_version != expected_input_version
            or action.expected_result_version != expected_result_version
            or job.analysis_input_version != expected_input_version
        ):
            raise JobActionStaleError("the requested action fence is stale")

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
        # A W2 ABORT/PURGE control message is deliberately created before the
        # source command is invalidated.  It must remain dispatchable; only the
        # source-command's own dispatch record is invalidated here.
        outbox_message = self.repository.get_latest_dispatch_outbox_message(command_id=command.id)
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
