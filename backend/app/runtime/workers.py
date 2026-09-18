from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.identity import User
from app.models.jobs import (
    Job,
    JobCommand,
    JobExecutionLease,
    JobRequiredAction,
    OutboxMessage,
    OwnerExecutionSlot,
)
from app.models.lifecycle_operations import JobCheckpoint
from app.models.sources import AnalysisSourceDecision, JobSourceLink
from app.runtime.core_decision_binding import (
    CoreDecisionBindingError,
    validate_database_core_binding,
)
from app.runtime.sqs import ReceivedSqsMessage, SqsPort, SqsRetryableError
from app.services.direct_source_registration import (
    DIRECT_SOURCE_REGISTRATION_DECISION_CODE,
    DIRECT_SOURCE_REGISTRATION_JOB_TYPE,
    DIRECT_SOURCE_REGISTRATION_OWNER,
    DIRECT_SOURCE_REGISTRATION_PURPOSE,
    DIRECT_SOURCE_REGISTRATION_SCOPE,
)
from app.services.jobs import JobService
from app.services.w2_commit_gate import (
    W2CommitGateError,
    W2CommitGateService,
)

_EXECUTION_MESSAGE_TYPE = "job.command.dispatch"
_W2_COMMAND_MESSAGE_TYPE = "w1.private.w2.collection-command.v1"
_W2_DIRECT_SOURCE_REGISTRATION_MESSAGE_TYPE = "w1.private.w2.direct-source-registration.v1"
_W2_RESULT_CONSUMER = "w1.collection-result"
_W2_COMMIT_READY_CONSUMER = "w1.collection-commit-ready"
_W2_RESULT_MESSAGE_TYPE = "w2.collection.result.v1"
_W2_RESULT_CHANNEL = "w1.private.w2.collection-result.v1"
_MAX_SQS_BATCH_SIZE = 10


class RuntimeContractError(ValueError):
    """A queue payload was syntactically valid JSON but violates the private contract."""


@dataclass(frozen=True)
class QueueWorkerRunResult:
    received: int
    acknowledged: int
    retry_scheduled: int
    stale_discarded: int
    rejected_schema: int
    duplicate: int
    lease_recovered: int


@dataclass(frozen=True)
class _DeliveryOutcome:
    acknowledge: bool
    stale: bool = False
    rejected_schema: bool = False
    duplicate: bool = False
    lease_id: UUID | None = None


@dataclass(frozen=True)
class CommitReadyCollectionResult:
    """W1-normalized staged-result identity for the future W2 adapter.

    This is deliberately not a W2 wire DTO.  W2 owns the canonical ACK and
    staged-result schemas; its adapter can call this boundary only after those
    artifacts are supplied and validated.  The fields here are exactly the
    immutable values W1 must re-check under the durable commit-gate locks.
    """

    message_id: UUID
    owner_user_id: UUID
    job_id: UUID
    command_id: UUID
    execution_lease_id: UUID
    execution_fence: int
    owner_deletion_epoch: int
    result_digest: str


@dataclass(frozen=True)
class CommitReadyApplicationResult:
    """Outcome of W1's pre-visibility COMMIT_READY boundary."""

    outcome_code: str
    operation_id: UUID | None = None
    operation_revision: int | None = None
    prepare_outbox_id: UUID | None = None


def _contract_validator(relative_path: str) -> Draft202012Validator:
    return _contract_validator_cached(relative_path)


@cache
def _contract_validator_cached(relative_path: str) -> Draft202012Validator:
    contract_root = Path(__file__).resolve().parents[2] / "contracts"
    with (contract_root / relative_path).open(encoding="utf-8") as stream:
        schema = json.load(stream)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _load_json_object(body: str) -> dict[str, object]:
    try:
        value = json.loads(body)
    except json.JSONDecodeError as error:
        raise RuntimeContractError("INVALID_JSON") from error
    if not isinstance(value, dict):
        raise RuntimeContractError("MESSAGE_NOT_OBJECT")
    return value


def _validate(value: dict[str, object], relative_path: str, code: str) -> None:
    if list(_contract_validator(relative_path).iter_errors(value)):
        raise RuntimeContractError(code)


def validate_w2_commit_gate_wire_schema(
    value: dict[str, object], *, message_type: str
) -> None:
    """Validate one pinned W2 proposal without widening the legacy result route.

    The legacy ``w2.collection.result.v1`` union remains intentionally separate:
    no commit-gate proposal is ever accepted by :class:`CollectionResultWorker`.
    The dedicated inbound adapter selects one of these exact W2-owned schemas
    before it asks the gate service to make a durable state change.
    """

    schema_by_message_type = {
        "w2.private.staged-result.proposal.v1": (
            "w2/v1/source-collection.staged-result.schema.json",
            "W2_COMMIT_GATE_STAGED_SCHEMA_INVALID",
        ),
        "w2.private.commit-gate-ack.proposal.v1": (
            "w2/v1/source-collection.commit-gate-ack.schema.json",
            "W2_COMMIT_GATE_ACK_SCHEMA_INVALID",
        ),
    }
    try:
        relative_path, code = schema_by_message_type[message_type]
    except KeyError as error:
        raise RuntimeContractError("W2_COMMIT_GATE_MESSAGE_TYPE_UNSUPPORTED") from error
    _validate(value, relative_path, code)


