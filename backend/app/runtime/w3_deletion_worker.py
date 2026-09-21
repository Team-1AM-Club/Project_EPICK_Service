from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.deletion import DeletionRequest, DeletionTarget
from app.models.identity import User
from app.models.jobs import InboxReceipt, OutboxMessage
from app.runtime.outbox_relay import (
    W3_OWNER_DELETION_MESSAGE_TYPE,
    W3_SOURCE_RETIREMENT_MESSAGE_TYPE,
    OutboxRelay,
)
from app.runtime.sqs import SqsDeliveryError, SqsPort, SqsRetryableError
from app.runtime.w3_source_retirement_worker import apply_source_retirement_receipt
from app.services.deletion import DeletionOrchestrationService

_MAX_BODY_BYTES = 16 * 1024
_CONSUMER_NAME = "w1.w3.retention-receipt"


class TransactionFactory(Protocol):
    def begin(self) -> Any: ...


class W3RetentionReceiptContractError(RuntimeError):
    pass


class W3RetentionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["w3.private.w1-lifecycle-receipt/1.0"]
    message_type: Literal["w3.private.w1.lifecycle-receipt"]
    receipt_id: UUID
    occurred_at: AwareDatetime
    visibility_scope: Literal["PRIVATE"]
    producer: Literal["w3"]
    command_id: UUID
    target_ref: UUID
    operation: Literal["DELETE_OWNER", "RETIRE_SOURCE"]
    outcome: Literal["APPLIED", "DUPLICATE", "STALE"]
    affected_count: int = Field(ge=0)
    applied_epoch: int | None = Field(default=None, ge=0)
    effective_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def target_matches_operation(self) -> W3RetentionReceipt:
        if self.operation == "DELETE_OWNER":
            if self.applied_epoch is None or self.effective_at is not None:
                raise ValueError("DELETE_OWNER_RESULT_FIELDS_INVALID")
        elif self.applied_epoch is not None or self.effective_at is None:
            raise ValueError("RETIRE_SOURCE_RESULT_FIELDS_INVALID")
        return self


@dataclass(frozen=True, slots=True)
class W3RetentionApplyResult:
    command_id: UUID
    operation: str
    outcome: str
    duplicate_receipt: bool


@dataclass(frozen=True, slots=True)
class W3RetentionWorkerResult:
    received: int
    acknowledged: int
    retry_scheduled: int
    applied: int
    duplicate: int
    stale: int
    conflict: int
    terminal_rejected: int


def parse_w3_retention_receipt(body: str) -> W3RetentionReceipt:
    if len(body.encode("utf-8")) > _MAX_BODY_BYTES:
        raise W3RetentionReceiptContractError("W3_RETENTION_RECEIPT_TOO_LARGE")
    try:
        return W3RetentionReceipt.model_validate_json(body)
    except ValidationError as error:
        raise W3RetentionReceiptContractError("W3_RETENTION_RECEIPT_INVALID") from error


def _command_body(message: OutboxMessage) -> str:
    try:
        return OutboxRelay._serialize_w3_retention_command(message=message)
    except Exception as error:
        raise W3RetentionReceiptContractError("W3_RETENTION_COMMAND_INVALID") from error


def receipt_digest(body: str) -> str:
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


