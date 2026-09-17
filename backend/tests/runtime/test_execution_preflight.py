from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from app.runtime.execution_preflight import (
    ExecutionRuntimePreflightError,
    build_execution_runtime_preflight_config,
    verify_execution_runtime,
)


class StubSession:
    def execute(self, statement: object) -> None:
        del statement


class StubSessionFactory:
    @contextmanager
    def begin(self):
        yield StubSession()


class StubSqsClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_queue_attributes(
        self, *, QueueUrl: str, AttributeNames: list[str]
    ) -> dict[str, object]:
        self.calls.append({"QueueUrl": QueueUrl, "AttributeNames": AttributeNames})
        return {"Attributes": {"QueueArn": "arn:aws:sqs:ap-northeast-2:123456789012:execution"}}


def _config():
    return build_execution_runtime_preflight_config(
        execution_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123456789012/w1-execution",
        command_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123456789012/w2-command",
        worker_id="synthetic-worker-01",
        relay_instance_id="synthetic-relay-01",
        aws_region="ap-northeast-2",
    )


def test_preflight_requires_every_execution_runtime_setting() -> None:
    with pytest.raises(ExecutionRuntimePreflightError) as error:
        build_execution_runtime_preflight_config(
            execution_queue_url=None,
            command_queue_url="",
            worker_id=None,
            relay_instance_id="",
            aws_region=None,
        )

    assert str(error.value) == (
        "missing required runtime settings: W1_JOB_EXECUTION_QUEUE_URL, "
        "W2_COLLECTION_COMMAND_QUEUE_URL, W1_WORKER_ID, "
        "W1_OUTBOX_RELAY_INSTANCE_ID, AWS_DEFAULT_REGION"
    )


def test_preflight_rejects_non_https_queue_urls() -> None:
    with pytest.raises(ExecutionRuntimePreflightError, match="W1_JOB_EXECUTION_QUEUE_URL"):
        build_execution_runtime_preflight_config(
            execution_queue_url="http://queue.example/execution",
            command_queue_url="https://queue.example/command",
            worker_id="worker",
            relay_instance_id="relay",
            aws_region="ap-northeast-2",
        )


def test_preflight_checks_worker_db_and_execution_queue_without_sending_messages() -> None:
    sqs_client = StubSqsClient()

    result = verify_execution_runtime(
        config=_config(),
        session_factory=StubSessionFactory(),
        sqs_client=sqs_client,
    )

    assert result.as_safe_dict() == {
        "status": "ok",
        "database": "connected",
        "execution_queue": "attributes_accessible",
        "command_queue": "configured_not_sent",
        "worker_identity": "configured",
    }
    assert sqs_client.calls == [
        {
            "QueueUrl": "https://sqs.ap-northeast-2.amazonaws.com/123456789012/w1-execution",
            "AttributeNames": ["QueueArn"],
        }
    ]
