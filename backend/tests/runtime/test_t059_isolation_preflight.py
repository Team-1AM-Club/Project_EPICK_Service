from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from app.runtime.t059_isolation_preflight import (
    T059IsolationPreflightError,
    build_t059_isolation_preflight_config,
    verify_t059_isolation,
)


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


class StubSqsClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_queue_attributes(
        self, *, QueueUrl: str, AttributeNames: list[str]
    ) -> dict[str, object]:
        self.calls.append({"QueueUrl": QueueUrl, "AttributeNames": AttributeNames})
        return {"Attributes": {"QueueArn": "arn:aws:sqs:ap-northeast-2:123456789012:t059"}}


def _config():
    return build_t059_isolation_preflight_config(
        worker_database_url="postgresql+psycopg://worker:password@postgres/epick_t059_runtime",
        execution_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123456789012/epick-t059-execution",
        command_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123456789012/epick-t059-w2-command",
        run_id="t059-runtime-01",
        aws_region="ap-northeast-2",
    )


def test_preflight_rejects_missing_settings() -> None:
    with pytest.raises(T059IsolationPreflightError) as error:
        build_t059_isolation_preflight_config(
            worker_database_url=None,
            execution_queue_url="",
            command_queue_url=None,
            run_id="",
            aws_region=None,
        )

    assert str(error.value) == (
        "missing required isolated-runtime settings: T059_WORKER_DATABASE_URL, "
        "T059_EXECUTION_QUEUE_URL, T059_W2_COMMAND_QUEUE_URL, T059_RUN_ID, AWS_DEFAULT_REGION"
    )


@pytest.mark.parametrize(
    ("database_url", "message"),
    [
        (
            "postgresql+psycopg://worker:password@postgres/epick_staging",
            "must target a disposable database",
        ),
        ("not a URL", "is not a valid SQLAlchemy URL"),
    ],
)
def test_preflight_rejects_shared_or_invalid_database_urls(database_url: str, message: str) -> None:
    with pytest.raises(T059IsolationPreflightError, match=message):
        build_t059_isolation_preflight_config(
            worker_database_url=database_url,
            execution_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123456789012/epick-t059-execution",
            command_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123456789012/epick-t059-w2-command",
            run_id="t059-runtime-01",
            aws_region="ap-northeast-2",
        )


@pytest.mark.parametrize(
    ("execution_queue_url", "command_queue_url", "run_id", "message"),
    [
        (
            "https://sqs.ap-northeast-2.amazonaws.com/123456789012/epick-execution",
            "https://sqs.ap-northeast-2.amazonaws.com/123456789012/epick-t059-w2-command",
            "t059-runtime-01",
            "T059_EXECUTION_QUEUE_URL must name a dedicated queue containing t059",
        ),
        (
            "https://sqs.ap-northeast-2.amazonaws.com/123456789012/epick-t059-execution",
            "http://queue.example/t059-command",
            "t059-runtime-01",
            "T059_W2_COMMAND_QUEUE_URL must be an HTTPS queue URL",
        ),
        (
            "https://sqs.ap-northeast-2.amazonaws.com/123456789012/epick-t059-execution",
            "https://sqs.ap-northeast-2.amazonaws.com/123456789012/epick-t059-w2-command",
            "runtime-01",
            "T059_RUN_ID must match",
        ),
    ],
)
def test_preflight_rejects_non_isolated_queues_or_run_ids(
    execution_queue_url: str, command_queue_url: str, run_id: str, message: str
) -> None:
    with pytest.raises(T059IsolationPreflightError, match=message):
        build_t059_isolation_preflight_config(
            worker_database_url="postgresql+psycopg://worker:password@postgres/epick_t059_runtime",
            execution_queue_url=execution_queue_url,
            command_queue_url=command_queue_url,
            run_id=run_id,
            aws_region="ap-northeast-2",
        )


def test_preflight_checks_only_the_isolated_execution_queue_without_messages() -> None:
    sqs_client = StubSqsClient()
    session_factory = StubSessionFactory()

    result = verify_t059_isolation(
        config=_config(),
        session_factory=session_factory,
        sqs_client=sqs_client,
    )

    assert result.as_safe_dict() == {
        "status": "ok",
        "database": "isolated_connected",
        "execution_queue": "isolated_attributes_accessible",
        "command_queue": "isolated_configured_not_sent",
        "run_id": "t059-runtime-01",
    }
    assert sqs_client.calls == [
        {
            "QueueUrl": "https://sqs.ap-northeast-2.amazonaws.com/123456789012/epick-t059-execution",
            "AttributeNames": ["QueueArn"],
        }
    ]
    assert session_factory.session.statements == ["SELECT id FROM users LIMIT 1 FOR UPDATE"]
