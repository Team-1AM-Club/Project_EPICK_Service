from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.jobs import Job, JobCommand, OutboxMessage
from app.runtime.sqs import SqsFinalDeliveryError, SqsPort, SqsRetryableError

_PRIVATE_DISPATCH_MESSAGE_TYPE = "job.command.dispatch"
_W1_EXECUTION_QUEUE = "w1_execution"
_MAX_SQS_BODY_BYTES = 16 * 1024


class OutboxRelayError(RuntimeError):
    """An internal relay error that can safely be persisted as a bounded code."""

    def __init__(self, code: str) -> None:
        if not code or len(code) > 64:
            raise ValueError("outbox relay error codes must be between 1 and 64 characters")
        super().__init__(code)
        self.code = code


class OutboxRouteError(OutboxRelayError):
    pass


class OutboxPayloadError(OutboxRelayError):
    pass


@dataclass(frozen=True)
class QueueRoute:
    logical_key: str
    message_type: str


class QueueUrlRegistry:
    """Routes are code-owned logical keys; queue URLs are deployment-provided values."""

    _ROUTES = {
        _PRIVATE_DISPATCH_MESSAGE_TYPE: QueueRoute(
            logical_key=_W1_EXECUTION_QUEUE,
            message_type=_PRIVATE_DISPATCH_MESSAGE_TYPE,
        )
    }

    def __init__(self, *, w1_execution_queue_url: str | None) -> None:
        self._urls = {_W1_EXECUTION_QUEUE: w1_execution_queue_url}

    @property
    def supported_message_types(self) -> tuple[str, ...]:
        return tuple(self._ROUTES)

    def resolve(self, *, message_type: str) -> tuple[QueueRoute, str]:
        route = self._ROUTES.get(message_type)
        if route is None:
            raise OutboxRouteError("OUTBOX_ROUTE_UNKNOWN")
        queue_url = self._urls[route.logical_key]
        if not isinstance(queue_url, str) or not queue_url.strip():
            raise OutboxRouteError("OUTBOX_ROUTE_NOT_CONFIGURED")
        return route, queue_url


@dataclass(frozen=True)
class RelayClaim:
    outbox_id: UUID
    claim_token: UUID
    queue_url: str
    body: str
    message_attributes: dict[str, str]
    attempts: int


@dataclass(frozen=True)
class RelayClaimBatch:
    claims: tuple[RelayClaim, ...]
    failed_final: int


@dataclass(frozen=True)
class RelayRunResult:
    claimed: int
    published: int
    retry_scheduled: int
    failed_final: int
    stale_completion: int


@lru_cache(maxsize=1)
def _private_dispatch_validator() -> Draft202012Validator:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "w1"
        / "v1"
        / "private-job-dispatch.schema.json"
    )
    with schema_path.open(encoding="utf-8") as stream:
        schema = json.load(stream)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


