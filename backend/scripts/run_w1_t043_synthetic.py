"""Run the W1-owned T043 Core Decision scenario on disposable PostgreSQL and SQS.

The runner is intentionally gated and refuses the staging database or shared queues.  It uses
an assumed, send-only synthetic W3 role for the producer side while the default EC2 identity
continues to consume as W1.  No production user content or credential value is printed.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, UUID, uuid5

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import boto3  # noqa: E402
from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.engine import URL, make_url  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.models.application_workspace import Company  # noqa: E402
from app.models.deletion import DeletionRequest  # noqa: F401, E402
from app.models.identity import User  # noqa: E402
from app.models.jobs import (  # noqa: E402
    InboxReceipt,
    Job,
    JobCommand,
    JobCoreDecisionBinding,
    JobRequiredAction,
    OutboxMessage,
)
from app.models.sources import AnalysisSourceDecision, JobSourceLink, Source  # noqa: E402
from app.runtime.core_decision_binding import parse_w3_core_decision_event  # noqa: E402
from app.runtime.sqs import Boto3SqsPort, ReceivedSqsMessage  # noqa: E402
from app.runtime.w3_core_decision_preflight import (  # noqa: E402
    W3CoreDecisionPreflightError,
    build_w3_core_decision_preflight_config,
    verify_w3_core_decision_runtime,
)
from app.runtime.w3_core_decision_worker import (  # noqa: E402
    W3CoreDecisionWorker,
    W3CoreDecisionWorkerResult,
)


class T043ScenarioError(RuntimeError):
    """Safe operator-facing reason that the isolated scenario refused to run."""


@dataclass(frozen=True, slots=True)
class T043Configuration:
    seed_database_url: str
    worker_database_url: str
    database_name: str
    queue_url: str
    dlq_url: str
    expected_producer: str
    expected_sender_id: str
    sender_role_arn: str
    run_id: str
    aws_region: str


@dataclass(frozen=True, slots=True)
class SeededDecision:
    owner_id: UUID
    job_id: UUID
    company_id: UUID
    source_id: UUID
    analysis_input_version: str


@dataclass(frozen=True, slots=True)
class RowCounts:
    receipts: int
    decisions: int
    bindings: int
    commands: int
    outbox: int


class RecordingSqsPort:
    """Record real deliveries while preserving the production adapter behavior."""

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.last_deliveries: list[ReceivedSqsMessage] = []

    def receive_messages(self, **kwargs: Any) -> list[ReceivedSqsMessage]:
        self.last_deliveries = list(self.delegate.receive_messages(**kwargs))
        return self.last_deliveries

    def delete_message(self, **kwargs: Any) -> None:
        self.delegate.delete_message(**kwargs)

    def change_message_visibility(self, **kwargs: Any) -> None:
        self.delegate.change_message_visibility(**kwargs)

    def get_queue_attributes(self, **kwargs: Any) -> dict[str, str]:
        return self.delegate.get_queue_attributes(**kwargs)


class _InjectedDatabaseFailureFactory:
    @contextmanager
    def begin(self):
        raise SQLAlchemyError("T043_INJECTED_DATABASE_FAILURE")
        yield  # pragma: no cover


def _environment(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value is not None else None


def _required_environment(name: str) -> str:
    value = _environment(name)
    if not value:
        raise T043ScenarioError(f"{name} must be set")
    return value


def _database_identity(value: str) -> tuple[str | None, int | None, str | None]:
    url: URL = make_url(value)
    return url.host, url.port, url.database


def _queue_name(value: str) -> str:
    parsed = urlparse(value)
    return parsed.path.rsplit("/", maxsplit=1)[-1]


def _require_configuration() -> T043Configuration:
    if _environment("T043_EXECUTE_SYNTHETIC") != "YES":
        raise T043ScenarioError("set T043_EXECUTE_SYNTHETIC=YES to permit a synthetic T043 run")

    seed_database_url = _required_environment("T043_SEED_DATABASE_URL")
    worker_database_url = _required_environment("WORKER_DATABASE_URL")
    seed_identity = _database_identity(seed_database_url)
    worker_identity = _database_identity(worker_database_url)
    if seed_identity != worker_identity:
        raise T043ScenarioError(
            "T043_SEED_DATABASE_URL and WORKER_DATABASE_URL must target the same database"
        )
    database_name = worker_identity[2] or ""
    if "t043" not in database_name.lower():
        raise T043ScenarioError("T043 requires a dedicated database containing t043 in its name")

    queue_url = _required_environment("W3_CORE_DECISION_QUEUE_URL")
    dlq_url = _required_environment("W3_CORE_DECISION_DLQ_URL")
    for value in (queue_url, dlq_url):
        if "t043" not in _queue_name(value).lower():
            raise T043ScenarioError(
                "T043 requires each dedicated queue containing t043 in its name"
            )
    if queue_url == dlq_url:
        raise T043ScenarioError("T043 main queue and DLQ must be distinct")

    expected_producer = _required_environment("W3_CORE_DECISION_EXPECTED_PRODUCER")
    if expected_producer != "w3":
        raise T043ScenarioError("W3_CORE_DECISION_EXPECTED_PRODUCER must be w3")
    expected_sender_id = _required_environment("W3_CORE_DECISION_EXPECTED_SENDER_ID")
    if ":" in expected_sender_id:
        raise T043ScenarioError("T043 expected sender must be a stable role id without session")

    sender_role_arn = _required_environment("T043_SENDER_ROLE_ARN")
    if not re.fullmatch(r"arn:aws:iam::\d{12}:role/[A-Za-z0-9+=,.@_/-]+", sender_role_arn):
        raise T043ScenarioError("T043_SENDER_ROLE_ARN must be an IAM role ARN")
    run_id = _required_environment("T043_RUN_ID")
    if "t043" not in run_id.lower():
        raise T043ScenarioError("T043_RUN_ID must contain t043")
    aws_region = _required_environment("AWS_DEFAULT_REGION")

    build_w3_core_decision_preflight_config(
        queue_url=queue_url,
        dlq_url=dlq_url,
        expected_producer=expected_producer,
        expected_sender_id=expected_sender_id,
    )
    return T043Configuration(
        seed_database_url=seed_database_url,
        worker_database_url=worker_database_url,
        database_name=database_name,
        queue_url=queue_url,
        dlq_url=dlq_url,
        expected_producer=expected_producer,
        expected_sender_id=expected_sender_id,
        sender_role_arn=sender_role_arn,
        run_id=run_id,
        aws_region=aws_region,
    )


def _queue_attributes(*, sqs_client: Any, queue_url: str) -> dict[str, str]:
    response = sqs_client.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
            "ApproximateNumberOfMessagesDelayed",
            "QueueArn",
            "RedrivePolicy",
        ],
    )
    attributes = response.get("Attributes")
    if not isinstance(attributes, dict):
        raise T043ScenarioError("T043 queue returned an invalid attributes response")
    return {
        str(name): str(value)
        for name, value in attributes.items()
        if isinstance(name, str) and isinstance(value, str)
    }


def _queue_depth(*, sqs_client: Any, queue_url: str) -> int:
    attributes = _queue_attributes(sqs_client=sqs_client, queue_url=queue_url)
    try:
        return sum(
            int(attributes.get(name, "0"))
            for name in (
                "ApproximateNumberOfMessages",
                "ApproximateNumberOfMessagesNotVisible",
                "ApproximateNumberOfMessagesDelayed",
            )
        )
    except ValueError as error:
        raise T043ScenarioError("T043 queue depth attributes are invalid") from error


def _max_receive_count(*, sqs_client: Any, queue_url: str) -> int:
    attributes = _queue_attributes(sqs_client=sqs_client, queue_url=queue_url)
    try:
        redrive = json.loads(attributes["RedrivePolicy"])
        value = int(redrive["maxReceiveCount"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise T043ScenarioError("T043 main queue RedrivePolicy is invalid") from error
    if value < 1:
        raise T043ScenarioError("T043 maxReceiveCount must be positive")
    return value


def _assert_empty_environment(
    *,
    session_factory: sessionmaker[Session],
    sqs_client: Any,
    config: T043Configuration,
) -> None:
    with session_factory.begin() as session:
        counts = {
            model.__tablename__: session.scalar(select(func.count()).select_from(model))
            for model in (
                User,
                InboxReceipt,
                AnalysisSourceDecision,
                JobCoreDecisionBinding,
                JobCommand,
                OutboxMessage,
            )
        }
    populated = [name for name, count in counts.items() if count]
    if populated:
        raise T043ScenarioError(
            "T043 fixture database is not empty: " + ", ".join(sorted(populated))
        )
    if _queue_depth(sqs_client=sqs_client, queue_url=config.queue_url) != 0:
        raise T043ScenarioError("T043 main queue is not empty")
    if _queue_depth(sqs_client=sqs_client, queue_url=config.dlq_url) != 0:
        raise T043ScenarioError("T043 DLQ is not empty")


def _seed_waiting_job(*, session: Session, run_id: str, scenario: str) -> SeededDecision:
    owner = User(
        display_name=f"T043 synthetic {run_id} {scenario}",
        locale="ko-KR",
        timezone="Asia/Seoul",
    )
    company = Company(
        legal_name=f"EPICK T043 {run_id} {scenario}",
        display_name=f"EPICK T043 {scenario}",
    )
    session.add_all([owner, company])
    session.flush()
    source = Source(
        company_id=company.id,
        source_type="CAREERS",
        canonical_url=f"https://t043.invalid/{run_id}/{scenario}",
        canonical_url_hash=f"t043-{uuid5(NAMESPACE_URL, f'{run_id}:{scenario}:source')}",
        url_normalization_version="v1",
        policy_version="t043-isolated",
        policy_checked_at=datetime.now(UTC),
    )
    analysis_input_version = f"t043-{scenario}-v1"
    job = Job(
        owner_user_id=owner.id,
        job_type="SOURCE_COLLECTION",
        status="WAITING_USER",
        dispatch_status="BLOCKED",
        owner_deletion_epoch=0,
        analysis_input_version=analysis_input_version,
    )
    session.add_all([source, job])
    session.flush()
    session.add(
        JobSourceLink(
            job_id=job.id,
            owner_user_id=owner.id,
            source_id=source.id,
            source_version_id=None,
            command_id=None,
            purpose_ref="SOURCE_COLLECTION",
            analysis_input_version=analysis_input_version,
        )
    )
    session.add(
        JobRequiredAction(
            job_id=job.id,
            owner_user_id=owner.id,
            action_code="CORE_DECISION_REQUIRED",
            action_status="OPEN",
            context_code="CORE_DECISION_BINDING_MISMATCH",
            expected_input_version=analysis_input_version,
        )
    )
    session.flush()
    return SeededDecision(
        owner_id=owner.id,
        job_id=job.id,
        company_id=company.id,
        source_id=source.id,
        analysis_input_version=analysis_input_version,
    )


def _core_event(*, seed: SeededDecision, run_id: str, scenario: str) -> dict[str, object]:
    event = {
        "schema_version": "w3.private.core-decision/0.1-candidate",
        "message_type": "w3.private.w1.core-decision",
        "message_id": str(uuid5(NAMESPACE_URL, f"{run_id}:{scenario}:message")),
        "occurred_at": "2026-09-18T00:00:00Z",
        "visibility_scope": "PRIVATE",
        "producer": "w3",
        "job_id": str(seed.job_id),
        "company_id": str(seed.company_id),
        "source_id": str(seed.source_id),
        "analysis_input_version": seed.analysis_input_version,
        "decision_scope": "COMPANY_KNOWLEDGE",
        "decision_owner": "W3",
        "question_version_id": None,
        "decision_version": 1,
        "is_core": True,
        "decision_code": "CORE_REQUIRED",
        "reason_code": "T043_SYNTHETIC_CORE",
    }
    parse_w3_core_decision_event(event)
    return event


def _assumed_sender_client(*, config: T043Configuration, sts_client: Any) -> Any:
    session_name = re.sub(r"[^A-Za-z0-9+=,.@-]", "-", config.run_id)[:64]
    response = sts_client.assume_role(
        RoleArn=config.sender_role_arn,
        RoleSessionName=session_name,
        DurationSeconds=900,
    )
    credentials = response.get("Credentials")
    if not isinstance(credentials, dict):
        raise T043ScenarioError("T043 sender role did not return temporary credentials")
    required = ("AccessKeyId", "SecretAccessKey", "SessionToken")
    if not all(isinstance(credentials.get(name), str) for name in required):
        raise T043ScenarioError("T043 sender role credentials response is incomplete")
    return boto3.client(
        "sqs",
        region_name=config.aws_region,
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )


def _send(*, sender: Boto3SqsPort, queue_url: str, body: object) -> None:
    sender.send_message(
        queue_url=queue_url,
        body=json.dumps(body, separators=(",", ":")),
        message_attributes={},
    )


def _worker(
    *,
    session_factory: Any,
    sqs: RecordingSqsPort,
    config: T043Configuration,
) -> W3CoreDecisionWorker:
    return W3CoreDecisionWorker(
        session_factory=session_factory,
        sqs=sqs,
        queue_url=config.queue_url,
        expected_sender_id=config.expected_sender_id,
        expected_producer=config.expected_producer,
        batch_size=1,
        visibility_timeout_seconds=120,
        wait_time_seconds=2,
    )


def _drain_delivery(
    *, worker: W3CoreDecisionWorker, attempts: int = 6
) -> W3CoreDecisionWorkerResult:
    for _ in range(attempts):
        result = worker.drain_once()
        if result.received:
            return result
        time.sleep(0.5)
    raise T043ScenarioError("T043 did not receive the expected synthetic delivery")


def _require_result(
    result: W3CoreDecisionWorkerResult,
    *,
    acknowledged: int,
    retry_scheduled: int,
    applied: int = 0,
    duplicate: int = 0,
    terminal_rejected: int = 0,
    context: str,
) -> None:
    expected = W3CoreDecisionWorkerResult(
        received=1,
        acknowledged=acknowledged,
        retry_scheduled=retry_scheduled,
        applied=applied,
        duplicate=duplicate,
        terminal_rejected=terminal_rejected,
    )
    if result != expected:
        raise T043ScenarioError(f"{context}: expected={asdict(expected)}, actual={asdict(result)}")


def _release_last_delivery(*, sqs: RecordingSqsPort, queue_url: str) -> None:
    if len(sqs.last_deliveries) != 1:
        raise T043ScenarioError("T043 retry probe did not retain exactly one receipt handle")
    sqs.change_message_visibility(
        queue_url=queue_url,
        receipt_handle=sqs.last_deliveries[0].receipt_handle,
        visibility_timeout_seconds=0,
    )


def _wait_for_dlq(*, sqs_client: Any, dlq_url: str, timeout_seconds: int = 45) -> int:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        depth = _queue_depth(sqs_client=sqs_client, queue_url=dlq_url)
        if depth >= 1:
            return depth
        time.sleep(1)
    raise T043ScenarioError("T043 retryable delivery did not appear in the configured DLQ")


def _row_counts(*, session_factory: sessionmaker[Session]) -> RowCounts:
    with session_factory.begin() as session:
        return RowCounts(
            receipts=session.scalar(select(func.count()).select_from(InboxReceipt)) or 0,
            decisions=(
                session.scalar(select(func.count()).select_from(AnalysisSourceDecision)) or 0
            ),
            bindings=(
                session.scalar(select(func.count()).select_from(JobCoreDecisionBinding)) or 0
            ),
            commands=session.scalar(select(func.count()).select_from(JobCommand)) or 0,
            outbox=session.scalar(select(func.count()).select_from(OutboxMessage)) or 0,
        )


def run() -> dict[str, object]:
    config = _require_configuration()
    seed_engine = create_engine(config.seed_database_url, pool_pre_ping=True)
    worker_engine = create_engine(config.worker_database_url, pool_pre_ping=True)
    seed_factory = sessionmaker(bind=seed_engine, autoflush=False, expire_on_commit=False)
    worker_factory = sessionmaker(bind=worker_engine, autoflush=False, expire_on_commit=False)
    receiver_client = boto3.client("sqs", region_name=config.aws_region)
    receiver = RecordingSqsPort(Boto3SqsPort(client=receiver_client))
    sts_client = boto3.client("sts", region_name=config.aws_region)
    try:
        verify_w3_core_decision_runtime(
            config=build_w3_core_decision_preflight_config(
                queue_url=config.queue_url,
                dlq_url=config.dlq_url,
                expected_producer=config.expected_producer,
                expected_sender_id=config.expected_sender_id,
            ),
            session_factory=worker_factory,
            sqs=receiver,
        )
        _assert_empty_environment(
            session_factory=seed_factory,
            sqs_client=receiver_client,
            config=config,
        )
        max_receive_count = _max_receive_count(
            sqs_client=receiver_client,
            queue_url=config.queue_url,
        )
        sender = Boto3SqsPort(client=_assumed_sender_client(config=config, sts_client=sts_client))
        with seed_factory.begin() as session:
            apply_seed = _seed_waiting_job(session=session, run_id=config.run_id, scenario="apply")
            redelivery_seed = _seed_waiting_job(
                session=session, run_id=config.run_id, scenario="redelivery"
            )
            dlq_seed = _seed_waiting_job(session=session, run_id=config.run_id, scenario="dlq")

        apply_event = _core_event(seed=apply_seed, run_id=config.run_id, scenario="apply")
        redelivery_event = _core_event(
            seed=redelivery_seed,
            run_id=config.run_id,
            scenario="redelivery",
        )
        dlq_event = _core_event(seed=dlq_seed, run_id=config.run_id, scenario="dlq")
        real_worker = _worker(session_factory=worker_factory, sqs=receiver, config=config)

        _send(sender=sender, queue_url=config.queue_url, body=apply_event)
        _require_result(
            _drain_delivery(worker=real_worker),
            acknowledged=1,
            retry_scheduled=0,
            applied=1,
            context="T043 valid Core apply failed",
        )
        _send(sender=sender, queue_url=config.queue_url, body=apply_event)
        _require_result(
            _drain_delivery(worker=real_worker),
            acknowledged=1,
            retry_scheduled=0,
            duplicate=1,
            context="T043 exact duplicate failed",
        )

        _send(sender=sender, queue_url=config.queue_url, body={"invalid": "t043"})
        _require_result(
            _drain_delivery(worker=real_worker),
            acknowledged=1,
            retry_scheduled=0,
            terminal_rejected=1,
            context="T043 terminal schema rejection failed",
        )

        failing_worker = _worker(
            session_factory=_InjectedDatabaseFailureFactory(),
            sqs=receiver,
            config=config,
        )
        _send(sender=sender, queue_url=config.queue_url, body=redelivery_event)
        _require_result(
            _drain_delivery(worker=failing_worker),
            acknowledged=0,
            retry_scheduled=1,
            context="T043 injected database failure was not retained",
        )
        _release_last_delivery(sqs=receiver, queue_url=config.queue_url)
        _require_result(
            _drain_delivery(worker=real_worker),
            acknowledged=1,
            retry_scheduled=0,
            applied=1,
            context="T043 redelivery was not applied after database recovery",
        )

        _send(sender=sender, queue_url=config.queue_url, body=dlq_event)
        dlq_depth = 0
        for _ in range(max_receive_count + 3):
            result = failing_worker.drain_once()
            if result.received:
                _require_result(
                    result,
                    acknowledged=0,
                    retry_scheduled=1,
                    context="T043 DLQ retry was not retained",
                )
                _release_last_delivery(sqs=receiver, queue_url=config.queue_url)
            dlq_depth = _queue_depth(sqs_client=receiver_client, queue_url=config.dlq_url)
            if dlq_depth:
                break
            time.sleep(0.5)
        if not dlq_depth:
            dlq_depth = _wait_for_dlq(sqs_client=receiver_client, dlq_url=config.dlq_url)

        counts = _row_counts(session_factory=seed_factory)
        expected_counts = RowCounts(
            receipts=2,
            decisions=2,
            bindings=2,
            commands=0,
            outbox=0,
        )
        if counts != expected_counts:
            raise T043ScenarioError(
                f"T043 final row counts differ: expected={asdict(expected_counts)}, "
                f"actual={asdict(counts)}"
            )
    finally:
        seed_engine.dispose()
        worker_engine.dispose()

    return {
        "status": "ok",
        "run_id": config.run_id,
        "database": config.database_name,
        "terminal": "invalid_schema_acknowledged",
        "duplicate": "applied_once_then_duplicate",
        "redelivery": "database_failure_then_applied",
        "dlq": f"retryable_failure_redriven_max_receive_{max_receive_count}",
        "dlq_depth": dlq_depth,
        "rows": asdict(counts),
        "automatic_commands": 0,
        "automatic_outbox": 0,
    }


def main() -> None:
    print(json.dumps(run(), separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (T043ScenarioError, W3CoreDecisionPreflightError) as error:
        raise SystemExit(str(error)) from error
