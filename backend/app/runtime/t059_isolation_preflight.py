"""Fail closed before a T059 runtime probe can touch shared staging resources."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError


class T059IsolationPreflightError(RuntimeError):
    """A safe, operator-facing failure before a synthetic T059 run starts."""


class T059SessionFactory(Protocol):
    def begin(self) -> Any: ...


class T059SqsAttributesClient(Protocol):
    def get_queue_attributes(
        self, *, QueueUrl: str, AttributeNames: list[str]
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class T059IsolationPreflightConfig:
    worker_database_url: str
    execution_queue_url: str
    command_queue_url: str
    run_id: str
    aws_region: str


@dataclass(frozen=True)
class T059IsolationPreflightResult:
    run_id: str

    def as_safe_dict(self) -> dict[str, str]:
        """Expose statuses only; never echo a DB URL, queue URL, or credential."""

        return {
            "status": "ok",
            "database": "isolated_connected",
            "execution_queue": "isolated_attributes_accessible",
            "command_queue": "isolated_configured_not_sent",
            "run_id": self.run_id,
        }


_RUN_ID_PATTERN = re.compile(r"t059-[a-z0-9][a-z0-9-]{2,48}\Z")
_REQUIRED_DATABASE_PREFIX = "epick_t059_"


def build_t059_isolation_preflight_config(
    *,
    worker_database_url: str | None,
    execution_queue_url: str | None,
    command_queue_url: str | None,
    run_id: str | None,
    aws_region: str | None,
) -> T059IsolationPreflightConfig:
    """Validate that a probe is explicitly pointed at disposable resources.

    The normal staging worker values are intentionally not accepted here.  A
    future T059 scenario runner must obtain this configuration first, so a
    copied production/staging ``worker.env`` fails before any broker action.
    """

    values = {
        "T059_WORKER_DATABASE_URL": worker_database_url,
        "T059_EXECUTION_QUEUE_URL": execution_queue_url,
        "T059_W2_COMMAND_QUEUE_URL": command_queue_url,
        "T059_RUN_ID": run_id,
        "AWS_DEFAULT_REGION": aws_region,
    }
    missing = [
        name for name, value in values.items() if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        raise T059IsolationPreflightError(
            "missing required isolated-runtime settings: " + ", ".join(missing)
        )

    assert worker_database_url is not None
    assert execution_queue_url is not None
    assert command_queue_url is not None
    assert run_id is not None
    assert aws_region is not None

    _require_isolated_database_url(worker_database_url)
    _require_isolated_queue_url("T059_EXECUTION_QUEUE_URL", execution_queue_url)
    _require_isolated_queue_url("T059_W2_COMMAND_QUEUE_URL", command_queue_url)
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise T059IsolationPreflightError(
            "T059_RUN_ID must match t059- followed by 3-49 lowercase letters, digits, or hyphens"
        )

    return T059IsolationPreflightConfig(
        worker_database_url=worker_database_url,
        execution_queue_url=execution_queue_url,
        command_queue_url=command_queue_url,
        run_id=run_id,
        aws_region=aws_region,
    )


def verify_t059_isolation(
    *,
    config: T059IsolationPreflightConfig,
    session_factory: T059SessionFactory,
    sqs_client: T059SqsAttributesClient,
) -> T059IsolationPreflightResult:
    """Check a disposable DB and execution queue without sending or receiving messages."""

    try:
        with session_factory.begin() as session:
            # JobWorker protects its account-status/deletion-epoch check with a
            # PostgreSQL row lock.  Exercising the same lock shape here catches
            # an incomplete worker privilege manifest before the synthetic
            # runner seeds rows or publishes any SQS message.
            session.execute(text("SELECT id FROM users LIMIT 1 FOR UPDATE"))
    except SQLAlchemyError as error:
        raise T059IsolationPreflightError(
            "isolated worker database connectivity check failed"
        ) from error

    try:
        response = sqs_client.get_queue_attributes(
            QueueUrl=config.execution_queue_url,
            AttributeNames=["QueueArn"],
        )
    except Exception as error:
        raise T059IsolationPreflightError(
            "isolated execution queue attributes check failed"
        ) from error
    attributes = response.get("Attributes")
    if not isinstance(attributes, dict) or not isinstance(attributes.get("QueueArn"), str):
        raise T059IsolationPreflightError(
            "isolated execution queue returned an invalid attributes response"
        )

    return T059IsolationPreflightResult(run_id=config.run_id)


def _require_isolated_database_url(value: str) -> None:
    try:
        database_name = make_url(value).database
    except ArgumentError as error:
        raise T059IsolationPreflightError(
            "T059_WORKER_DATABASE_URL is not a valid SQLAlchemy URL"
        ) from error
    if not isinstance(database_name, str) or not database_name.startswith(
        _REQUIRED_DATABASE_PREFIX
    ):
        raise T059IsolationPreflightError(
            "T059_WORKER_DATABASE_URL must target a disposable database named epick_t059_*"
        )


def _require_isolated_queue_url(name: str, value: str) -> None:
    parsed = urlparse(value)
    queue_name = parsed.path.rsplit("/", maxsplit=1)[-1].lower()
    if parsed.scheme != "https" or not parsed.netloc or not queue_name:
        raise T059IsolationPreflightError(f"{name} must be an HTTPS queue URL")
    if "t059" not in queue_name:
        raise T059IsolationPreflightError(f"{name} must name a dedicated queue containing t059")
