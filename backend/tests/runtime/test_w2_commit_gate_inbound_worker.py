from __future__ import annotations

import pytest

from app.runtime.sqs import InMemorySqsPort, SqsRetryableError
from app.runtime.w2_commit_gate_worker import W2CommitGateInboundWorker


def test_inbound_worker_rejects_invalid_runtime_bounds() -> None:
    with pytest.raises(ValueError, match="expected_sender_id"):
        W2CommitGateInboundWorker(
            session_factory=lambda: None,  # type: ignore[arg-type]
            sqs=InMemorySqsPort(),
            queue_url="queue",
            expected_sender_id="",
        )
    with pytest.raises(ValueError, match="batch_size"):
        W2CommitGateInboundWorker(
            session_factory=lambda: None,  # type: ignore[arg-type]
            sqs=InMemorySqsPort(),
            queue_url="queue",
            expected_sender_id="sender",
            batch_size=11,
        )


def test_inbound_worker_treats_receive_transport_error_as_retryable_idle_cycle() -> None:
    class _RetryingSqs(InMemorySqsPort):
        def receive_messages(self, **_kwargs: object) -> list[object]:
            raise SqsRetryableError("SQS_TRANSPORT_FAILURE")

    worker = W2CommitGateInboundWorker(
        session_factory=lambda: None,  # type: ignore[arg-type]
        sqs=_RetryingSqs(),
        queue_url="queue",
        expected_sender_id="sender",
    )

    assert worker.drain_once().received == 0
