from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.exc import SQLAlchemyError

from app.runtime.sqs import SqsDeliveryError, SqsPort, SqsRetryableError
from app.runtime.w4_question_core_decision import (
    W4QuestionCoreContractError,
    W4QuestionCoreReceiptOutcome,
    parse_w4_question_core_event,
)
from app.services.w4_question_core_inbound import (
    W4QuestionCoreInboundError,
    W4QuestionCoreInboundService,
)

_MAX_SQS_BATCH_SIZE = 10


class TransactionFactory(Protocol):
    def begin(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class AuthenticatedDeliveryContext:
    """Identity derived from SQS system metadata, never from the body."""

    sender_id: str
    logical_principal: str


@dataclass(frozen=True, slots=True)
class W4QuestionCoreDecisionWorkerResult:
    received: int
    acknowledged: int
    retry_scheduled: int
    applied: int
    duplicate: int
    terminal_rejected: int


class W4QuestionCoreDecisionWorker:
    """Apply authenticated W4 decisions and ACK only durable terminal outcomes."""

    def __init__(
        self,
        *,
        session_factory: TransactionFactory,
        sqs: SqsPort,
        queue_url: str,
        expected_sender_id: str,
        expected_producer: str = "w4",
        batch_size: int = _MAX_SQS_BATCH_SIZE,
        visibility_timeout_seconds: int = 120,
        wait_time_seconds: int = 20,
        service_factory: Callable[
            [Any], W4QuestionCoreInboundService
        ] = W4QuestionCoreInboundService,
    ) -> None:
        if not queue_url.strip():
            raise ValueError("queue_url must be present")
        if not expected_sender_id.strip() or ":" in expected_sender_id:
            raise ValueError("expected_sender_id must be a stable principal id without session")
        if expected_producer != "w4":
            raise ValueError("expected_producer must be w4")
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
        self._service_factory = service_factory

    def drain_once(self) -> W4QuestionCoreDecisionWorkerResult:
        try:
            deliveries = self._sqs.receive_messages(
                queue_url=self._queue_url,
                max_messages=self._batch_size,
                visibility_timeout_seconds=self._visibility_timeout_seconds,
                wait_time_seconds=self._wait_time_seconds,
            )
        except SqsRetryableError:
            return W4QuestionCoreDecisionWorkerResult(0, 0, 1, 0, 0, 0)

        acknowledged = retry_scheduled = applied = duplicate = terminal_rejected = 0
        for delivery in deliveries:
            context = self._authenticate(sender_id=delivery.sender_id)
            if context is None:
                terminal_rejected += 1
                if self._delete(delivery.receipt_handle):
                    acknowledged += 1
                else:
                    retry_scheduled += 1
                continue
            try:
                event = parse_w4_question_core_event(delivery.body)
                if event.producer != self._expected_producer:
                    raise W4QuestionCoreContractError("W4_QUESTION_CORE_PRODUCER_MISMATCH")
                with self._session_factory.begin() as session:
                    receipt = self._service_factory(session).apply(
                        body=delivery.body,
                        authenticated_principal=context.logical_principal,
                        expected_principal=self._expected_sender_id,
                    )
            except (W4QuestionCoreContractError, W4QuestionCoreInboundError):
                terminal_rejected += 1
                if self._delete(delivery.receipt_handle):
                    acknowledged += 1
                else:
                    retry_scheduled += 1
                continue
            except (SQLAlchemyError, OSError, TimeoutError):
                retry_scheduled += 1
                continue

            if (
                receipt.retryable
                or receipt.outcome is W4QuestionCoreReceiptOutcome.RETRYABLE_INFRA_FAILURE
            ):
                retry_scheduled += 1
                continue
            if receipt.outcome is W4QuestionCoreReceiptOutcome.APPLIED:
                applied += 1
            elif receipt.outcome is W4QuestionCoreReceiptOutcome.DUPLICATE:
                duplicate += 1
            else:
                terminal_rejected += 1
            if self._delete(delivery.receipt_handle):
                acknowledged += 1
            else:
                retry_scheduled += 1
        return W4QuestionCoreDecisionWorkerResult(
            received=len(deliveries),
            acknowledged=acknowledged,
            retry_scheduled=retry_scheduled,
            applied=applied,
            duplicate=duplicate,
            terminal_rejected=terminal_rejected,
        )

    def _authenticate(self, *, sender_id: str | None) -> AuthenticatedDeliveryContext | None:
        if sender_id is None or sender_id.split(":", maxsplit=1)[0] != self._expected_sender_id:
            return None
        return AuthenticatedDeliveryContext(
            sender_id=sender_id,
            logical_principal=self._expected_sender_id,
        )

    def _delete(self, receipt_handle: str) -> bool:
        try:
            self._sqs.delete_message(queue_url=self._queue_url, receipt_handle=receipt_handle)
        except SqsDeliveryError:
            return False
        return True
