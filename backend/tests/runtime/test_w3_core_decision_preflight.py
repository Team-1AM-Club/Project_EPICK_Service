from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from app.runtime.w3_core_decision_preflight import (
    W3CoreDecisionPreflightError,
    build_w3_core_decision_preflight_config,
    verify_w3_core_decision_runtime,
)

QUEUE_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/epick-staging-w3-decision"
DLQ_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/epick-staging-w3-decision-dlq"
DLQ_ARN = "arn:aws:sqs:ap-northeast-2:123:epick-staging-w3-decision-dlq"


class StubSession:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: object) -> None:
        self.statements.append(str(statement))


class StubSessionFactory:
    def __init__(self) -> None:
        self.session = StubSession()

    @contextmanager
    def begin(self):
        yield self.session


class StubSqs:
    def __init__(self, *, target_arn: str = DLQ_ARN) -> None:
        self.target_arn = target_arn
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def get_queue_attributes(
        self, *, queue_url: str, attribute_names: tuple[str, ...]
    ) -> dict[str, str]:
        self.calls.append((queue_url, attribute_names))
        if queue_url == DLQ_URL:
            return {"QueueArn": DLQ_ARN}
        return {
            "QueueArn": "arn:aws:sqs:ap-northeast-2:123:epick-staging-w3-decision",
            "RedrivePolicy": json.dumps(
                {"deadLetterTargetArn": self.target_arn, "maxReceiveCount": "5"}
            ),
        }


def _config():
    return build_w3_core_decision_preflight_config(
        queue_url=QUEUE_URL,
        dlq_url=DLQ_URL,
        expected_producer="w3",
        expected_sender_id="AROAW3PRODUCER",
    )


def test_preflight_is_mutation_free_and_verifies_the_redrive_binding() -> None:
    factory = StubSessionFactory()
    sqs = StubSqs()

    result = verify_w3_core_decision_runtime(
        config=_config(),
        session_factory=factory,
        sqs=sqs,
    )

    assert result.as_safe_dict() == {
        "status": "ok",
        "database": "worker_read_accessible",
        "queue": "attributes_accessible",
        "dlq": "attributes_accessible_and_bound",
        "principal": "configured",
    }
    assert factory.session.statements == [
        "SELECT id FROM users LIMIT 1",
        "SELECT id FROM jobs LIMIT 1",
        "SELECT event_id FROM inbox_receipts LIMIT 1",
    ]
    assert sqs.calls == [
        (QUEUE_URL, ("QueueArn", "RedrivePolicy")),
        (DLQ_URL, ("QueueArn",)),
    ]


def test_preflight_rejects_a_different_dlq_target() -> None:
    with pytest.raises(W3CoreDecisionPreflightError, match="does not target"):
        verify_w3_core_decision_runtime(
            config=_config(),
            session_factory=StubSessionFactory(),
            sqs=StubSqs(target_arn="arn:aws:sqs:ap-northeast-2:123:wrong-dlq"),
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"queue_url": None}, "missing required"),
        ({"dlq_url": QUEUE_URL}, "must be distinct"),
        ({"expected_producer": "body-value"}, "must be w3"),
        ({"expected_sender_id": "AROA:session"}, "without session"),
    ],
)
def test_preflight_rejects_unsafe_configuration(
    overrides: dict[str, str | None], message: str
) -> None:
    values: dict[str, str | None] = {
        "queue_url": QUEUE_URL,
        "dlq_url": DLQ_URL,
        "expected_producer": "w3",
        "expected_sender_id": "AROAW3PRODUCER",
    }
    values.update(overrides)
    with pytest.raises(W3CoreDecisionPreflightError, match=message):
        build_w3_core_decision_preflight_config(**values)
