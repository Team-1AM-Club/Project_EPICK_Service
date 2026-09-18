from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.runtime.sqs import SqsPort


class W3CoreDecisionPreflightError(RuntimeError):
    """Safe operator-facing failure before the private consumer starts."""


class SessionFactory(Protocol):
    def begin(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class W3CoreDecisionPreflightConfig:
    queue_url: str
    dlq_url: str
    expected_producer: str
    expected_sender_id: str


@dataclass(frozen=True, slots=True)
class W3CoreDecisionPreflightResult:
    def as_safe_dict(self) -> dict[str, str]:
        return {
            "status": "ok",
            "database": "worker_read_accessible",
            "queue": "attributes_accessible",
            "dlq": "attributes_accessible_and_bound",
            "principal": "configured",
        }


def build_w3_core_decision_preflight_config(
    *,
    queue_url: str | None,
    dlq_url: str | None,
    expected_producer: str | None,
    expected_sender_id: str | None,
) -> W3CoreDecisionPreflightConfig:
    values = {
        "W3_CORE_DECISION_QUEUE_URL": queue_url,
        "W3_CORE_DECISION_DLQ_URL": dlq_url,
        "W3_CORE_DECISION_EXPECTED_PRODUCER": expected_producer,
        "W3_CORE_DECISION_EXPECTED_SENDER_ID": expected_sender_id,
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise W3CoreDecisionPreflightError(
            f"missing required W3 runtime settings: {', '.join(missing)}"
        )
    assert queue_url is not None
    assert dlq_url is not None
    assert expected_producer is not None
    assert expected_sender_id is not None
    _require_queue_url("W3_CORE_DECISION_QUEUE_URL", queue_url)
    _require_queue_url("W3_CORE_DECISION_DLQ_URL", dlq_url)
    if queue_url == dlq_url:
        raise W3CoreDecisionPreflightError("W3 queue and DLQ URLs must be distinct")
    if expected_producer != "w3":
        raise W3CoreDecisionPreflightError("W3_CORE_DECISION_EXPECTED_PRODUCER must be w3")
    if ":" in expected_sender_id:
        raise W3CoreDecisionPreflightError(
            "W3_CORE_DECISION_EXPECTED_SENDER_ID must be the stable principal id without session"
        )
    return W3CoreDecisionPreflightConfig(
        queue_url=queue_url,
        dlq_url=dlq_url,
        expected_producer=expected_producer,
        expected_sender_id=expected_sender_id,
    )


def verify_w3_core_decision_runtime(
    *,
    config: W3CoreDecisionPreflightConfig,
    session_factory: SessionFactory,
    sqs: SqsPort,
) -> W3CoreDecisionPreflightResult:
    try:
        with session_factory.begin() as session:
            session.execute(text("SELECT id FROM users LIMIT 1"))
            session.execute(text("SELECT id FROM jobs LIMIT 1"))
            session.execute(text("SELECT event_id FROM inbox_receipts LIMIT 1"))
    except SQLAlchemyError as error:
        raise W3CoreDecisionPreflightError("worker database read check failed") from error

    try:
        queue_attributes = sqs.get_queue_attributes(
            queue_url=config.queue_url,
            attribute_names=("QueueArn", "RedrivePolicy"),
        )
        dlq_attributes = sqs.get_queue_attributes(
            queue_url=config.dlq_url,
            attribute_names=("QueueArn",),
        )
    except Exception as error:
        raise W3CoreDecisionPreflightError("queue or DLQ attributes check failed") from error

    queue_arn = queue_attributes.get("QueueArn")
    dlq_arn = dlq_attributes.get("QueueArn")
    raw_redrive = queue_attributes.get("RedrivePolicy")
    if not queue_arn or not dlq_arn or not raw_redrive:
        raise W3CoreDecisionPreflightError("queue or DLQ attributes response is incomplete")
    try:
        redrive = json.loads(raw_redrive)
        max_receive_count = int(redrive["maxReceiveCount"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise W3CoreDecisionPreflightError("queue RedrivePolicy is invalid") from error
    if redrive.get("deadLetterTargetArn") != dlq_arn or max_receive_count < 1:
        raise W3CoreDecisionPreflightError("queue RedrivePolicy does not target the configured DLQ")
    return W3CoreDecisionPreflightResult()


def _require_queue_url(name: str, value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or not parsed.path.rsplit("/", 1)[-1]:
        raise W3CoreDecisionPreflightError(f"{name} must be an HTTPS queue URL")