def _bounded_message(value: object, *, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value[:1024]
    return fallback


class JobWorker:
    """Consume W1 execution references and create the W2 dispatch outbox transactionally.

    The worker deliberately does not contact W2.  It commits the W2 dispatch Outbox record with
    the current lease, then the existing OutboxRelay performs broker delivery.  This keeps an
    execution-queue redelivery from creating more than one W2 command.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        sqs: SqsPort,
        execution_queue_url: str,
        worker_id: str,
        visibility_timeout_seconds: int = 120,
        long_poll_seconds: int = 20,
        lease_heartbeat_seconds: int = 60,
    ) -> None:
        if not execution_queue_url.strip():
            raise ValueError("execution_queue_url must be present")
        if not worker_id.strip() or len(worker_id) > 128:
            raise ValueError("worker_id must be between 1 and 128 characters")
        if not 1 <= visibility_timeout_seconds <= 43_200:
            raise ValueError("visibility_timeout_seconds must be between 1 and 43200")
        if not 0 <= long_poll_seconds <= 20:
            raise ValueError("long_poll_seconds must be between 0 and 20")
        if lease_heartbeat_seconds <= 0 or lease_heartbeat_seconds >= visibility_timeout_seconds:
            raise ValueError("lease heartbeat must be shorter than the visibility timeout")
        self._session_factory = session_factory
        self._sqs = sqs
        self._execution_queue_url = execution_queue_url
        self._worker_id = worker_id
        self._visibility_timeout_seconds = visibility_timeout_seconds
        self._long_poll_seconds = long_poll_seconds
        self._lease_heartbeat_seconds = lease_heartbeat_seconds

    def drain_once(self, *, max_messages: int = _MAX_SQS_BATCH_SIZE) -> QueueWorkerRunResult:
        if not 1 <= max_messages <= _MAX_SQS_BATCH_SIZE:
            raise ValueError("max_messages must be between 1 and 10")
        lease_recovered = self.recover_expired_leases()
        try:
            deliveries = self._sqs.receive_messages(
                queue_url=self._execution_queue_url,
                max_messages=max_messages,
                visibility_timeout_seconds=self._visibility_timeout_seconds,
                wait_time_seconds=self._long_poll_seconds,
            )
        except SqsRetryableError:
            return QueueWorkerRunResult(0, 0, 0, 0, 0, 0, lease_recovered)

        acknowledged = retry_scheduled = stale_discarded = rejected_schema = duplicate = 0
        for delivery in deliveries:
            outcome = self._handle_delivery(delivery)
            if outcome.lease_id is not None and self.heartbeat(lease_id=outcome.lease_id):
                try:
                    self._sqs.change_message_visibility(
                        queue_url=self._execution_queue_url,
                        receipt_handle=delivery.receipt_handle,
                        visibility_timeout_seconds=self._visibility_timeout_seconds,
                    )
                except SqsRetryableError:
                    # The transaction is already durable; an execution redelivery is harmless.
                    outcome = _DeliveryOutcome(acknowledge=False, lease_id=outcome.lease_id)
            if outcome.acknowledge:
                try:
                    self._sqs.delete_message(
                        queue_url=self._execution_queue_url,
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
            lease_recovered=lease_recovered,
        )

    def _handle_delivery(self, delivery: ReceivedSqsMessage) -> _DeliveryOutcome:
        try:
            message = _load_json_object(delivery.body)
            _validate(message, "w1/v1/private-job-dispatch.schema.json", "EXECUTION_SCHEMA_INVALID")
            command_id = UUID(str(message["command_id"]))
            job_id = UUID(str(message["job_id"]))
            if message["message_id"] != str(command_id):
                raise RuntimeContractError("EXECUTION_MESSAGE_COMMAND_MISMATCH")
            execution_fence = int(message["execution_fence"])
            owner_deletion_epoch = int(message["owner_deletion_epoch"])
        except (KeyError, TypeError, ValueError, RuntimeContractError):
            return _DeliveryOutcome(acknowledge=True, rejected_schema=True)

        with self._session_factory.begin() as session:
            command = session.scalar(
                select(JobCommand).where(JobCommand.id == command_id).with_for_update()
            )
            job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if command is None or job is None:
                return _DeliveryOutcome(acknowledge=True, stale=True)
            owner = session.scalar(
                select(User).where(User.id == job.owner_user_id).with_for_update()
            )
            if not self._matches_execution_message(
                command=command,
                job=job,
                owner=owner,
                execution_fence=execution_fence,
                owner_deletion_epoch=owner_deletion_epoch,
            ):
                return _DeliveryOutcome(acknowledge=True, stale=True)
            if command.command_type in {"CANCEL_JOB", "INVALIDATE_JOB"}:
                # Cancellation is observed by W2 through lookup.  This queue reference must not
                # release the active slot before W2's cleanup/result acknowledgement arrives.
                return _DeliveryOutcome(acknowledge=True, stale=True)
            if command.command_type != "EXECUTE_JOB":
                return _DeliveryOutcome(acknowledge=True, stale=True)
            if command.status == "CLAIMED" and job.status == "RUNNING":
                # The initial claim and W2 outbox creation are one transaction, so this is a
                # Standard-SQS duplicate rather than another claim.
                return _DeliveryOutcome(acknowledge=True, duplicate=True)
            if command.status != "ENQUEUED" or job.status != "QUEUED":
                return _DeliveryOutcome(acknowledge=True, stale=True)

            lease = JobService(session).claim_execution(
                owner_user_id=job.owner_user_id,
                job_id=job.id,
                worker_ref=self._worker_id,
            )
            if lease is None:
                # All three owner slots are occupied.  Keep the Job queued and let SQS redeliver.
                return _DeliveryOutcome(acknowledge=False)

            if not self._create_w2_dispatch_or_block(
                session=session,
                job=job,
                execution_command=command,
                lease=lease,
            ):
                return _DeliveryOutcome(acknowledge=True, stale=True)
            return _DeliveryOutcome(acknowledge=True, lease_id=lease.id)

    @staticmethod
    def _matches_execution_message(
        *,
        command: JobCommand,
        job: Job,
        owner: User | None,
        execution_fence: int,
        owner_deletion_epoch: int,
    ) -> bool:
        return (
            command.job_id == job.id
            and command.owner_user_id == job.owner_user_id
            and command.execution_fence == execution_fence
            and command.owner_deletion_epoch == owner_deletion_epoch
            and job.execution_fence == execution_fence
            and job.owner_deletion_epoch == owner_deletion_epoch
            and owner is not None
            and owner.deletion_epoch == owner_deletion_epoch
            and owner.account_status not in {"DELETION_PENDING", "DELETED"}
        )

    def _create_w2_dispatch_or_block(
        self,
        *,
        session: Session,
        job: Job,
        execution_command: JobCommand,
        lease: JobExecutionLease,
    ) -> bool:
        execution_payload = execution_command.payload
        if (
            isinstance(execution_payload, dict)
            and execution_payload.get("dispatch_kind") == DIRECT_SOURCE_REGISTRATION_SCOPE
        ):
            # A W1 non-core registration is never an escape hatch for a normal analysis Job.
            # The dedicated service is the only producer and uses SOURCE_REGISTRATION.
            if job.job_type != DIRECT_SOURCE_REGISTRATION_JOB_TYPE:
                self._block_for_core_decision(session=session, job=job, lease=lease)
                return False
            return self._create_direct_source_registration_dispatch_or_block(
                session=session,
                job=job,
                execution_command=execution_command,
                lease=lease,
            )
        pin = (
            execution_payload.get("core_decision_pin")
            if isinstance(execution_payload, dict)
            else None
        )
        if not isinstance(pin, dict):
            self._block_for_core_decision(session=session, job=job, lease=lease)
            return False
        try:
            source_id = UUID(str(pin["source_id"]))
            company_id = UUID(str(pin["company_id"]))
            origin_message_id = UUID(str(pin["origin_message_id"]))
            decision_id = UUID(str(pin["decision_id"]))
            if pin.get("decision_scope") != "COMPANY_KNOWLEDGE":
                raise RuntimeContractError("CORE_DECISION_SCOPE_UNSUPPORTED")
            if pin.get("question_version_id") is not None:
                raise RuntimeContractError("CORE_DECISION_SCOPE_INVALID")
            if pin.get("decision_code") != "CORE_REQUIRED" or pin.get("is_core") is not True:
                raise RuntimeContractError("CORE_DECISION_NOT_REQUIRED")
            decision_version = int(pin["decision_version"])
            if decision_version < 1:
                raise RuntimeContractError("CORE_DECISION_VERSION_INVALID")
            analysis_input_version = pin.get("analysis_input_version")
            reason_code = pin.get("reason_code")
            if not isinstance(analysis_input_version, str) or not analysis_input_version:
                raise RuntimeContractError("CORE_DECISION_INPUT_INVALID")
            if not isinstance(reason_code, str) or not reason_code:
                raise RuntimeContractError("CORE_DECISION_REASON_INVALID")
        except (KeyError, TypeError, ValueError, RuntimeContractError):
            self._block_for_core_decision(session=session, job=job, lease=lease)
            return False

        if analysis_input_version != job.analysis_input_version:
            self._block_for_core_decision(session=session, job=job, lease=lease)
            return False
        decision = session.scalar(
            select(AnalysisSourceDecision)
            .where(AnalysisSourceDecision.id == decision_id)
            .with_for_update()
        )
        if (
            decision is None
            or decision.decision_scope != "COMPANY_KNOWLEDGE"
            or decision.company_id != company_id
            or decision.question_version_id is not None
            or decision.source_id != source_id
            or decision.analysis_input_version != analysis_input_version
            or decision.decision_version != decision_version
            or decision.decision_code != "CORE_REQUIRED"
            or decision.reason_code != reason_code
        ):
            self._block_for_core_decision(session=session, job=job, lease=lease)
            return False
        source_link = session.scalar(
            select(JobSourceLink)
            .where(
                JobSourceLink.job_id == job.id,
                JobSourceLink.owner_user_id == job.owner_user_id,
                JobSourceLink.source_id == source_id,
            )
            .order_by(JobSourceLink.created_at, JobSourceLink.id)
            .limit(1)
            .with_for_update()
        )
        if source_link is None or source_link.command_id is not None:
            self._block_for_core_decision(session=session, job=job, lease=lease)
            return False

        # D-04 fixes all W2 integer decision versions to the immutable decision revision.  W1's
        # command sequence remains an internal ordering value only.
        input_version = decision_version
        w2_command_id = uuid4()
        w2_payload: dict[str, object] = {
            "schema_version": "w2.collection.v1",
            "command_id": str(w2_command_id),
            "job_id": str(job.id),
            "authenticated_owner_ref": str(job.owner_user_id),
            "project_ref": str(job.project_id) if job.project_id is not None else None,
            "company_id": str(company_id),
            "source_id": str(source_id),
            "input_version": input_version,
            "execution_fence": str(job.execution_fence),
            "purpose_ref": str(source_link.id),
            "core_source_decision": {
                "is_core": True,
                "decided_by": decision.decision_owner,
                "rationale": reason_code,
                "decision_revision": decision_version,
                "analysis_input_version": input_version,
            },
            "resume_stage": self._resume_stage(session=session, job=job, command=execution_command),
            "policy_revision": None,
            "owner_deletion_epoch": job.owner_deletion_epoch,
        }
        try:
            validate_database_core_binding(
                decision=decision,
                pin=pin,
                w2_command=w2_payload,
                job_analysis_input_version=job.analysis_input_version,
                source_link=source_link,
            )
        except CoreDecisionBindingError:
            self._block_for_core_decision(session=session, job=job, lease=lease)
            return False
        _validate(
            w2_payload,
            "w2/v1/source-collection.command.schema.json",
            "W2_COMMAND_SCHEMA_INVALID",
        )
        child_command = JobCommand(
            id=w2_command_id,
            job_id=job.id,
            owner_user_id=job.owner_user_id,
            command_type="W2_SOURCE_COLLECTION",
            command_schema_version="1.0",
            command_sequence=execution_command.command_sequence + 1,
            execution_fence=job.execution_fence,
            owner_deletion_epoch=job.owner_deletion_epoch,
            analysis_input_version=job.analysis_input_version,
            analysis_source_decision_id=decision.id,
            payload={
                "command_type": "W2_SOURCE_COLLECTION",
                "w2_command": w2_payload,
                "core_decision_pin": {
                    **pin,
                    "origin_message_id": str(origin_message_id),
                    "decision_id": str(decision_id),
                    "company_id": str(company_id),
                    "source_id": str(source_id),
                },
                "runtime": {"lease_id": str(lease.id), "input_version": input_version},
            },
        )
        session.add(child_command)
        session.flush()
        source_link.command_id = child_command.id
        session.add(
            OutboxMessage(
                message_type=_W2_COMMAND_MESSAGE_TYPE,
                schema_version="1.0",
                visibility_scope="PRIVATE",
                aggregate_type="JOB",
                aggregate_id=job.id,
                aggregate_revision=child_command.command_sequence,
                command_id=child_command.id,
                job_id=job.id,
                owner_user_id=job.owner_user_id,
                execution_fence=job.execution_fence,
                owner_deletion_epoch=job.owner_deletion_epoch,
                payload={
                    "command_id": str(child_command.id),
                    "job_id": str(job.id),
                    "execution_fence": job.execution_fence,
                    "owner_deletion_epoch": job.owner_deletion_epoch,
                },
            )
        )
        session.flush()
        return True

    def _create_direct_source_registration_dispatch_or_block(
        self,
        *,
        session: Session,
        job: Job,
        execution_command: JobCommand,
        lease: JobExecutionLease,
    ) -> bool:
        """Create W2 work only from the W1-owned direct-registration ledger pin."""

        execution_payload = execution_command.payload
        pin = (
            execution_payload.get("direct_source_registration_pin")
            if isinstance(execution_payload, dict)
            else None
        )
        if not isinstance(pin, dict):
            self._block_for_core_decision(session=session, job=job, lease=lease)
            return False
        try:
            decision_id = UUID(str(pin["registration_decision_id"]))
            company_id = UUID(str(pin["company_id"]))
            source_id = UUID(str(pin["source_id"]))
            registration_input_version = pin["registration_input_version"]
            decision_version = int(pin["decision_version"])
            reason_code = pin["reason_code"]
            if (
                pin.get("decision_scope") != DIRECT_SOURCE_REGISTRATION_SCOPE
                or pin.get("question_version_id") is not None
                or pin.get("is_core") is not False
                or pin.get("decision_code") != DIRECT_SOURCE_REGISTRATION_DECISION_CODE
                or pin.get("decision_owner") != DIRECT_SOURCE_REGISTRATION_OWNER
                or pin.get("purpose") != DIRECT_SOURCE_REGISTRATION_PURPOSE
                or not isinstance(registration_input_version, str)
                or not registration_input_version
                or decision_version < 1
                or not isinstance(reason_code, str)
                or not reason_code
            ):
                raise RuntimeContractError("DIRECT_SOURCE_REGISTRATION_PIN_INVALID")
        except (KeyError, TypeError, ValueError, RuntimeContractError):
            self._block_for_core_decision(session=session, job=job, lease=lease)
            return False

        if registration_input_version != job.analysis_input_version:
            self._block_for_core_decision(session=session, job=job, lease=lease)
            return False
        decision = session.scalar(
            select(AnalysisSourceDecision)
            .where(AnalysisSourceDecision.id == decision_id)
            .with_for_update()
        )
        if (
            decision is None
            or execution_command.analysis_source_decision_id != decision.id
            or decision.decision_scope != DIRECT_SOURCE_REGISTRATION_SCOPE
            or decision.company_id != company_id
            or decision.question_version_id is not None
            or decision.source_id != source_id
            or decision.analysis_input_version != registration_input_version
            or decision.decision_version != decision_version
            or decision.decision_code != DIRECT_SOURCE_REGISTRATION_DECISION_CODE
            or decision.decision_owner != DIRECT_SOURCE_REGISTRATION_OWNER
            or decision.reason_code != reason_code
        ):
            self._block_for_core_decision(session=session, job=job, lease=lease)
            return False
        source_link = session.scalar(
            select(JobSourceLink)
            .where(
                JobSourceLink.job_id == job.id,
                JobSourceLink.owner_user_id == job.owner_user_id,
                JobSourceLink.source_id == source_id,
                JobSourceLink.purpose_ref == DIRECT_SOURCE_REGISTRATION_PURPOSE,
            )
            .order_by(JobSourceLink.created_at, JobSourceLink.id)
            .limit(1)
            .with_for_update()
        )
        if (
            source_link is None
            or source_link.command_id is not None
            or source_link.analysis_input_version != registration_input_version
        ):
            self._block_for_core_decision(session=session, job=job, lease=lease)
            return False

        # D-04 maps the W1 decision revision to W2's integer transport input version.  The
        # opaque registration_input_version is preserved only in the W1 decision and pin.
        input_version = decision_version
        w2_command_id = uuid4()
        w2_payload: dict[str, object] = {
            "schema_version": "w2.collection.v1",
            "command_id": str(w2_command_id),
            "job_id": str(job.id),
            "authenticated_owner_ref": str(job.owner_user_id),
            "project_ref": str(job.project_id) if job.project_id is not None else None,
            "company_id": str(company_id),
            "source_id": str(source_id),
            "input_version": input_version,
            "execution_fence": str(job.execution_fence),
            "purpose_ref": str(source_link.id),
            "core_source_decision": {
                "is_core": False,
                "decided_by": DIRECT_SOURCE_REGISTRATION_OWNER,
                "rationale": reason_code,
                "decision_revision": decision_version,
                "analysis_input_version": input_version,
            },
            "resume_stage": self._resume_stage(session=session, job=job, command=execution_command),
            "policy_revision": None,
            "owner_deletion_epoch": job.owner_deletion_epoch,
        }
        _validate(
            w2_payload,
            "w2/v1/source-collection.command.schema.json",
            "W2_DIRECT_SOURCE_REGISTRATION_COMMAND_SCHEMA_INVALID",
        )
        child_command = JobCommand(
            id=w2_command_id,
            job_id=job.id,
            owner_user_id=job.owner_user_id,
            command_type="W2_DIRECT_SOURCE_REGISTRATION",
            command_schema_version="1.0",
            command_sequence=execution_command.command_sequence + 1,
            execution_fence=job.execution_fence,
            owner_deletion_epoch=job.owner_deletion_epoch,
            analysis_input_version=job.analysis_input_version,
            analysis_source_decision_id=decision.id,
            payload={
                "command_type": "W2_DIRECT_SOURCE_REGISTRATION",
                "w2_command": w2_payload,
                "direct_source_registration_pin": pin,
                "runtime": {"lease_id": str(lease.id), "input_version": input_version},
            },
        )
        session.add(child_command)
        session.flush()
        source_link.command_id = child_command.id
        session.add(
            OutboxMessage(
                message_type=_W2_DIRECT_SOURCE_REGISTRATION_MESSAGE_TYPE,
                schema_version="1.0",
                visibility_scope="PRIVATE",
                aggregate_type="JOB",
                aggregate_id=job.id,
                aggregate_revision=child_command.command_sequence,
                command_id=child_command.id,
                job_id=job.id,
                owner_user_id=job.owner_user_id,
                execution_fence=job.execution_fence,
                owner_deletion_epoch=job.owner_deletion_epoch,
                payload={
                    "command_id": str(child_command.id),
                    "job_id": str(job.id),
                    "execution_fence": job.execution_fence,
                    "owner_deletion_epoch": job.owner_deletion_epoch,
                },
            )
        )
        session.flush()
        return True

    @staticmethod
    def _resume_stage(*, session: Session, job: Job, command: JobCommand) -> str:
        payload = command.payload
        checkpoint_id = payload.get("checkpoint_id") if isinstance(payload, dict) else None
        if isinstance(checkpoint_id, str):
            try:
                checkpoint = session.scalar(
                    select(JobCheckpoint).where(
                        JobCheckpoint.id == UUID(checkpoint_id),
                        JobCheckpoint.job_id == job.id,
                        JobCheckpoint.owner_user_id == job.owner_user_id,
                    )
                )
            except ValueError:
                checkpoint = None
            if checkpoint is not None and checkpoint.resume_stage in {
                "policy",
                "fetch",
                "parse",
                "persist",
                "deliver",
            }:
                return checkpoint.resume_stage
        return "policy"

    @staticmethod
    def _block_for_core_decision(
        *, session: Session,
        job: Job,
        lease: JobExecutionLease,
    ) -> None:
        # Never synthesize W3/W4 provenance.  Until a validated decision pin exists, release
        # the slot and make the lack of a dispatchable decision durable and visible.
        JobService(session).pause_execution(
            owner_user_id=job.owner_user_id,
            job_id=job.id,
            lease_id=lease.id,
            execution_fence=job.execution_fence,
            owner_deletion_epoch=job.owner_deletion_epoch,
            status="WAITING_USER",
            action_code="CORE_DECISION_REQUIRED",
            context_code="CORE_DECISION_BINDING_MISMATCH",
        )

    def heartbeat(self, *, lease_id: UUID) -> bool:
        with self._session_factory.begin() as session:
            lease = session.scalar(
                select(JobExecutionLease).where(JobExecutionLease.id == lease_id).with_for_update()
            )
            if lease is None or lease.released_at is not None:
                return False
            job = session.scalar(select(Job).where(Job.id == lease.job_id).with_for_update())
            owner = (
                session.scalar(select(User).where(User.id == lease.owner_user_id).with_for_update())
                if job is not None
                else None
            )
            if (
                job is None
                or owner is None
                or job.status != "RUNNING"
                or job.active_lease_id != lease.id
                or job.execution_fence != lease.execution_fence
                or job.owner_deletion_epoch != lease.owner_deletion_epoch
                or owner.deletion_epoch != lease.owner_deletion_epoch
                or owner.account_status != "ACTIVE"
            ):
                return False
            lease.heartbeat_at = datetime.now(UTC)
            return True

    def recover_expired_leases(self, *, limit: int = 100) -> int:
        if limit <= 0:
            raise ValueError("lease recovery limit must be positive")
        cutoff = datetime.now(UTC) - timedelta(seconds=self._lease_heartbeat_seconds * 2)
        recovered = 0
        with self._session_factory.begin() as session:
            leases = list(
                session.scalars(
                    select(JobExecutionLease)
                    .where(
                        JobExecutionLease.released_at.is_(None),
                        func.coalesce(JobExecutionLease.heartbeat_at, JobExecutionLease.claimed_at)
                        <= cutoff,
                    )
                    .order_by(JobExecutionLease.claimed_at, JobExecutionLease.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )
            )
            for lease in leases:
                job = session.scalar(select(Job).where(Job.id == lease.job_id).with_for_update())
                if job is None or job.active_lease_id != lease.id:
                    continue
                slot = session.scalar(
                    select(OwnerExecutionSlot)
                    .where(
                        OwnerExecutionSlot.owner_user_id == lease.owner_user_id,
                        OwnerExecutionSlot.slot_no == lease.slot_no,
                    )
                    .with_for_update()
                )
                now = datetime.now(UTC)
                if slot is not None and slot.lease_id == lease.id and slot.job_id == job.id:
                    slot.job_id = None
                    slot.lease_id = None
                    slot.claimed_at = None
                    slot.updated_at = now
                lease.released_at = now
                lease.release_reason = "LEASE_EXPIRED"
                job.active_lease_id = None
                job.execution_fence += 1
                job.updated_at = now
                if job.status == "CANCEL_REQUESTED":
                    job.status = "CANCELLED"
                    job.dispatch_status = "INVALIDATED"
                    job.completed_at = now
                else:
                    job.status = "FAILED_RETRYABLE"
                    job.dispatch_status = "BLOCKED"
                    job.retryable = True
                    job.failure_code = "LEASE_EXPIRED"
                    job.safe_failure_message = "작업 연결이 끊겨 재시도가 필요합니다."
                    session.add(
                        JobRequiredAction(
                            job_id=job.id,
                            owner_user_id=job.owner_user_id,
                            action_code="RETRY",
                            action_status="OPEN",
                            expected_input_version=job.analysis_input_version,
                        )
                    )
                current_command = session.scalar(
                    select(JobCommand)
                    .where(JobCommand.job_id == job.id)
                    .order_by(JobCommand.command_sequence.desc())
                    .limit(1)
                    .with_for_update()
                )
                if current_command is not None and current_command.status in {
                    "PENDING",
                    "ENQUEUED",
                    "CLAIMED",
                }:
                    current_command.status = "INVALIDATED"
                recovered += 1
        return recovered


class CollectionResultWorker:
    """Apply W2 private collection results exactly once against the current Job lease."""

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        sqs: SqsPort,
        result_queue_url: str,
        visibility_timeout_seconds: int = 120,
        long_poll_seconds: int = 20,
    ) -> None:
        if not result_queue_url.strip():
            raise ValueError("result_queue_url must be present")
        if not 1 <= visibility_timeout_seconds <= 43_200:
            raise ValueError("visibility_timeout_seconds must be between 1 and 43200")
        if not 0 <= long_poll_seconds <= 20:
            raise ValueError("long_poll_seconds must be between 0 and 20")
        self._session_factory = session_factory
        self._sqs = sqs
        self._result_queue_url = result_queue_url
        self._visibility_timeout_seconds = visibility_timeout_seconds
        self._long_poll_seconds = long_poll_seconds

    def drain_once(self, *, max_messages: int = _MAX_SQS_BATCH_SIZE) -> QueueWorkerRunResult:
        if not 1 <= max_messages <= _MAX_SQS_BATCH_SIZE:
            raise ValueError("max_messages must be between 1 and 10")
        try:
            deliveries = self._sqs.receive_messages(
                queue_url=self._result_queue_url,
                max_messages=max_messages,
                visibility_timeout_seconds=self._visibility_timeout_seconds,
                wait_time_seconds=self._long_poll_seconds,
            )
        except SqsRetryableError:
            return QueueWorkerRunResult(0, 0, 0, 0, 0, 0, 0)
        acknowledged = retry_scheduled = stale_discarded = rejected_schema = duplicate = 0
        for delivery in deliveries:
            outcome = self._handle_delivery(delivery)
            if outcome.acknowledge:
                try:
                    self._sqs.delete_message(
                        queue_url=self._result_queue_url,
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

    def _handle_delivery(self, delivery: ReceivedSqsMessage) -> _DeliveryOutcome:
        try:
            envelope = _load_json_object(delivery.body)
            message_id = UUID(str(envelope.get("message_id")))
            if (
                envelope.get("producer") != "w2"
                or envelope.get("message_type") != _W2_RESULT_MESSAGE_TYPE
                or envelope.get("channel") != _W2_RESULT_CHANNEL
            ):
                self._record_rejection(
                    message_id=message_id,
                    outcome_code="REJECTED_PRINCIPAL",
                )
                return _DeliveryOutcome(acknowledge=True)
            _validate(
                envelope,
                "w1/v1/private-message-envelope.schema.json",
                "RESULT_ENVELOPE_INVALID",
            )
            result = envelope.get("payload")
            if not isinstance(result, dict):
                raise RuntimeContractError("RESULT_PAYLOAD_INVALID")
            _validate(result, "w2/v1/source-collection.result.schema.json", "RESULT_SCHEMA_INVALID")
        except (KeyError, TypeError, ValueError, RuntimeContractError):
            self._record_rejected_schema_if_possible(delivery.body)
            return _DeliveryOutcome(acknowledge=True, rejected_schema=True)

        with self._session_factory.begin() as session:
            outcome_code, _ = self._apply_result(session=session, result=result)
            recorded = JobService(session).record_inbox_receipt(
                consumer_name=_W2_RESULT_CONSUMER,
                event_id=message_id,
                outcome_code=outcome_code,
            )
            if not recorded:
                return _DeliveryOutcome(acknowledge=True, duplicate=True)
            return _DeliveryOutcome(
                acknowledge=True,
                stale=outcome_code == "STALE_DISCARDED",
                rejected_schema=outcome_code == "REJECTED_SCHEMA",
            )

    def _record_rejected_schema_if_possible(self, body: str) -> None:
        try:
            value = _load_json_object(body)
            message_id = UUID(str(value.get("message_id")))
        except (TypeError, ValueError, RuntimeContractError):
            return
        self._record_rejection(message_id=message_id, outcome_code="REJECTED_SCHEMA")

    def _record_rejection(self, *, message_id: UUID, outcome_code: str) -> None:
        with self._session_factory.begin() as session:
            JobService(session).record_inbox_receipt(
                consumer_name=_W2_RESULT_CONSUMER,
                event_id=message_id,
                outcome_code=outcome_code,
            )

    def accept_commit_ready(
        self, *, staged_result: CommitReadyCollectionResult
    ) -> CommitReadyApplicationResult:
        """Create W1's PREPARE operation and outbox atomically.

        The current W2 ``source-collection.result`` schema deliberately has no
        COMMIT_READY variant, so the raw SQS result consumer above must not
        guess one.  A later W2-owned adapter validates its canonical artifact,
        normalizes it to ``CommitReadyCollectionResult``, then uses this method.
        Until then this is exercised by the W1 fake-gate integration harness.
        """

        with self._session_factory.begin() as session:
            try:
                acceptance = W2CommitGateService(session).create_prepare_operation_with_outbox(
                    owner_user_id=staged_result.owner_user_id,
                    job_id=staged_result.job_id,
                    command_id=staged_result.command_id,
                    execution_fence=staged_result.execution_fence,
                    owner_deletion_epoch=staged_result.owner_deletion_epoch,
                    execution_lease_id=staged_result.execution_lease_id,
                    result_digest=staged_result.result_digest,
                )
            except W2CommitGateError:
                outcome = CommitReadyApplicationResult(outcome_code="STALE_REJECTED")
            else:
                outcome = CommitReadyApplicationResult(
                    outcome_code="PREPARE_CREATED" if acceptance.created else "PREPARE_PENDING",
                    operation_id=acceptance.operation.id,
                    operation_revision=acceptance.operation.operation_revision,
                    prepare_outbox_id=(
                        acceptance.prepare_outbox.id
                        if acceptance.prepare_outbox is not None
                        else None
                    ),
                )
            recorded = JobService(session).record_inbox_receipt(
                consumer_name=_W2_COMMIT_READY_CONSUMER,
                event_id=staged_result.message_id,
                outcome_code=outcome.outcome_code,
            )
            if not recorded:
                return CommitReadyApplicationResult(outcome_code="DUPLICATE")
            return outcome

    def _apply_result(
        self, *, session: Session, result: dict[str, object]
    ) -> tuple[str, str | None]:
        command_id = UUID(str(result["command_id"]))
        job_id = UUID(str(result["job_id"]))
        command = session.scalar(
            select(JobCommand).where(JobCommand.id == command_id).with_for_update()
        )
        job = session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if command is None or job is None or command.job_id != job.id:
            return "STALE_DISCARDED", "COMMAND_NOT_FOUND"
        owner = session.scalar(
            select(User).where(User.id == job.owner_user_id).with_for_update()
        )
        runtime = command.payload.get("runtime") if isinstance(command.payload, dict) else None
        w2_command = (
            command.payload.get("w2_command") if isinstance(command.payload, dict) else None
        )
        if (
            command.command_type not in {"W2_SOURCE_COLLECTION", "W2_DIRECT_SOURCE_REGISTRATION"}
            or not isinstance(runtime, dict)
            or not isinstance(w2_command, dict)
            or not isinstance(runtime.get("lease_id"), str)
            or not isinstance(runtime.get("input_version"), int)
            or result.get("input_version") != runtime["input_version"]
        ):
            return "STALE_DISCARDED", "COMMAND_INPUT_MISMATCH"
        try:
            lease_id = UUID(runtime["lease_id"])
        except ValueError:
            return "STALE_DISCARDED", "COMMAND_LEASE_INVALID"
        if job.status == "CANCEL_REQUESTED":
            self._cancel_current_lease(session=session, job=job, lease_id=lease_id)
            return "STALE_DISCARDED", "CANCEL_REQUESTED"
        if (
            owner is None
            or owner.account_status != "ACTIVE"
            or owner.deletion_epoch != command.owner_deletion_epoch
            or job.status != "RUNNING"
            or job.active_lease_id != lease_id
            or job.execution_fence != command.execution_fence
            or job.owner_deletion_epoch != command.owner_deletion_epoch
            # The result may race the relay's post-send `PENDING -> ENQUEUED` commit.  The
            # matching private command ID is only received by W2 after a broker send, and all
            # current Job/lease/fence/epoch checks above still fence this result.
            or command.status not in {"PENDING", "ENQUEUED"}
        ):
            return "STALE_DISCARDED", "EXECUTION_NOT_CURRENT"
        lease = session.scalar(
            select(JobExecutionLease)
            .where(
                JobExecutionLease.id == lease_id,
                JobExecutionLease.owner_user_id == job.owner_user_id,
            )
            .with_for_update()
        )
        if (
            lease is None
            or lease.released_at is not None
            or lease.execution_fence != command.execution_fence
            or lease.owner_deletion_epoch != command.owner_deletion_epoch
        ):
            return "STALE_DISCARDED", "LEASE_NOT_CURRENT"
        return apply_locked_collection_result(
            session=session,
            owner=owner,
            job=job,
            command=command,
            lease=lease,
            result=result,
        )

    @staticmethod
    def _append_checkpoint(
        *,
        session: Session,
        job: Job,
        checkpoint_ref: str,
        resume_stage: str,
    ) -> None:
        previous_revision = session.scalar(
            select(func.max(JobCheckpoint.checkpoint_revision)).where(
                JobCheckpoint.job_id == job.id
            )
        )
        session.add(
            JobCheckpoint(
                job_id=job.id,
                owner_user_id=job.owner_user_id,
                checkpoint_revision=(previous_revision or 0) + 1,
                checkpoint_schema_version="w2.collection.v1",
                analysis_input_version=job.analysis_input_version,
                execution_fence=job.execution_fence,
                owner_deletion_epoch=job.owner_deletion_epoch,
                resume_stage=resume_stage,
                state_ref=checkpoint_ref,
                resume_payload={},
                resumable=True,
            )
        )

    @staticmethod
    def _apply_transition(
        *,
        session: Session,
        job: Job,
        command: JobCommand,
        lease: JobExecutionLease,
        result: dict[str, object],
    ) -> None:
        required_actions = result.get("required_actions")
        retry_not_before = result.get("retry_not_before")
        completeness = result["completion_kind"]
        now = datetime.now(UTC)
        if isinstance(required_actions, list) and required_actions:
            CollectionResultWorker._release_lease(
                session=session, job=job, lease=lease, reason="WAITING_USER"
            )
            job.status = "WAITING_USER"
            job.dispatch_status = "BLOCKED"
            job.retryable = False
            session.add(
                JobRequiredAction(
                    job_id=job.id,
                    owner_user_id=job.owner_user_id,
                    action_code="CONTINUE_LIMITED",
                    action_status="OPEN",
                    expected_input_version=job.analysis_input_version,
                    expected_result_version=str(result["result_version"]),
                )
            )
        elif isinstance(retry_not_before, str):
            CollectionResultWorker._release_lease(
                session=session, job=job, lease=lease, reason="PAUSED_RATE_LIMIT"
            )
            job.status = "PAUSED_RATE_LIMIT"
            job.dispatch_status = "BLOCKED"
            job.retryable = True
            job.retry_after = CollectionResultWorker._parse_timestamp_or_none(retry_not_before)
            session.add(
                JobRequiredAction(
                    job_id=job.id,
                    owner_user_id=job.owner_user_id,
                    action_code="RETRY",
                    action_status="OPEN",
                    expected_input_version=job.analysis_input_version,
                    expected_result_version=str(result["result_version"]),
                )
            )
        elif completeness == "complete":
            CollectionResultWorker._release_lease(
                session=session, job=job, lease=lease, reason="SUCCEEDED"
            )
            job.status = "SUCCEEDED"
            job.dispatch_status = "ENQUEUED"
            job.completed_at = now
            job.retryable = False
        else:
            CollectionResultWorker._release_lease(
                session=session, job=job, lease=lease, reason="FAILED_RETRYABLE"
            )
            job.status = "FAILED_RETRYABLE"
            job.dispatch_status = "BLOCKED"
            job.retryable = True
            job.failure_code = "W2_COLLECTION_INCOMPLETE"
            job.safe_failure_message = _bounded_message(
                result.get("message_ko"), fallback="수집 결과가 완전하지 않아 재시도가 필요합니다."
            )
            session.add(
                JobRequiredAction(
                    job_id=job.id,
                    owner_user_id=job.owner_user_id,
                    action_code="RETRY",
                    action_status="OPEN",
                    expected_input_version=job.analysis_input_version,
                    expected_result_version=str(result["result_version"]),
                )
            )
        command.status = "CONSUMED"
        command.consumed_at = now
        job.completeness = str(completeness)
        job.updated_at = now

    @staticmethod
    def _parse_timestamp_or_none(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _cancel_current_lease(self, *, session: Session, job: Job, lease_id: UUID) -> None:
        lease = session.scalar(
            select(JobExecutionLease).where(JobExecutionLease.id == lease_id).with_for_update()
        )
        if lease is None or lease.released_at is not None or job.active_lease_id != lease.id:
            return
        self._release_lease(session=session, job=job, lease=lease, reason="CANCELLED")
        job.status = "CANCELLED"
        job.dispatch_status = "INVALIDATED"
        job.completed_at = datetime.now(UTC)
        job.updated_at = datetime.now(UTC)

    @staticmethod
    def _release_lease(
        *, session: Session, job: Job, lease: JobExecutionLease, reason: str
    ) -> None:
        slot = session.scalar(
            select(OwnerExecutionSlot)
            .where(
                OwnerExecutionSlot.owner_user_id == lease.owner_user_id,
                OwnerExecutionSlot.slot_no == lease.slot_no,
            )
            .with_for_update()
        )
        if slot is None or slot.lease_id != lease.id or slot.job_id != job.id:
            raise RuntimeError("runtime lease no longer owns its execution slot")
        now = datetime.now(UTC)
        lease.released_at = now
        lease.release_reason = reason
        slot.job_id = None
        slot.lease_id = None
        slot.claimed_at = None
        slot.updated_at = now
        job.active_lease_id = None


def apply_locked_collection_result(
    *,
    session: Session,
    owner: User | None,
    job: Job,
    command: JobCommand,
    lease: JobExecutionLease,
    result: dict[str, object],
) -> tuple[str, str | None]:
    """Apply a validated result while caller-owned Job/command/lease locks remain held.

    This is deliberately the only concrete writer used by both the legacy
    result worker and the staged gate finalizer. It does no ``SELECT ... FOR
    UPDATE`` itself, so the gate finalizer cannot invert the documented W1
    lock ordering.
    """

    runtime = command.payload.get("runtime") if isinstance(command.payload, dict) else None
    w2_command = command.payload.get("w2_command") if isinstance(command.payload, dict) else None
    if (
        command.command_type not in {"W2_SOURCE_COLLECTION", "W2_DIRECT_SOURCE_REGISTRATION"}
        or not isinstance(runtime, dict)
        or not isinstance(w2_command, dict)
        or not isinstance(runtime.get("input_version"), int)
        or result.get("input_version") != runtime["input_version"]
        or result.get("command_id") != str(command.id)
        or result.get("job_id") != str(job.id)
    ):
        return "STALE_DISCARDED", "COMMAND_INPUT_MISMATCH"
    if (
        owner is None
        or owner.account_status != "ACTIVE"
        or owner.deletion_epoch != command.owner_deletion_epoch
        or job.status != "RUNNING"
        or job.active_lease_id != lease.id
        or lease.released_at is not None
        or lease.execution_fence != command.execution_fence
        or lease.owner_deletion_epoch != command.owner_deletion_epoch
    ):
        return "STALE_DISCARDED", "EXECUTION_NOT_CURRENT"
    checkpoint_ref = result.get("checkpoint_ref")
    resume_stage = result.get("resume_stage")
    if checkpoint_ref is not None:
        if (
            not isinstance(checkpoint_ref, str)
            or not checkpoint_ref.strip()
            or len(checkpoint_ref) > 512
        ):
            return "REJECTED_SCHEMA", "CHECKPOINT_REFERENCE_INVALID"
        if not isinstance(resume_stage, str):
            return "REJECTED_SCHEMA", "CHECKPOINT_STAGE_REQUIRED"
        CollectionResultWorker._append_checkpoint(
            session=session,
            job=job,
            checkpoint_ref=checkpoint_ref,
            resume_stage=resume_stage,
        )
    CollectionResultWorker._apply_transition(
        session=session,
        job=job,
        command=command,
        lease=lease,
        result=result,
    )
    return "APPLIED", None