class W3RetentionReceiptService:
    """Apply one authenticated W3 receipt in the caller's database transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def apply(self, *, body: str) -> W3RetentionApplyResult:
        receipt = parse_w3_retention_receipt(body)
        digest = receipt_digest(body)
        message = self.session.scalar(
            select(OutboxMessage)
            .where(OutboxMessage.id == receipt.command_id)
            .with_for_update()
        )
        if message is None:
            raise W3RetentionReceiptContractError("W3_RETENTION_COMMAND_NOT_FOUND")
        expected_type = (
            W3_OWNER_DELETION_MESSAGE_TYPE
            if receipt.operation == "DELETE_OWNER"
            else W3_SOURCE_RETIREMENT_MESSAGE_TYPE
        )
        if message.message_type != expected_type:
            raise W3RetentionReceiptContractError("W3_RETENTION_OPERATION_MISMATCH")
        _command_body(message)

        existing = self.session.scalar(
            select(InboxReceipt).where(
                InboxReceipt.consumer_name == _CONSUMER_NAME,
                InboxReceipt.event_id == receipt.command_id,
            )
        )
        if existing is not None:
            if (
                existing.payload_digest != digest
                or existing.outcome_code != receipt.outcome
                or existing.schema_version != receipt.schema_version
            ):
                raise W3RetentionReceiptContractError("W3_RETENTION_RECEIPT_CONFLICT")
            return W3RetentionApplyResult(
                command_id=receipt.command_id,
                operation=receipt.operation,
                outcome=receipt.outcome,
                duplicate_receipt=True,
            )

        if receipt.operation == "DELETE_OWNER":
            self._apply_owner_deletion(message=message, receipt=receipt)
        else:
            try:
                apply_source_retirement_receipt(
                    session=self.session,
                    message=message,
                    receipt=receipt,
                )
            except ValueError as error:
                raise W3RetentionReceiptContractError(str(error)) from error
        self.session.add(
            InboxReceipt(
                consumer_name=_CONSUMER_NAME,
                event_id=receipt.command_id,
                outcome_code=receipt.outcome,
                payload_digest=digest,
                producer_name="w3",
                schema_version=receipt.schema_version,
            )
        )
        self.session.flush()
        return W3RetentionApplyResult(
            command_id=receipt.command_id,
            operation=receipt.operation,
            outcome=receipt.outcome,
            duplicate_receipt=False,
        )

    def _apply_owner_deletion(
        self,
        *,
        message: OutboxMessage,
        receipt: W3RetentionReceipt,
    ) -> None:
        if (
            message.deletion_request_id is None
            or message.deletion_target_id is None
            or message.owner_user_id is None
            or message.owner_deletion_epoch is None
            or receipt.target_ref != message.deletion_target_id
        ):
            raise W3RetentionReceiptContractError("W3_DELETION_RECEIPT_BINDING_MISMATCH")
        owner = self.session.scalar(
            select(User).where(User.id == message.owner_user_id).with_for_update()
        )
        request = self.session.scalar(
            select(DeletionRequest)
            .where(DeletionRequest.id == message.deletion_request_id)
            .with_for_update()
        )
        target = self.session.scalar(
            select(DeletionTarget)
            .where(
                DeletionTarget.id == message.deletion_target_id,
                DeletionTarget.deletion_request_id == message.deletion_request_id,
            )
            .with_for_update()
        )
        if (
            target is None
            or request is None
            or owner is None
            or target.store_type != "W3_CORE_RUNTIME"
            or request.owner_user_id != message.owner_user_id
            or request.owner_deletion_epoch != message.owner_deletion_epoch
            or owner.deletion_epoch != message.owner_deletion_epoch
        ):
            raise W3RetentionReceiptContractError("W3_DELETION_CURRENT_EPOCH_MISMATCH")

        if (
            receipt.outcome in {"APPLIED", "DUPLICATE"}
            and receipt.applied_epoch != message.owner_deletion_epoch
        ):
            raise W3RetentionReceiptContractError("W3_DELETION_RECEIPT_EPOCH_MISMATCH")

        service = DeletionOrchestrationService(self.session)
        if receipt.outcome in {"APPLIED", "DUPLICATE"}:
            service.acknowledge_target(
                owner_user_id=message.owner_user_id,
                deletion_request_id=message.deletion_request_id,
                deletion_target_id=message.deletion_target_id,
                ack_epoch=message.owner_deletion_epoch,
                ack_event_id=receipt.command_id,
            )
            return
        service.record_target_failure(
            owner_user_id=message.owner_user_id,
            deletion_request_id=message.deletion_request_id,
            deletion_target_id=message.deletion_target_id,
            failure_code=f"W3_DELETE_{receipt.outcome}",
        )


class W3RetentionReceiptWorker:
    """Consume authenticated W3 receipts and delete SQS only after DB commit."""

    def __init__(
        self,
        *,
        session_factory: TransactionFactory,
        sqs: SqsPort,
        queue_url: str,
        expected_sender_id: str,
        batch_size: int = 10,
        visibility_timeout_seconds: int = 120,
        wait_time_seconds: int = 20,
        service_factory: Callable[[Any], W3RetentionReceiptService] = W3RetentionReceiptService,
    ) -> None:
        if not queue_url.strip() or not expected_sender_id.strip() or ":" in expected_sender_id:
            raise ValueError("W3 receipt queue and stable SenderId are required")
        if not 1 <= batch_size <= 10:
            raise ValueError("batch_size must be between 1 and 10")
        if not 1 <= visibility_timeout_seconds <= 43_200:
            raise ValueError("visibility_timeout_seconds must be between 1 and 43200")
        if not 0 <= wait_time_seconds <= 20:
            raise ValueError("wait_time_seconds must be between 0 and 20")
        self._session_factory = session_factory
        self._sqs = sqs
        self._queue_url = queue_url
        self._expected_sender_id = expected_sender_id
        self._batch_size = batch_size
        self._visibility_timeout_seconds = visibility_timeout_seconds
        self._wait_time_seconds = wait_time_seconds
        self._service_factory = service_factory

    def drain_once(self) -> W3RetentionWorkerResult:
        try:
            deliveries = self._sqs.receive_messages(
                queue_url=self._queue_url,
                max_messages=self._batch_size,
                visibility_timeout_seconds=self._visibility_timeout_seconds,
                wait_time_seconds=self._wait_time_seconds,
            )
        except SqsRetryableError:
            return W3RetentionWorkerResult(0, 0, 1, 0, 0, 0, 0, 0)
        acknowledged = retry_scheduled = terminal_rejected = 0
        counts = {"APPLIED": 0, "DUPLICATE": 0, "STALE": 0, "CONFLICT": 0}
        for delivery in deliveries:
            sender_id = delivery.sender_id
            if sender_id is None or sender_id.split(":", maxsplit=1)[0] != self._expected_sender_id:
                terminal_rejected += 1
                deleted = self._delete(delivery.receipt_handle)
                acknowledged += int(deleted)
                retry_scheduled += int(not deleted)
                continue
            try:
                with self._session_factory.begin() as session:
                    result = self._service_factory(session).apply(body=delivery.body)
            except W3RetentionReceiptContractError:
                terminal_rejected += 1
                deleted = self._delete(delivery.receipt_handle)
                acknowledged += int(deleted)
                retry_scheduled += int(not deleted)
                continue
            except (SQLAlchemyError, OSError, TimeoutError):
                retry_scheduled += 1
                continue
            if result.duplicate_receipt or result.outcome == "DUPLICATE":
                counts["DUPLICATE"] += 1
            else:
                counts[result.outcome] += 1
            deleted = self._delete(delivery.receipt_handle)
            acknowledged += int(deleted)
            retry_scheduled += int(not deleted)
        return W3RetentionWorkerResult(
            received=len(deliveries),
            acknowledged=acknowledged,
            retry_scheduled=retry_scheduled,
            applied=counts["APPLIED"],
            duplicate=counts["DUPLICATE"],
            stale=counts["STALE"],
            conflict=counts["CONFLICT"],
            terminal_rejected=terminal_rejected,
        )

    def _delete(self, receipt_handle: str) -> bool:
        try:
            self._sqs.delete_message(queue_url=self._queue_url, receipt_handle=receipt_handle)
        except SqsDeliveryError:
            return False
        return True


# Task-plan compatibility: this worker owns W3 deletion receipts while routing
# Source-retirement receipts through the same authenticated queue consumer.
W3DeletionWorker = W3RetentionReceiptWorker
