"""Fail-closed, side-effect-free preflight for the W1 execution runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError


class ExecutionRuntimePreflightError(RuntimeError):
    """A safe configuration or connectivity failure for an operator-facing preflight."""


class SqsAttributesClient(Protocol):
    def get_queue_attributes(
        self, *, QueueUrl: str, AttributeNames: list[str]
    ) -> dict[str, Any]: ...


class WorkerSessionFactory(Protocol):
    def begin(self) -> Any: ...


@dataclass(frozen=True)
class ExecutionRuntimePreflightConfig:
    execution_queue_url: str
    command_queue_url: str
    worker_id: str
    relay_instance_id: str
    aws_region: str


@dataclass(frozen=True)
class ExecutionRuntimePreflightResult:
    database: str
    execution_queue: str
    command_queue: str
    worker_identity: str

    def as_safe_dict(self) -> dict[str, str]:
        """Return statuses only; URLs, database names, and credentials never reach stdout."""

        return {
            "status": "ok",
            "database": self.database,
            "execution_queue": self.execution_queue,
            "command_queue": self.command_queue,
            "worker_identity": self.worker_identity,
        }


def build_execution_runtime_preflight_config(
    *,
    execution_queue_url: str | None,
    command_queue_url: str | None,
    worker_id: str | None,
    relay_instance_id: str | None,
    aws_region: str | None,
) -> ExecutionRuntimePreflightConfig:
    """Validate the values required before relay/job workers can be started."""

    values = {
        "W1_JOB_EXECUTION_QUEUE_URL": execution_queue_url,
        "W2_COLLECTION_COMMAND_QUEUE_URL": command_queue_url,
        "W1_WORKER_ID": worker_id,
        "W1_OUTBOX_RELAY_INSTANCE_ID": relay_instance_id,
        "AWS_DEFAULT_REGION": aws_region,
    }
    missing = [
        name for name, value in values.items() if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        message = f"missing required runtime settings: {', '.join(missing)}"
        raise ExecutionRuntimePreflightError(message)

    assert execution_queue_url is not None
    assert command_queue_url is not None
    assert worker_id is not None
    assert relay_instance_id is not None
    assert aws_region is not None
    _require_https_queue_url("W1_JOB_EXECUTION_QUEUE_URL", execution_queue_url)
    _require_https_queue_url("W2_COLLECTION_COMMAND_QUEUE_URL", command_queue_url)
    return ExecutionRuntimePreflightConfig(
        execution_queue_url=execution_queue_url,
        command_queue_url=command_queue_url,
        worker_id=worker_id,
        relay_instance_id=relay_instance_id,
        aws_region=aws_region,
    )


def verify_execution_runtime(
    *,
    config: ExecutionRuntimePreflightConfig,
    session_factory: WorkerSessionFactory,
    sqs_client: SqsAttributesClient,
) -> ExecutionRuntimePreflightResult:
    """Check only dependencies that have no publish/consume side effect.

    W1's worker role is intentionally not required to read attributes from the W2 command queue.
    The command queue is therefore validated only as deployment configuration here; actual
    `SendMessage` evidence belongs to the synthetic T059 scenario.
    """

    try:
        with session_factory.begin() as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        raise ExecutionRuntimePreflightError("worker database connectivity check failed") from error

    try:
        response = sqs_client.get_queue_attributes(
            QueueUrl=config.execution_queue_url,
            AttributeNames=["QueueArn"],
        )
    except Exception as error:
        raise ExecutionRuntimePreflightError("execution queue attributes check failed") from error
    attributes = response.get("Attributes")
    if not isinstance(attributes, dict) or not isinstance(attributes.get("QueueArn"), str):
        raise ExecutionRuntimePreflightError(
            "execution queue returned an invalid attributes response"
        )

    return ExecutionRuntimePreflightResult(
        database="connected",
        execution_queue="attributes_accessible",
        command_queue="configured_not_sent",
        worker_identity="configured",
    )


def _require_https_queue_url(name: str, value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or not parsed.path.strip("/"):
        raise ExecutionRuntimePreflightError(f"{name} must be an HTTPS queue URL")
