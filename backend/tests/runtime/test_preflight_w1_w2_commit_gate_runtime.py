from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from app.runtime.w2_commit_gate_preflight import (
    W2CommitGatePreflightError,
    build_w2_commit_gate_preflight_config,
    verify_w2_commit_gate_runtime,
)

QUEUE_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/epick-staging-w2-commit-gate"
DLQ_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/epick-staging-w2-commit-gate-dlq"
LEGACY_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/epick-staging-w2-result"
QUEUE_ARN = "arn:aws:sqs:ap-northeast-2:123:epick-staging-w2-commit-gate"
DLQ_ARN = "arn:aws:sqs:ap-northeast-2:123:epick-staging-w2-commit-gate-dlq"


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
            "QueueArn": QUEUE_ARN,
            "RedrivePolicy": json.dumps(
                {"deadLetterTargetArn": self.target_arn, "maxReceiveCount": "5"}
            ),
        }


def _config():
    return build_w2_commit_gate_preflight_config(
        queue_url=QUEUE_URL,
        dlq_url=DLQ_URL,
        legacy_result_queue_url=LEGACY_URL,
        expected_producer="w2",
        expected_sender_id="AROAW2PRODUCER",
    )


def test_preflight_is_mutation_free_and_checks_dedicated_redrive_binding() -> None:
    factory = StubSessionFactory()
    sqs = StubSqs()

    result = verify_w2_commit_gate_runtime(
        config=_config(),
        session_factory=factory,
        sqs=sqs,  # type: ignore[arg-type]
    )

    assert result.as_safe_dict() == {
        "status": "ok",
        "database": "worker_read_accessible",
        "queue": "attributes_accessible",
        "dlq": "attributes_accessible_and_bound",
        "legacy_route": "distinct",
        "principal": "configured",
    }
    assert factory.session.statements == [
        "SELECT id FROM users LIMIT 1",
        "SELECT id FROM jobs LIMIT 1",
        "SELECT id FROM job_commands LIMIT 1",
        "SELECT id FROM w2_commit_operations LIMIT 1",
        "SELECT id FROM w2_staged_results LIMIT 1",
        "SELECT event_id FROM inbox_receipts LIMIT 1",
    ]
    assert sqs.calls == [
        (QUEUE_URL, ("QueueArn", "RedrivePolicy")),
        (DLQ_URL, ("QueueArn",)),
    ]


def test_preflight_rejects_an_incorrect_dlq_target() -> None:
    with pytest.raises(W2CommitGatePreflightError, match="does not target"):
        verify_w2_commit_gate_runtime(
            config=_config(),
            session_factory=StubSessionFactory(),
            sqs=StubSqs(target_arn="arn:aws:sqs:ap-northeast-2:123:wrong-dlq"),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"queue_url": None}, "missing required"),
        ({"dlq_url": QUEUE_URL}, "must be distinct"),
        ({"legacy_result_queue_url": QUEUE_URL}, "legacy result queue"),
        ({"expected_producer": "body-value"}, "must be w2"),
        ({"expected_sender_id": "AROA:session"}, "without session"),
    ],
)
def test_preflight_rejects_unsafe_or_incomplete_configuration(
    overrides: dict[str, str | None], message: str
) -> None:
    values: dict[str, str | None] = {
        "queue_url": QUEUE_URL,
        "dlq_url": DLQ_URL,
        "legacy_result_queue_url": LEGACY_URL,
        "expected_producer": "w2",
        "expected_sender_id": "AROAW2PRODUCER",
    }
    values.update(overrides)
    with pytest.raises(W2CommitGatePreflightError, match=message):
        build_w2_commit_gate_preflight_config(**values)
