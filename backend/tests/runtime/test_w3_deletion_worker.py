from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.runtime.sqs import InMemorySqsPort, SqsRetryableError
from app.runtime.w3_deletion_worker import (
    W3RetentionApplyResult,
    W3RetentionReceiptContractError,
    W3RetentionReceiptWorker,
    parse_w3_retention_receipt,
)

QUEUE_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/w3-retention-receipt"
EXPECTED_SENDER_ID = "AROAW3RETENTION"


def _receipt(*, operation: str = "DELETE_OWNER", outcome: str = "APPLIED") -> dict[str, object]:
    occurred_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "schema_version": "w3.private.w1-lifecycle-receipt/1.0",
        "message_type": "w3.private.w1.lifecycle-receipt",
        "receipt_id": str(uuid4()),
        "occurred_at": occurred_at,
        "visibility_scope": "PRIVATE",
        "producer": "w3",
        "command_id": str(uuid4()),
        "target_ref": str(uuid4()),
        "operation": operation,
        "outcome": outcome,
        "affected_count": 1 if outcome == "APPLIED" else 0,
        "applied_epoch": 2 if operation == "DELETE_OWNER" else None,
        "effective_at": None if operation == "DELETE_OWNER" else occurred_at,
    }


class FakeSessionFactory:
    @contextmanager
    def begin(self):
        yield object()


class StubService:
    def __init__(
        self,
        *results: W3RetentionApplyResult,
        error: Exception | None = None,
    ) -> None:
        self.results = list(results)
        self.error = error
        self.calls = 0

    def apply(self, *, body: str) -> W3RetentionApplyResult:
        assert body
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.results.pop(0)


class FailFirstDeleteSqs(InMemorySqsPort):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def delete_message(self, *, queue_url: str, receipt_handle: str) -> None:
        if not self.failed:
            self.failed = True
            raise SqsRetryableError("SQS_DELETE_RETRYABLE")
        super().delete_message(queue_url=queue_url, receipt_handle=receipt_handle)


def _result(
    *,
    operation: str = "DELETE_OWNER",
    outcome: str = "APPLIED",
    duplicate: bool = False,
) -> W3RetentionApplyResult:
    return W3RetentionApplyResult(
        command_id=uuid4(),
        operation=operation,
        outcome=outcome,
        duplicate_receipt=duplicate,
    )


def _worker(*, sqs: InMemorySqsPort, service: StubService) -> W3RetentionReceiptWorker:
    return W3RetentionReceiptWorker(
        session_factory=FakeSessionFactory(),
        sqs=sqs,
        queue_url=QUEUE_URL,
        expected_sender_id=EXPECTED_SENDER_ID,
        service_factory=lambda _: service,
    )


def test_receipt_contract_requires_operation_specific_target_and_aware_time() -> None:
    valid = _receipt()
    assert parse_w3_retention_receipt(json.dumps(valid)).operation == "DELETE_OWNER"

    with pytest.raises(W3RetentionReceiptContractError):
        parse_w3_retention_receipt(json.dumps(valid | {"target_ref": None}))
    with pytest.raises(W3RetentionReceiptContractError):
        parse_w3_retention_receipt(json.dumps(valid | {"occurred_at": "2026-09-20T00:00:00"}))


@pytest.mark.parametrize("outcome", ["APPLIED", "DUPLICATE", "STALE"])
def test_authenticated_terminal_receipts_commit_then_delete(outcome: str) -> None:
    sqs = InMemorySqsPort()
    sqs.inject_message(
        queue_url=QUEUE_URL,
        body=json.dumps(_receipt(outcome=outcome)),
        sender_id=f"{EXPECTED_SENDER_ID}:session",
    )

    result = _worker(sqs=sqs, service=StubService(_result(outcome=outcome))).drain_once()

    assert result.received == result.acknowledged == 1
    assert result.retry_scheduled == result.terminal_rejected == 0
    assert len(sqs.deleted_receipt_handles) == 1


def test_conflict_is_not_a_valid_w3_receipt_outcome() -> None:
    with pytest.raises(W3RetentionReceiptContractError):
        parse_w3_retention_receipt(json.dumps(_receipt(outcome="CONFLICT")))


def test_untrusted_sender_is_deleted_without_domain_call() -> None:
    sqs = InMemorySqsPort()
    sqs.inject_message(queue_url=QUEUE_URL, body=json.dumps(_receipt()), sender_id="forged")
    service = StubService(error=AssertionError("must not be called"))

    result = _worker(sqs=sqs, service=service).drain_once()

    assert result.terminal_rejected == result.acknowledged == 1
    assert service.calls == 0


def test_database_failure_is_retained_for_redelivery() -> None:
    sqs = InMemorySqsPort()
    sqs.inject_message(
        queue_url=QUEUE_URL,
        body=json.dumps(_receipt()),
        sender_id=f"{EXPECTED_SENDER_ID}:session",
    )
    worker = _worker(sqs=sqs, service=StubService(error=SQLAlchemyError("db unavailable")))

    assert worker.drain_once().retry_scheduled == 1
    assert not sqs.deleted_receipt_handles


def test_commit_before_sqs_delete_redelivers_stored_result_as_duplicate() -> None:
    sqs = FailFirstDeleteSqs()
    sqs.inject_message(
        queue_url=QUEUE_URL,
        body=json.dumps(_receipt()),
        sender_id=f"{EXPECTED_SENDER_ID}:session",
    )
    service = StubService(_result(), _result(duplicate=True))
    worker = _worker(sqs=sqs, service=service)

    first = worker.drain_once()
    sqs.redeliver_all()
    second = worker.drain_once()

    assert first.acknowledged == 0 and first.retry_scheduled == 1
    assert second.acknowledged == second.duplicate == 1
    assert service.calls == 2
