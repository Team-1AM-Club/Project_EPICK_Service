from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.runtime.sqs import InMemorySqsPort, SqsRetryableError
from app.runtime.w4_question_core_decision import (
    W4QuestionCorePrincipalError,
    W4QuestionCoreReceipt,
    W4QuestionCoreReceiptOutcome,
    verify_w4_question_core_principal,
)
from app.runtime.w4_question_core_decision_worker import W4QuestionCoreDecisionWorker

QUEUE_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/w4-question-core-decision"
EXPECTED_SENDER_ID = "AROAW4PRODUCER"


def test_body_producer_value_is_not_transport_authentication() -> None:
    with pytest.raises(W4QuestionCorePrincipalError):
        verify_w4_question_core_principal(
            authenticated_principal="forged-w4-body",
            expected_principal="w4-runtime-sender",
        )


def test_w4_principal_requires_the_configured_transport_identity() -> None:
    verify_w4_question_core_principal(
        authenticated_principal="w4-runtime-sender",
        expected_principal="w4-runtime-sender",
    )

    with pytest.raises(W4QuestionCorePrincipalError):
        verify_w4_question_core_principal(
            authenticated_principal="w4-runtime-sender",
            expected_principal="",
        )


def _event() -> dict[str, object]:
    return {
        "schema_version": "w4.private.question-core-decision/0.1-candidate",
        "message_type": "w4.private.w1.question-core-decision",
        "message_id": str(uuid4()),
        "decision_id": str(uuid4()),
        "occurred_at": "2026-09-19T00:00:00Z",
        "visibility_scope": "PRIVATE",
        "producer": "w4",
        "job_id": str(uuid4()),
        "company_id": None,
        "question_version_id": str(uuid4()),
        "source_id": str(uuid4()),
        "analysis_input_version": "question-input:runtime",
        "decision_scope": "QUESTION_MATCHING",
        "decision_owner": "W4",
        "decision_version": 1,
        "is_core": True,
        "decision_code": "CORE_REQUIRED",
        "reason_code": "QUESTION_EVIDENCE_REQUIRED",
    }


class FakeSessionFactory:
    @contextmanager
    def begin(self) -> object:
        yield object()


class StubService:
    def __init__(self, *receipts: W4QuestionCoreReceipt, error: Exception | None = None) -> None:
        self._receipts = list(receipts)
        self._error = error
        self.calls = 0

    def apply(
        self,
        *,
        body: str,
        authenticated_principal: str,
        expected_principal: str,
    ) -> W4QuestionCoreReceipt:
        assert body
        assert authenticated_principal == EXPECTED_SENDER_ID
        assert expected_principal == EXPECTED_SENDER_ID
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._receipts
        return self._receipts.pop(0)


class FailFirstDeleteSqs(InMemorySqsPort):
    def __init__(self) -> None:
        super().__init__()
        self._delete_failed = False

    def delete_message(self, *, queue_url: str, receipt_handle: str) -> None:
        if not self._delete_failed:
            self._delete_failed = True
            raise SqsRetryableError("SQS_DELETE_RETRYABLE")
        super().delete_message(queue_url=queue_url, receipt_handle=receipt_handle)


def _receipt(outcome: W4QuestionCoreReceiptOutcome) -> W4QuestionCoreReceipt:
    return W4QuestionCoreReceipt(
        message_id=UUID(str(_event()["message_id"])),
        payload_digest="sha256:" + "a" * 64,
        outcome=outcome,
        retryable=outcome is W4QuestionCoreReceiptOutcome.RETRYABLE_INFRA_FAILURE,
        error_code=None,
        decision_id=None,
        received_at=datetime.now(UTC),
    )


def _worker(*, sqs: InMemorySqsPort, service: StubService) -> W4QuestionCoreDecisionWorker:
    return W4QuestionCoreDecisionWorker(
        session_factory=FakeSessionFactory(),
        sqs=sqs,
        queue_url=QUEUE_URL,
        expected_sender_id=EXPECTED_SENDER_ID,
        batch_size=10,
        wait_time_seconds=20,
        service_factory=lambda _: service,
    )


