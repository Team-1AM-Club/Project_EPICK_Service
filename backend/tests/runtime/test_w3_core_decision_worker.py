from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.runtime.core_decision_binding import (
    CoreDecisionReceipt,
    CoreDecisionReceiptOutcome,
)
from app.runtime.sqs import InMemorySqsPort
from app.runtime.w3_core_decision_worker import W3CoreDecisionWorker

QUEUE_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/w3-core-decision"
EXPECTED_SENDER_ID = "AROAW3PRODUCER"


def _event() -> dict[str, object]:
    return {
        "schema_version": "w3.private.core-decision/0.1-candidate",
        "message_type": "w3.private.w1.core-decision",
        "message_id": str(uuid4()),
        "occurred_at": "2026-09-18T00:00:00Z",
        "visibility_scope": "PRIVATE",
        "producer": "w3",
        "job_id": str(uuid4()),
        "company_id": str(uuid4()),
        "source_id": str(uuid4()),
        "analysis_input_version": "knowledge-input:runtime",
        "decision_scope": "COMPANY_KNOWLEDGE",
        "decision_owner": "W3",
        "question_version_id": None,
        "decision_version": 1,
        "is_core": True,
        "decision_code": "CORE_REQUIRED",
        "reason_code": "REQUIRED_COMPANY_EVIDENCE",
    }


class FakeSessionFactory:
    @contextmanager
    def begin(self):
        yield object()


class StubService:
    def __init__(self, receipt: CoreDecisionReceipt | None = None, error: Exception | None = None):
        self.receipt = receipt
        self.error = error

    def apply(self, *, body: str, authenticated_principal: str) -> CoreDecisionReceipt:
        assert body
        assert authenticated_principal == "w3"
        if self.error is not None:
            raise self.error
        assert self.receipt is not None
        return self.receipt


def _receipt(outcome: CoreDecisionReceiptOutcome) -> CoreDecisionReceipt:
    return CoreDecisionReceipt(
        message_id=UUID(str(_event()["message_id"])),
        payload_digest="sha256:" + "a" * 64,
        outcome=outcome,
        retryable=outcome is CoreDecisionReceiptOutcome.RETRYABLE_INFRA_FAILURE,
        error_code=None,
        decision_id=None,
        received_at=datetime.now(UTC),
    )


def _worker(
    *, sqs: InMemorySqsPort, service: StubService, batch_size: int = 10
) -> W3CoreDecisionWorker:
    return W3CoreDecisionWorker(
        session_factory=FakeSessionFactory(),
        sqs=sqs,
        queue_url=QUEUE_URL,
        expected_sender_id=EXPECTED_SENDER_ID,
        batch_size=batch_size,
        wait_time_seconds=20,
        service_factory=lambda _: service,
    )


@pytest.mark.parametrize(
    "outcome",
    [
        CoreDecisionReceiptOutcome.APPLIED,
        CoreDecisionReceiptOutcome.DUPLICATE,
        CoreDecisionReceiptOutcome.STALE_DISCARDED,
        CoreDecisionReceiptOutcome.REJECTED_BINDING,
        CoreDecisionReceiptOutcome.REJECTED_CONFLICT,
    ],
)
def test_terminal_durable_outcomes_are_acknowledged(outcome: CoreDecisionReceiptOutcome) -> None:
    sqs = InMemorySqsPort()
    sqs.inject_message(
        queue_url=QUEUE_URL,
        body=json.dumps(_event()),
        sender_id=f"{EXPECTED_SENDER_ID}:relay-session",
    )

    result = _worker(sqs=sqs, service=StubService(_receipt(outcome))).drain_once()

    assert result.received == 1
    assert result.acknowledged == 1
    assert result.retry_scheduled == 0
    assert len(sqs.deleted_receipt_handles) == 1
    assert result.duplicate == int(outcome is CoreDecisionReceiptOutcome.DUPLICATE)


def test_sqs_sender_id_is_checked_before_the_body_is_trusted() -> None:
    sqs = InMemorySqsPort()
    sqs.inject_message(queue_url=QUEUE_URL, body="{}", sender_id="untrusted-sender")

    result = _worker(
        sqs=sqs,
        service=StubService(error=AssertionError("service must not be called")),
    ).drain_once()

    assert result.terminal_rejected == 1
    assert result.acknowledged == 1


@pytest.mark.parametrize("body", ["not-json", "x" * (16 * 1024 + 1)])
def test_invalid_or_oversized_body_is_terminal_and_deleted(body: str) -> None:
    sqs = InMemorySqsPort()
    sqs.inject_message(
        queue_url=QUEUE_URL,
        body=body,
        sender_id=f"{EXPECTED_SENDER_ID}:relay-session",
    )

    result = _worker(
        sqs=sqs,
        service=StubService(error=AssertionError("service must not be called")),
    ).drain_once()

    assert result.terminal_rejected == 1
    assert result.acknowledged == 1


def test_database_infrastructure_failure_is_not_deleted_and_is_redelivered() -> None:
    sqs = InMemorySqsPort()
    message_id = sqs.inject_message(
        queue_url=QUEUE_URL,
        body=json.dumps(_event()),
        sender_id=f"{EXPECTED_SENDER_ID}:relay-session",
    )
    worker = _worker(sqs=sqs, service=StubService(error=SQLAlchemyError("db unavailable")))

    first = worker.drain_once()
    sqs.redeliver_all()
    second = worker.drain_once()

    assert first.retry_scheduled == second.retry_scheduled == 1
    assert not sqs.deleted_receipt_handles
    sqs.redeliver_all()
    redelivery = sqs.receive_messages(
        queue_url=QUEUE_URL,
        max_messages=1,
        visibility_timeout_seconds=120,
        wait_time_seconds=20,
    )[0]
    assert redelivery.message_id == message_id
    assert redelivery.receive_count == 3


def test_retryable_receipt_is_retained_for_queue_redrive_and_dlq_policy() -> None:
    sqs = InMemorySqsPort()
    sqs.inject_message(
        queue_url=QUEUE_URL,
        body=json.dumps(_event()),
        sender_id=f"{EXPECTED_SENDER_ID}:relay-session",
    )
    worker = _worker(
        sqs=sqs,
        service=StubService(_receipt(CoreDecisionReceiptOutcome.RETRYABLE_INFRA_FAILURE)),
    )

    for _ in range(5):
        result = worker.drain_once()
        assert result.retry_scheduled == 1
        sqs.redeliver_all()

    assert not sqs.deleted_receipt_handles


def test_worker_receives_at_most_ten_messages_per_long_poll() -> None:
    sqs = InMemorySqsPort()
    for _ in range(11):
        sqs.inject_message(
            queue_url=QUEUE_URL,
            body=json.dumps(_event()),
            sender_id=f"{EXPECTED_SENDER_ID}:relay-session",
        )

    result = _worker(
        sqs=sqs,
        service=StubService(_receipt(CoreDecisionReceiptOutcome.APPLIED)),
    ).drain_once()

    assert result.received == 10
    assert result.acknowledged == 10