class OutboxRelay:
    """Publish committed private Job outbox references after their DB transaction ends.

    The relay never runs in an API request.  It claims rows in a short transaction, makes the
    broker call outside that transaction, and conditionally records the result using the claim
    token.  A crash between SQS acceptance and the final database write is intentionally
    recoverable as a duplicate Standard-SQS delivery.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        sqs: SqsPort,
        queues: QueueUrlRegistry,
        relay_id: str,
        lease_seconds: int = 120,
        retry_base_seconds: int = 5,
        retry_max_seconds: int = 300,
    ) -> None:
        if not relay_id or len(relay_id) > 128:
            raise ValueError("relay_id must be between 1 and 128 characters")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if retry_base_seconds <= 0 or retry_max_seconds < retry_base_seconds:
            raise ValueError("relay retry bounds are invalid")
        self._session_factory = session_factory
        self._sqs = sqs
        self._queues = queues
        self._relay_id = relay_id
        self._lease_seconds = lease_seconds
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds

    def drain_once(self, *, limit: int) -> RelayRunResult:
        """Claim at most ``limit`` due records and make one best-effort delivery attempt each."""

        batch = self.claim_due(limit=limit)
        published = 0
        retry_scheduled = 0
        failed_final = batch.failed_final
        stale_completion = 0
        for claim in batch.claims:
            try:
                self._sqs.send_message(
                    queue_url=claim.queue_url,
                    body=claim.body,
                    message_attributes=claim.message_attributes,
                )
            except SqsRetryableError as error:
                if self._mark_retryable(claim=claim, error_code=error.code):
                    retry_scheduled += 1
                else:
                    stale_completion += 1
            except SqsFinalDeliveryError as error:
                if self._mark_failed_final(claim=claim, error_code=error.code):
                    failed_final += 1
                else:
                    stale_completion += 1
            except Exception:
                # An unclassified client failure is ambiguous: SQS may have accepted the
                # message, so preserve at-least-once semantics by retrying the same command.
                if self._mark_retryable(claim=claim, error_code="SQS_UNEXPECTED_ERROR"):
                    retry_scheduled += 1
                else:
                    stale_completion += 1
            else:
                if self._mark_published(claim=claim):
                    published += 1
                else:
                    stale_completion += 1
        return RelayRunResult(
            claimed=len(batch.claims),
            published=published,
            retry_scheduled=retry_scheduled,
            failed_final=failed_final,
            stale_completion=stale_completion,
        )

    def claim_due(self, *, limit: int) -> RelayClaimBatch:
        """Persist relay leases before any SQS operation and return immutable send instructions."""

        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("relay claim limit must be positive")
        claims: list[RelayClaim] = []
        failed_final = 0
        with self._session_factory.begin() as session:
            messages = self._select_claimable_messages(session=session, limit=limit)
            for message in messages:
                claim_token = uuid4()
                now = datetime.now(UTC)
                command: JobCommand | None = None
                job: Job | None = None
                message.status = "PUBLISHING"
                message.attempts += 1
                message.relay_claim_token = claim_token
                message.relay_claimed_by = self._relay_id
                message.relay_lease_expires_at = now + timedelta(seconds=self._lease_seconds)

                try:
                    command, job = self._require_private_job_command(
                        session=session, message=message
                    )
                    _, queue_url = self._queues.resolve(message_type=message.message_type)
                    body = self._serialize_private_dispatch(
                        message=message,
                        command=command,
                        job=job,
                        issued_at=now,
                    )
                except (OutboxPayloadError, OutboxRouteError) as error:
                    if command is None or job is None:
                        command, job = self._read_current_job_command(
                            session=session, message=message
                        )
                    self._mark_failed_final_in_transaction(
                        message=message,
                        command=command,
                        job=job,
                        error_code=error.code,
                        now=now,
                    )
                    failed_final += 1
                    continue

                claims.append(
                    RelayClaim(
                        outbox_id=message.id,
                        claim_token=claim_token,
                        queue_url=queue_url,
                        body=body,
                        message_attributes={
                            "epick_message_type": message.message_type,
                            "epick_schema_version": message.schema_version,
                            "epick_message_id": str(command.id),
                            "epick_command_id": str(command.id),
                            "epick_job_id": str(job.id),
                        },
                        attempts=message.attempts,
                    )
                )
            session.flush()
        return RelayClaimBatch(claims=tuple(claims), failed_final=failed_final)

    def _select_claimable_messages(self, *, session: Session, limit: int) -> list[OutboxMessage]:
        return list(
            session.scalars(
                select(OutboxMessage)
                .where(
                    OutboxMessage.visibility_scope == "PRIVATE",
                    OutboxMessage.message_type.in_(self._queues.supported_message_types),
                    or_(
                        OutboxMessage.status.in_(("PENDING", "FAILED_RETRYABLE")),
                        (
                            (OutboxMessage.status == "PUBLISHING")
                            & OutboxMessage.relay_lease_expires_at.is_not(None)
                            & (OutboxMessage.relay_lease_expires_at <= func.now())
                        ),
                    ),
                    OutboxMessage.available_at <= func.now(),
                )
                .order_by(OutboxMessage.available_at, OutboxMessage.created_at, OutboxMessage.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )

    def _require_private_job_command(
        self, *, session: Session, message: OutboxMessage
    ) -> tuple[JobCommand, Job]:
        if (
            message.command_id is None
            or message.job_id is None
            or message.owner_user_id is None
            or message.execution_fence is None
            or message.owner_deletion_epoch is None
            or message.schema_version != "1.0"
        ):
            raise OutboxPayloadError("OUTBOX_PRIVATE_REFERENCE_INVALID")
        command = session.scalar(
            select(JobCommand).where(JobCommand.id == message.command_id).with_for_update()
        )
        job = session.scalar(select(Job).where(Job.id == message.job_id).with_for_update())
        if command is None or job is None:
            raise OutboxPayloadError("OUTBOX_CANONICAL_COMMAND_NOT_FOUND")
        if (
            command.job_id != message.job_id
            or command.owner_user_id != message.owner_user_id
            or command.execution_fence != message.execution_fence
            or command.owner_deletion_epoch != message.owner_deletion_epoch
            or command.command_schema_version != message.schema_version
            or job.owner_user_id != message.owner_user_id
        ):
            raise OutboxPayloadError("OUTBOX_CANONICAL_REFERENCE_MISMATCH")
        payload = message.payload
        expected_payload = {
            "command_id": str(command.id),
            "job_id": str(job.id),
            "execution_fence": command.execution_fence,
            "owner_deletion_epoch": command.owner_deletion_epoch,
        }
        if (
            not isinstance(payload, dict)
            or set(payload).difference({*expected_payload, "checkpoint_id"})
            or any(payload.get(key) != value for key, value in expected_payload.items())
        ):
            raise OutboxPayloadError("OUTBOX_PRIVATE_PAYLOAD_INVALID")
        command_payload = command.payload
        if (
            not isinstance(command_payload, dict)
            or set(command_payload).difference({"command_type", "checkpoint_id"})
            or command_payload.get("command_type") != command.command_type
        ):
            raise OutboxPayloadError("OUTBOX_COMMAND_PAYLOAD_INVALID")
        outbox_checkpoint_id = payload.get("checkpoint_id")
        command_checkpoint_id = command_payload.get("checkpoint_id")
        if outbox_checkpoint_id != command_checkpoint_id:
            raise OutboxPayloadError("OUTBOX_CHECKPOINT_REFERENCE_MISMATCH")
        if outbox_checkpoint_id is not None:
            if not isinstance(outbox_checkpoint_id, str):
                raise OutboxPayloadError("OUTBOX_CHECKPOINT_REFERENCE_INVALID")
            try:
                UUID(outbox_checkpoint_id)
            except ValueError as error:
                raise OutboxPayloadError("OUTBOX_CHECKPOINT_REFERENCE_INVALID") from error
        if command.status != "PENDING":
            raise OutboxPayloadError("OUTBOX_COMMAND_NOT_PENDING")
        return command, job

    @staticmethod
    def _serialize_private_dispatch(
        *,
        message: OutboxMessage,
        command: JobCommand,
        job: Job,
        issued_at: datetime,
    ) -> str:
        dispatch: dict[str, object] = {
            "schema_version": message.schema_version,
            # W1's replay-safe dispatch ID is deliberately the canonical command ID.
            "message_id": str(command.id),
            "command_id": str(command.id),
            "job_id": str(job.id),
            "execution_fence": command.execution_fence,
            "owner_deletion_epoch": command.owner_deletion_epoch,
            "issued_at": issued_at.isoformat().replace("+00:00", "Z"),
            "payload_ref": str(message.id),
        }
        errors = list(_private_dispatch_validator().iter_errors(dispatch))
        if errors:
            raise OutboxPayloadError("OUTBOX_DISPATCH_SCHEMA_INVALID")
        if dispatch["message_id"] != dispatch["command_id"]:
            raise OutboxPayloadError("OUTBOX_MESSAGE_COMMAND_MISMATCH")
        body = json.dumps(dispatch, ensure_ascii=False, separators=(",", ":"))
        if len(body.encode("utf-8")) > _MAX_SQS_BODY_BYTES:
            raise OutboxPayloadError("OUTBOX_DISPATCH_TOO_LARGE")
        return body

    def _mark_published(self, *, claim: RelayClaim) -> bool:
        with self._session_factory.begin() as session:
            message = self._get_current_claim_for_update(session=session, claim=claim)
            if message is None:
                return False
            now = datetime.now(UTC)
            message.status = "PUBLISHED"
            message.published_at = now
            self._clear_claim(message)
            message.last_error_code = None
            message.last_error_at = None

            command, job = self._read_current_job_command(session=session, message=message)
            if (
                command is not None
                and job is not None
                and command.status == "PENDING"
                and job.status == "QUEUED"
                and job.dispatch_status == "OUTBOX_PENDING"
            ):
                command.status = "ENQUEUED"
                job.dispatch_status = "ENQUEUED"
                job.updated_at = now
            session.flush()
            return True

    def _mark_retryable(self, *, claim: RelayClaim, error_code: str) -> bool:
        with self._session_factory.begin() as session:
            message = self._get_current_claim_for_update(session=session, claim=claim)
            if message is None:
                return False
            now = datetime.now(UTC)
            message.status = "FAILED_RETRYABLE"
            message.available_at = now + timedelta(
                seconds=self._retry_delay_seconds(message.attempts)
            )
            self._clear_claim(message)
            message.last_error_code = self._bounded_error_code(error_code)
            message.last_error_at = now
            session.flush()
            return True

    def _mark_failed_final(self, *, claim: RelayClaim, error_code: str) -> bool:
        with self._session_factory.begin() as session:
            message = self._get_current_claim_for_update(session=session, claim=claim)
            if message is None:
                return False
            command, job = self._read_current_job_command(session=session, message=message)
            self._mark_failed_final_in_transaction(
                message=message,
                command=command,
                job=job,
                error_code=error_code,
                now=datetime.now(UTC),
            )
            session.flush()
            return True

    @staticmethod
    def _get_current_claim_for_update(
        *, session: Session,
        claim: RelayClaim,
    ) -> OutboxMessage | None:
        message = session.scalar(
            select(OutboxMessage).where(OutboxMessage.id == claim.outbox_id).with_for_update()
        )
        if (
            message is None
            or message.status != "PUBLISHING"
            or message.relay_claim_token != claim.claim_token
        ):
            return None
        return message

    @staticmethod
    def _read_current_job_command(
        *, session: Session,
        message: OutboxMessage,
    ) -> tuple[JobCommand | None, Job | None]:
        command = (
            session.scalar(
                select(JobCommand).where(JobCommand.id == message.command_id).with_for_update()
            )
            if message.command_id is not None
            else None
        )
        job = (
            session.scalar(select(Job).where(Job.id == message.job_id).with_for_update())
            if message.job_id is not None
            else None
        )
        return command, job

    @staticmethod
    def _clear_claim(message: OutboxMessage) -> None:
        message.relay_claim_token = None
        message.relay_claimed_by = None
        message.relay_lease_expires_at = None

    @staticmethod
    def _bounded_error_code(error_code: str) -> str:
        return error_code[:64] if error_code else "OUTBOX_RELAY_ERROR"

    def _retry_delay_seconds(self, attempts: int) -> int:
        exponent = max(attempts - 1, 0)
        return min(self._retry_base_seconds * (2**exponent), self._retry_max_seconds)

    @classmethod
    def _mark_failed_final_in_transaction(
        cls,
        *,
        message: OutboxMessage,
        command: JobCommand | None,
        job: Job | None,
        error_code: str,
        now: datetime,
    ) -> None:
        message.status = "FAILED_FINAL"
        cls._clear_claim(message)
        message.last_error_code = cls._bounded_error_code(error_code)
        message.last_error_at = now
        if command is not None and command.status == "PENDING":
            command.status = "FAILED"
        if job is not None and job.status == "QUEUED" and job.dispatch_status == "OUTBOX_PENDING":
            job.dispatch_status = "BLOCKED"
            job.updated_at = now