@pytest.mark.parametrize(
    "outcome",
    (
        W4QuestionCoreReceiptOutcome.APPLIED,
        W4QuestionCoreReceiptOutcome.DUPLICATE,
        W4QuestionCoreReceiptOutcome.STALE_DISCARDED,
        W4QuestionCoreReceiptOutcome.REJECTED_BINDING,
        W4QuestionCoreReceiptOutcome.REJECTED_CONFLICT,
    ),
)
def test_w4_terminal_durable_outcomes_are_deleted_only_after_commit(
    outcome: W4QuestionCoreReceiptOutcome,
) -> None:
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
    assert result.duplicate == int(outcome is W4QuestionCoreReceiptOutcome.DUPLICATE)


def test_w4_infrastructure_failure_is_retained_for_redelivery() -> None:
    sqs = InMemorySqsPort()
    message_id = sqs.inject_message(
        queue_url=QUEUE_URL,
        body=json.dumps(_event()),
        sender_id=f"{EXPECTED_SENDER_ID}:relay-session",
    )
    worker = _worker(sqs=sqs, service=StubService(error=SQLAlchemyError("db unavailable")))

    assert worker.drain_once().retry_scheduled == 1
    sqs.redeliver_all()
    assert worker.drain_once().retry_scheduled == 1
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


def test_w4_commit_before_delete_redelivery_is_deduplicated_then_acknowledged() -> None:
    sqs = FailFirstDeleteSqs()
    sqs.inject_message(
        queue_url=QUEUE_URL,
        body=json.dumps(_event()),
        sender_id=f"{EXPECTED_SENDER_ID}:relay-session",
    )
    service = StubService(
        _receipt(W4QuestionCoreReceiptOutcome.APPLIED),
        _receipt(W4QuestionCoreReceiptOutcome.DUPLICATE),
    )
    worker = _worker(sqs=sqs, service=service)

    first = worker.drain_once()
    sqs.redeliver_all()
    second = worker.drain_once()

    assert first.received == 1 and first.acknowledged == 0 and first.retry_scheduled == 1
    assert second.received == 1 and second.acknowledged == 1 and second.duplicate == 1
    assert service.calls == 2
    assert len(sqs.deleted_receipt_handles) == 1


def test_w4_untrusted_sender_is_terminally_acknowledged_without_domain_call() -> None:
    sqs = InMemorySqsPort()
    sqs.inject_message(queue_url=QUEUE_URL, body=json.dumps(_event()), sender_id="forged-sender")

    result = _worker(
        sqs=sqs,
        service=StubService(error=AssertionError("untrusted sender must not reach the domain")),
    ).drain_once()

    assert result.received == result.acknowledged == result.terminal_rejected == 1
    assert result.retry_scheduled == 0


def test_w4_retryable_receipt_is_retained_for_queue_redrive_and_dlq_policy() -> None:
    sqs = InMemorySqsPort()
    sqs.inject_message(
        queue_url=QUEUE_URL,
        body=json.dumps(_event()),
        sender_id=f"{EXPECTED_SENDER_ID}:relay-session",
    )
    worker = _worker(
        sqs=sqs,
        service=StubService(
            *(_receipt(W4QuestionCoreReceiptOutcome.RETRYABLE_INFRA_FAILURE) for _ in range(5))
        ),
    )

    for _ in range(5):
        result = worker.drain_once()
        assert result.retry_scheduled == 1
        assert result.acknowledged == 0
        sqs.redeliver_all()

    assert not sqs.deleted_receipt_handles


def test_w4_worker_receives_at_most_ten_messages_per_long_poll() -> None:
    sqs = InMemorySqsPort()
    for _ in range(11):
        sqs.inject_message(
            queue_url=QUEUE_URL,
            body=json.dumps(_event()),
            sender_id=f"{EXPECTED_SENDER_ID}:relay-session",
        )

    result = _worker(
        sqs=sqs,
        service=StubService(*(_receipt(W4QuestionCoreReceiptOutcome.APPLIED) for _ in range(10))),
    ).drain_once()

    assert result.received == result.acknowledged == result.applied == 10
