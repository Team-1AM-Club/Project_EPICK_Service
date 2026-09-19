"""Mutation-free readiness checks for W1's dedicated W4 Question Core consumer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.runtime.sqs import SqsPort


class W4QuestionCorePreflightError(RuntimeError):
    """Safe operator-facing failure before the private consumer starts."""


class SessionFactory(Protocol):
    def begin(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class W4QuestionCorePreflightConfig:
    queue_url: str
    dlq_url: str
    expected_producer: str
    expected_sender_id: str


@dataclass(frozen=True, slots=True)
class W4QuestionCorePreflightResult:
    def as_safe_dict(self) -> dict[str, str]:
        return {
            "status": "ok",
            "database": "worker_read_accessible",
            "queue": "attributes_accessible",
            "dlq": "attributes_accessible_and_bound",
            "principal": "configured",
        }


def build_w4_question_core_preflight_config(
    *,
    queue_url: str | None,
    dlq_url: str | None,
    expected_producer: str | None,
    expected_sender_id: str | None,
) -> W4QuestionCorePreflightConfig:
    values = {
        "W4_QUESTION_CORE_DECISION_QUEUE_URL": queue_url,
        "W4_QUESTION_CORE_DECISION_DLQ_URL": dlq_url,
        "W4_QUESTION_CORE_DECISION_EXPECTED_PRODUCER": expected_producer,
        "W4_QUESTION_CORE_DECISION_EXPECTED_SENDER_ID": expected_sender_id,
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise W4QuestionCorePreflightError(
            f"missing required W4 runtime settings: {', '.join(missing)}"
        )
    assert queue_url is not None
    assert dlq_url is not None
    assert expected_producer is not None
    assert expected_sender_id is not None
    _require_queue_url("W4_QUESTION_CORE_DECISION_QUEUE_URL", queue_url)
    _require_queue_url("W4_QUESTION_CORE_DECISION_DLQ_URL", dlq_url)
    if queue_url == dlq_url:
        raise W4QuestionCorePreflightError("W4 queue and DLQ URLs must be distinct")
    if expected_producer != "w4":
        raise W4QuestionCorePreflightError("W4_QUESTION_CORE_DECISION_EXPECTED_PRODUCER must be w4")
    if ":" in expected_sender_id:
        raise W4QuestionCorePreflightError(
            "W4_QUESTION_CORE_DECISION_EXPECTED_SENDER_ID must be the stable principal "
            "id without session"
        )
    return W4QuestionCorePreflightConfig(
        queue_url=queue_url,
        dlq_url=dlq_url,
        expected_producer=expected_producer,
        expected_sender_id=expected_sender_id,
    )


def verify_w4_question_core_runtime(
    *,
    config: W4QuestionCorePreflightConfig,
    session_factory: SessionFactory,
    sqs: SqsPort,
) -> W4QuestionCorePreflightResult:
    """Check read/row-lock access and Redrive binding without durable mutation."""

    try:
        with session_factory.begin() as session:
            for statement in (
                "SELECT id FROM users LIMIT 1 FOR UPDATE",
                "SELECT id FROM jobs LIMIT 1 FOR UPDATE",
                "SELECT event_id FROM inbox_receipts LIMIT 1 FOR UPDATE",
                "SELECT id FROM analysis_source_decisions LIMIT 1",
                "SELECT id FROM job_core_decision_bindings LIMIT 1",
                "SELECT id FROM application_projects LIMIT 1 FOR UPDATE",
                "SELECT id FROM application_project_versions LIMIT 1 FOR UPDATE",
                "SELECT id FROM project_questions LIMIT 1 FOR UPDATE",
                "SELECT id FROM question_versions LIMIT 1 FOR UPDATE",
                "SELECT id FROM job_source_links LIMIT 1 FOR UPDATE",
                "SELECT id FROM sources LIMIT 1 FOR UPDATE",
                "SELECT id FROM job_required_actions LIMIT 1 FOR UPDATE",
            ):
                session.execute(text(statement))
    except SQLAlchemyError as error:
        raise W4QuestionCorePreflightError("worker database read check failed") from error

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
        raise W4QuestionCorePreflightError("queue or DLQ attributes check failed") from error

    queue_arn = queue_attributes.get("QueueArn")
    dlq_arn = dlq_attributes.get("QueueArn")
    raw_redrive = queue_attributes.get("RedrivePolicy")
    if not queue_arn or not dlq_arn or not raw_redrive:
        raise W4QuestionCorePreflightError("queue or DLQ attributes response is incomplete")
    try:
        redrive = json.loads(raw_redrive)
        max_receive_count = int(redrive["maxReceiveCount"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise W4QuestionCorePreflightError("queue RedrivePolicy is invalid") from error
    if redrive.get("deadLetterTargetArn") != dlq_arn or max_receive_count < 1:
        raise W4QuestionCorePreflightError("queue RedrivePolicy does not target the configured DLQ")
    return W4QuestionCorePreflightResult()


def _require_queue_url(name: str, value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or not parsed.path.rsplit("/", 1)[-1]:
        raise W4QuestionCorePreflightError(f"{name} must be an HTTPS queue URL")
