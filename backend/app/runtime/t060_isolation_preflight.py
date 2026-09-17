"""Fail closed before a T060 recovery probe can touch shared resources."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError


class T060IsolationPreflightError(RuntimeError):
    """A safe, operator-facing failure before a synthetic T060 run starts."""


class T060SessionFactory(Protocol):
    def begin(self) -> Any: ...


class T060SqsAttributesClient(Protocol):
    def get_queue_attributes(
        self, *, QueueUrl: str, AttributeNames: list[str]
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class T060IsolationPreflightConfig:
    worker_database_url: str
    execution_queue_url: str
    command_queue_url: str
    result_queue_url: str
    run_id: str
    aws_region: str


@dataclass(frozen=True)
class T060IsolationPreflightResult:
    run_id: str

    def as_safe_dict(self) -> dict[str, str]:
        """Expose statuses only; never echo a DB URL, queue URL, or credential."""

        return {
            "status": "ok",
            "database": "isolated_connected",
            "execution_queue": "isolated_attributes_accessible",
            "command_queue": "isolated_configured_not_sent",
            "result_queue": "isolated_attributes_accessible",
            "run_id": self.run_id,
        }


_RUN_ID_PATTERN = re.compile(r"t060-[a-z0-9][a-z0-9-]{2,48}\Z")
_REQUIRED_DATABASE_PREFIX = "epick_t060_"


def build_t060_isolation_preflight_config(
    *,
    worker_database_url: str | None,
    execution_queue_url: str | None,
    command_queue_url: str | None,
    result_queue_url: str | None,
    run_id: str | None,
    aws_region: str | None,
) -> T060IsolationPreflightConfig:
    values = {
        "T060_WORKER_DATABASE_URL": worker_database_url,
        "T060_EXECUTION_QUEUE_URL": execution_queue_url,
        "T060_W2_COMMAND_QUEUE_URL": command_queue_url,
        "T060_W2_RESULT_QUEUE_URL": result_queue_url,
        "T060_RUN_ID": run_id,
        "AWS_DEFAULT_REGION": aws_region,
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise T060IsolationPreflightError(
            f"missing required isolated-runtime settings: {', '.join(missing)}"
        )
    assert worker_database_url is not None
    assert execution_queue_url is not None
    assert command_queue_url is not None
    assert result_queue_url is not None
    assert run_id is not None
    assert aws_region is not None

    _require_isolated_database_url(worker_database_url)
    queue_values = {
        "T060_EXECUTION_QUEUE_URL": execution_queue_url,
        "T060_W2_COMMAND_QUEUE_URL": command_queue_url,
        "T060_W2_RESULT_QUEUE_URL": result_queue_url,
    }
    for name, value in queue_values.items():
        _require_isolated_queue_url(name, value)
    if len(set(queue_values.values())) != len(queue_values):
        raise T060IsolationPreflightError("T060 queue URLs must identify three distinct queues")
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise T060IsolationPreflightError(
            "T060_RUN_ID must match t060- followed by 3-49 lowercase letters, digits, or hyphens"
        )

    return T060IsolationPreflightConfig(
        worker_database_url=worker_database_url,
        execution_queue_url=execution_queue_url,
        command_queue_url=command_queue_url,
        result_queue_url=result_queue_url,
        run_id=run_id,
        aws_region=aws_region,
    )


def verify_t060_isolation(
    *,
    config: T060IsolationPreflightConfig,
    session_factory: T060SessionFactory,
    sqs_client: T060SqsAttributesClient,
) -> T060IsolationPreflightResult:
    """Check the worker lock shape and all dedicated queues without mutating them."""

    try:
        with session_factory.begin() as session:
            session.execute(text("SELECT id FROM users LIMIT 1 FOR UPDATE"))
            session.execute(text("SELECT event_id FROM inbox_receipts LIMIT 1 FOR UPDATE"))
    except SQLAlchemyError as error:
        raise T060IsolationPreflightError(
            "isolated worker database connectivity or lock check failed"
        ) from error

    # The least-privilege worker role can SendMessage to the W2 command queue,
    # but intentionally cannot read its attributes.  The synthetic relay later
    # proves that send permission without broadening IAM for this preflight.
    for label, queue_url in (
        ("execution", config.execution_queue_url),
        ("result", config.result_queue_url),
    ):
        try:
            response = sqs_client.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=["QueueArn"],
            )
        except Exception as error:
            raise T060IsolationPreflightError(
                f"isolated {label} queue attributes check failed"
            ) from error
        attributes = response.get("Attributes")
        if not isinstance(attributes, dict) or not isinstance(attributes.get("QueueArn"), str):
            raise T060IsolationPreflightError(
                f"isolated {label} queue returned an invalid attributes response"
            )

    return T060IsolationPreflightResult(run_id=config.run_id)


def _require_isolated_database_url(value: str) -> None:
    try:
        database_name = make_url(value).database
    except ArgumentError as error:
        raise T060IsolationPreflightError(
            "T060_WORKER_DATABASE_URL is not a valid SQLAlchemy URL"
        ) from error
    if not isinstance(database_name, str) or not database_name.startswith(
        _REQUIRED_DATABASE_PREFIX
    ):
        raise T060IsolationPreflightError(
            "T060_WORKER_DATABASE_URL must target a disposable database named epick_t060_*"
        )


def _require_isolated_queue_url(name: str, value: str) -> None:
    parsed = urlparse(value)
    queue_name = parsed.path.rsplit("/", maxsplit=1)[-1].lower()
    if parsed.scheme != "https" or not parsed.netloc or not queue_name:
        raise T060IsolationPreflightError(f"{name} must be an HTTPS queue URL")
    if "t060" not in queue_name:
        raise T060IsolationPreflightError(f"{name} must name a dedicated queue containing t060")
