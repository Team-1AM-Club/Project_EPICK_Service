from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from app.runtime.t060_isolation_preflight import (
    T060IsolationPreflightError,
    build_t060_isolation_preflight_config,
    verify_t060_isolation,
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
        return {"Attributes": {"QueueArn": f"arn:aws:sqs:ap-northeast-2:123:{QueueUrl}"}}


def _config():
    return build_t060_isolation_preflight_config(
        worker_database_url="postgresql+psycopg://worker:password@postgres/epick_t060_runtime",
        execution_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/epick-t060-execution",
        command_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/epick-t060-command",
        result_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/epick-t060-result",
        run_id="t060-runtime-01",
        aws_region="ap-northeast-2",
    )


def test_preflight_rejects_missing_settings() -> None:
    with pytest.raises(T060IsolationPreflightError) as error:
        build_t060_isolation_preflight_config(
            worker_database_url=None,
            execution_queue_url=None,
            command_queue_url=None,
            result_queue_url=None,
            run_id=None,
            aws_region=None,
        )

    assert str(error.value) == (
        "missing required isolated-runtime settings: T060_WORKER_DATABASE_URL, "
        "T060_EXECUTION_QUEUE_URL, T060_W2_COMMAND_QUEUE_URL, T060_W2_RESULT_QUEUE_URL, "
        "T060_RUN_ID, AWS_DEFAULT_REGION"
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
def test_preflight_rejects_shared_or_invalid_database_urls(
    database_url: str, message: str
) -> None:
    with pytest.raises(T060IsolationPreflightError, match=message):
        build_t060_isolation_preflight_config(
            worker_database_url=database_url,
            execution_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/t060-execution",
            command_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/t060-command",
            result_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/t060-result",
            run_id="t060-runtime-01",
            aws_region="ap-northeast-2",
        )


def test_preflight_rejects_shared_duplicate_or_non_t060_queues() -> None:
    with pytest.raises(T060IsolationPreflightError, match="containing t060"):
        build_t060_isolation_preflight_config(
            worker_database_url="postgresql+psycopg://worker:p@postgres/epick_t060_runtime",
            execution_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/shared-execution",
            command_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/t060-command",
            result_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/t060-result",
            run_id="t060-runtime-01",
            aws_region="ap-northeast-2",
        )

    duplicate = "https://sqs.ap-northeast-2.amazonaws.com/123/t060-same"
    with pytest.raises(T060IsolationPreflightError, match="three distinct queues"):
        build_t060_isolation_preflight_config(
            worker_database_url="postgresql+psycopg://worker:p@postgres/epick_t060_runtime",
            execution_queue_url=duplicate,
            command_queue_url=duplicate,
            result_queue_url="https://sqs.ap-northeast-2.amazonaws.com/123/t060-result",
            run_id="t060-runtime-01",
            aws_region="ap-northeast-2",
        )


def test_preflight_checks_database_locks_and_all_three_queues_without_messages() -> None:
    factory = StubSessionFactory()
    sqs = StubSqsClient()

    result = verify_t060_isolation(
        config=_config(),
        session_factory=factory,
        sqs_client=sqs,
    )

    assert result.as_safe_dict() == {
        "status": "ok",
        "database": "isolated_connected",
        "execution_queue": "isolated_attributes_accessible",
        "command_queue": "isolated_configured_not_sent",
        "result_queue": "isolated_attributes_accessible",
        "run_id": "t060-runtime-01",
    }
    assert factory.session.statements == [
        "SELECT id FROM users LIMIT 1 FOR UPDATE",
        "SELECT event_id FROM inbox_receipts LIMIT 1 FOR UPDATE",
    ]
    assert [call["QueueUrl"].rsplit("/", 1)[-1] for call in sqs.calls] == [
        "epick-t060-execution",
        "epick-t060-result",
    ]
    assert all(call["AttributeNames"] == ["QueueArn"] for call in sqs.calls)
