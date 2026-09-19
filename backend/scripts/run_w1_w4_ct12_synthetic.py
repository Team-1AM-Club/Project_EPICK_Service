"""Run W1-owned, isolated W4 Question Core transport evidence.

This is deliberately not a substitute for W4's pinned durable producer.  It
uses an approved isolated send-only role to exercise W1's receive/commit/ACK
boundary with synthetic data, then reports only non-secret aggregate facts.
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

from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import Settings  # noqa: E402
from app.models.application_workspace import (  # noqa: E402
    ApplicationProject,
    ApplicationProjectVersion,
    Company,
    ProjectQuestion,
    QuestionVersion,
)
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
from app.runtime.sqs import Boto3SqsPort, SqsPort, SqsRetryableError  # noqa: E402
from app.runtime.w4_question_core_decision import parse_w4_question_core_event  # noqa: E402
from app.runtime.w4_question_core_decision_worker import (  # noqa: E402
    W4QuestionCoreDecisionWorker,
    W4QuestionCoreDecisionWorkerResult,
)
from app.runtime.w4_question_core_preflight import (  # noqa: E402
    W4QuestionCorePreflightError,
    build_w4_question_core_preflight_config,
    verify_w4_question_core_runtime,
)
from app.services.jobs import JobService  # noqa: E402


class W1W4Ct12ScenarioError(RuntimeError):
    """A safe explanation for refusing an unsafe W1-only CT-12 run."""


@dataclass(frozen=True, slots=True)
class W1W4Ct12Configuration:
    seed_database_url: str
    worker_database_url: str
    database_name: str
    queue_url: str
    dlq_url: str
    expected_sender_id: str
    sender_role_arn: str
    run_id: str
    aws_region: str


@dataclass(frozen=True, slots=True)
class SeededQuestionCore:
    owner_id: UUID
    job_id: UUID
    source_id: UUID
    question_version_id: UUID
    analysis_input_version: str


@dataclass(frozen=True, slots=True)
class RowCounts:
    receipts: int
    decisions: int
    bindings: int
    commands: int
    outbox: int


class RecordingSqsPort:
    """Remember received handles so an intentional retry can be made visible."""

    def __init__(self, delegate: SqsPort) -> None:
        self.delegate = delegate
        self.last_deliveries: list[Any] = []

    def send_message(self, **kwargs: Any) -> str:
        return self.delegate.send_message(**kwargs)

    def receive_messages(self, **kwargs: Any) -> list[Any]:
        deliveries = self.delegate.receive_messages(**kwargs)
        self.last_deliveries = list(deliveries)
        return deliveries

    def delete_message(self, **kwargs: Any) -> None:
        self.delegate.delete_message(**kwargs)

    def change_message_visibility(self, **kwargs: Any) -> None:
        self.delegate.change_message_visibility(**kwargs)

    def get_queue_attributes(self, **kwargs: Any) -> dict[str, str]:
        return self.delegate.get_queue_attributes(**kwargs)


class FailFirstDeleteSqsPort(RecordingSqsPort):
    """Model a consumer crash after DB commit and before SQS deletion."""

    def __init__(self, delegate: SqsPort) -> None:
        super().__init__(delegate)
        self._failed = False

    def delete_message(self, **kwargs: Any) -> None:
        if not self._failed:
            self._failed = True
            raise SqsRetryableError("W1_W4_CT12_INJECTED_ACK_LOSS")
        super().delete_message(**kwargs)


class _InjectedDatabaseFailureFactory:
    @contextmanager
    def begin(self):
        raise SQLAlchemyError("W1_W4_CT12_INJECTED_DATABASE_FAILURE")
        yield  # pragma: no cover


def _boto3() -> Any:
    """Delay the production-only SDK import so configuration checks stay testable."""

    try:
        import boto3
    except ImportError as error:  # pragma: no cover - production image declares boto3
        raise W1W4Ct12ScenarioError("boto3 must be installed to run the CT-12 transport") from error
    return boto3


def _value(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value else None


def _required(name: str) -> str:
    value = _value(name)
    if not value:
        raise W1W4Ct12ScenarioError(f"{name} must be set")
    return value


def _database_identity(value: str) -> tuple[str | None, int | None, str | None]:
    url: URL = make_url(value)
    return url.host, url.port, url.database


def _database_principal(value: str) -> str | None:
    return make_url(value).username


def _queue_name(value: str) -> str:
    return urlparse(value).path.rsplit("/", maxsplit=1)[-1]


def _require_configuration() -> W1W4Ct12Configuration:
    if _value("W1_W4_CT12_EXECUTE_SYNTHETIC") != "YES":
        raise W1W4Ct12ScenarioError(
            "set W1_W4_CT12_EXECUTE_SYNTHETIC=YES to permit the W1-only CT-12 run"
        )
    seed_database_url = _required("W1_W4_CT12_SEED_DATABASE_URL")
    worker_database_url = _required("WORKER_DATABASE_URL")
    seed_identity = _database_identity(seed_database_url)
    worker_identity = _database_identity(worker_database_url)
    if seed_identity != worker_identity:
        raise W1W4Ct12ScenarioError(
            "W1_W4_CT12_SEED_DATABASE_URL and WORKER_DATABASE_URL must target the same database"
        )
    seed_principal = _database_principal(seed_database_url)
    worker_principal = _database_principal(worker_database_url)
    if not seed_principal or not worker_principal or seed_principal == worker_principal:
        raise W1W4Ct12ScenarioError(
            "CT-12 seed and worker database URLs must use different database principals"
        )
    database_name = worker_identity[2] or ""
    if "ct12" not in database_name.lower() or "w1" not in database_name.lower():
        raise W1W4Ct12ScenarioError(
            "CT-12 requires a dedicated database containing w1 and ct12 in its name"
        )
    configured_sender_id = _value("W4_QUESTION_CORE_DECISION_EXPECTED_SENDER_ID")
    if configured_sender_id and ":" in configured_sender_id:
        raise W1W4Ct12ScenarioError(
            "W4 expected sender must be a stable principal id without session"
        )
    try:
        runtime_settings = Settings()
    except ValidationError as error:
        raise W1W4Ct12ScenarioError("W4 Question Core runtime settings are invalid") from error
    try:
        config = build_w4_question_core_preflight_config(
            queue_url=runtime_settings.w4_question_core_decision_queue_url,
            dlq_url=runtime_settings.w4_question_core_decision_dlq_url,
            expected_producer=runtime_settings.w4_question_core_decision_expected_producer,
            expected_sender_id=runtime_settings.w4_question_core_decision_expected_sender_id,
        )
    except W4QuestionCorePreflightError as error:
        raise W1W4Ct12ScenarioError(str(error)) from error
    for queue_url in (config.queue_url, config.dlq_url):
        queue_name = _queue_name(queue_url).lower()
        if "ct12" not in queue_name or "w4" not in queue_name:
            raise W1W4Ct12ScenarioError(
                "CT-12 requires each dedicated queue containing w4 and ct12 in its name"
            )
    sender_role_arn = _required("W1_W4_CT12_SENDER_ROLE_ARN")
    if not re.fullmatch(r"arn:aws:iam::\d{12}:role/[A-Za-z0-9+=,.@_/-]+", sender_role_arn):
        raise W1W4Ct12ScenarioError("W1_W4_CT12_SENDER_ROLE_ARN must be an IAM role ARN")
    run_id = _required("W1_W4_CT12_RUN_ID")
    if "ct12" not in run_id.lower():
        raise W1W4Ct12ScenarioError("W1_W4_CT12_RUN_ID must contain ct12")
    return W1W4Ct12Configuration(
        seed_database_url=seed_database_url,
        worker_database_url=worker_database_url,
        database_name=database_name,
        queue_url=config.queue_url,
        dlq_url=config.dlq_url,
        expected_sender_id=config.expected_sender_id,
        sender_role_arn=sender_role_arn,
        run_id=run_id,
        aws_region=_required("AWS_DEFAULT_REGION"),
    )


def _queue_attributes(*, sqs_client: Any, queue_url: str) -> dict[str, str]:
    response = sqs_client.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=[
            "ApproximateNumberOfMessages",
            "ApproximateNumberOfMessagesNotVisible",
            "ApproximateNumberOfMessagesDelayed",
        ],
    )
    attributes = response.get("Attributes")
    if not isinstance(attributes, dict):
        raise W1W4Ct12ScenarioError("CT-12 queue returned invalid attributes")
    return {str(name): str(value) for name, value in attributes.items()}


def _queue_depth(*, sqs_client: Any, queue_url: str) -> int:
    try:
        attributes = _queue_attributes(sqs_client=sqs_client, queue_url=queue_url)
        return sum(
            int(attributes.get(name, "0"))
            for name in (
                "ApproximateNumberOfMessages",
                "ApproximateNumberOfMessagesNotVisible",
                "ApproximateNumberOfMessagesDelayed",
            )
        )
    except ValueError as error:
        raise W1W4Ct12ScenarioError("CT-12 queue depth attributes are invalid") from error


def _assert_empty_environment(
    *, session_factory: sessionmaker[Session], sqs_client: Any, config: W1W4Ct12Configuration
) -> None:
    with session_factory.begin() as session:
        populated = [
            model.__tablename__
            for model in (
                User,
                InboxReceipt,
                AnalysisSourceDecision,
                JobCoreDecisionBinding,
                JobCommand,
                OutboxMessage,
            )
            if session.scalar(select(func.count()).select_from(model))
        ]
    if populated:
        raise W1W4Ct12ScenarioError(
            "CT-12 fixture database is not empty: " + ", ".join(sorted(populated))
        )
    for queue_url, name in ((config.queue_url, "main"), (config.dlq_url, "DLQ")):
        if _queue_depth(sqs_client=sqs_client, queue_url=queue_url):
            raise W1W4Ct12ScenarioError(f"CT-12 {name} queue is not empty")


def _seed_waiting_question_job(
    *, session: Session, run_id: str, scenario: str
) -> SeededQuestionCore:
    owner = User(display_name=f"CT12 {run_id} {scenario}", locale="ko-KR", timezone="Asia/Seoul")
    company = Company(legal_name=f"CT12 {scenario}", display_name=f"CT12 {scenario}")
    session.add_all((owner, company))
    session.flush()
    project = ApplicationProject(owner_user_id=owner.id)
    session.add(project)
    session.flush()
    project_version = ApplicationProjectVersion(
        project_id=project.id,
        owner_user_id=owner.id,
        version_no=1,
        company_id=company.id,
        title=f"CT12 {scenario}",
        role_name="Synthetic",
    )
    session.add(project_version)
    session.flush()
    project.current_version_id = project_version.id
    question = ProjectQuestion(owner_user_id=owner.id, project_id=project.id, display_order=0)
    session.add(question)
    session.flush()
    question_version = QuestionVersion(
        question_id=question.id,
        project_id=project.id,
        owner_user_id=owner.id,
        version_no=1,
        prompt="Synthetic CT-12 question",
        source="USER",
    )
    session.add(question_version)
    session.flush()
    question.current_version_id = question_version.id
    source = Source(
        company_id=company.id,
        source_type="CAREERS",
        canonical_url=f"https://ct12.invalid/{run_id}/{scenario}",
        canonical_url_hash=f"ct12-{uuid5(NAMESPACE_URL, f'{run_id}:{scenario}:source')}",
        url_normalization_version="v1",
        policy_version="ct12-isolated",
        policy_checked_at=datetime.now(UTC),
    )
    input_version = f"ct12-{scenario}-v1"
    job = Job(
        owner_user_id=owner.id,
        project_id=project.id,
        job_type="SOURCE_COLLECTION",
        status="WAITING_USER",
        dispatch_status="BLOCKED",
        owner_deletion_epoch=0,
        analysis_input_version=input_version,
    )
    session.add_all((source, job))
    session.flush()
    session.add_all(
        (
            JobSourceLink(
                job_id=job.id,
                owner_user_id=owner.id,
                source_id=source.id,
                source_version_id=None,
                command_id=None,
                purpose_ref="SOURCE_COLLECTION",
                analysis_input_version=input_version,
            ),
            JobRequiredAction(
                job_id=job.id,
                owner_user_id=owner.id,
                action_code="CORE_DECISION_REQUIRED",
                action_status="OPEN",
                context_code="CORE_DECISION_BINDING_MISMATCH",
                expected_input_version=input_version,
            ),
        )
    )
    session.flush()
    return SeededQuestionCore(
        owner_id=owner.id,
        job_id=job.id,
        source_id=source.id,
        question_version_id=question_version.id,
        analysis_input_version=input_version,
    )


def _event(*, seed: SeededQuestionCore, run_id: str, scenario: str) -> dict[str, object]:
    event = {
        "schema_version": "w4.private.question-core-decision/0.1-candidate",
        "message_type": "w4.private.w1.question-core-decision",
        "message_id": str(uuid5(NAMESPACE_URL, f"{run_id}:{scenario}:message")),
        "decision_id": str(uuid5(NAMESPACE_URL, f"{run_id}:{scenario}:decision")),
        "occurred_at": "2026-09-19T00:00:00Z",
        "visibility_scope": "PRIVATE",
        "producer": "w4",
        "job_id": str(seed.job_id),
        "company_id": None,
        "question_version_id": str(seed.question_version_id),
        "source_id": str(seed.source_id),
        "analysis_input_version": seed.analysis_input_version,
        "decision_scope": "QUESTION_MATCHING",
        "decision_owner": "W4",
        "decision_version": 1,
        "is_core": True,
        "decision_code": "CORE_REQUIRED",
        "reason_code": "W1_W4_CT12_SYNTHETIC",
    }
    parse_w4_question_core_event(event)
    return event


def _assumed_sender_client(*, config: W1W4Ct12Configuration, sts_client: Any) -> Any:
    response = sts_client.assume_role(
        RoleArn=config.sender_role_arn,
        RoleSessionName=re.sub(r"[^A-Za-z0-9+=,.@-]", "-", config.run_id)[:64],
        DurationSeconds=900,
    )
    credentials = response.get("Credentials")
    required = ("AccessKeyId", "SecretAccessKey", "SessionToken")
    if not isinstance(credentials, dict) or not all(
        isinstance(credentials.get(name), str) for name in required
    ):
        raise W1W4Ct12ScenarioError("CT-12 sender role credentials response is incomplete")
    return _boto3().client(
        "sqs",
        region_name=config.aws_region,
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )


def _worker(
    *, session_factory: Any, sqs: SqsPort, config: W1W4Ct12Configuration
) -> W4QuestionCoreDecisionWorker:
    return W4QuestionCoreDecisionWorker(
        session_factory=session_factory,
        sqs=sqs,
        queue_url=config.queue_url,
        expected_sender_id=config.expected_sender_id,
        batch_size=1,
        visibility_timeout_seconds=120,
        wait_time_seconds=2,
    )


def _send(*, sender: Boto3SqsPort, queue_url: str, body: object) -> None:
    sender.send_message(
        queue_url=queue_url,
        body=json.dumps(body, separators=(",", ":")),
        message_attributes={},
    )


def _drain(worker: W4QuestionCoreDecisionWorker) -> W4QuestionCoreDecisionWorkerResult:
    for _ in range(6):
        result = worker.drain_once()
        if result.received:
            return result
        time.sleep(0.5)
    raise W1W4Ct12ScenarioError("CT-12 did not receive the expected synthetic delivery")


def _release_last_delivery(*, sqs: RecordingSqsPort, queue_url: str) -> None:
    if len(sqs.last_deliveries) != 1:
        raise W1W4Ct12ScenarioError("CT-12 retry probe did not retain one receipt handle")
    sqs.change_message_visibility(
        queue_url=queue_url,
        receipt_handle=sqs.last_deliveries[0].receipt_handle,
        visibility_timeout_seconds=0,
    )


def _assert_result(
    result: W4QuestionCoreDecisionWorkerResult,
    *,
    acknowledged: int,
    retry_scheduled: int,
    applied: int = 0,
    duplicate: int = 0,
    terminal_rejected: int = 0,
    context: str,
) -> None:
    expected = W4QuestionCoreDecisionWorkerResult(
        received=1,
        acknowledged=acknowledged,
        retry_scheduled=retry_scheduled,
        applied=applied,
        duplicate=duplicate,
        terminal_rejected=terminal_rejected,
    )
    if result != expected:
        raise W1W4Ct12ScenarioError(
            f"{context}: expected={asdict(expected)}, actual={asdict(result)}"
        )


def _row_counts(*, session_factory: sessionmaker[Session]) -> RowCounts:
    with session_factory.begin() as session:
        return RowCounts(
            receipts=session.scalar(select(func.count()).select_from(InboxReceipt)) or 0,
            decisions=session.scalar(select(func.count()).select_from(AnalysisSourceDecision)) or 0,
            bindings=session.scalar(select(func.count()).select_from(JobCoreDecisionBinding)) or 0,
            commands=session.scalar(select(func.count()).select_from(JobCommand)) or 0,
            outbox=session.scalar(select(func.count()).select_from(OutboxMessage)) or 0,
        )


def _explicit_retry(*, factory: sessionmaker[Session], seed: SeededQuestionCore) -> None:
    with factory.begin() as session:
        action = session.scalar(
            select(JobRequiredAction).where(
                JobRequiredAction.job_id == seed.job_id,
                JobRequiredAction.owner_user_id == seed.owner_id,
                JobRequiredAction.action_code == "RETRY",
                JobRequiredAction.action_status == "OPEN",
            )
        )
        if action is None:
            raise W1W4Ct12ScenarioError("CT-12 valid apply did not create an explicit RETRY action")
        JobService(session).apply_required_action(
            owner_user_id=seed.owner_id,
            job_id=seed.job_id,
            required_action_id=action.id,
            action_code="RETRY",
            expected_input_version=action.expected_input_version,
            expected_result_version=action.expected_result_version,
            acknowledge_rate_limit=False,
        )


def run() -> dict[str, object]:
    config = _require_configuration()
    seed_engine = create_engine(config.seed_database_url, pool_pre_ping=True)
    worker_engine = create_engine(config.worker_database_url, pool_pre_ping=True)
    seed_factory = sessionmaker(bind=seed_engine, autoflush=False, expire_on_commit=False)
    worker_factory = sessionmaker(bind=worker_engine, autoflush=False, expire_on_commit=False)
    boto3 = _boto3()
    receiver_client = boto3.client("sqs", region_name=config.aws_region)
    receiver = RecordingSqsPort(Boto3SqsPort(client=receiver_client))
    try:
        verify_w4_question_core_runtime(
            config=build_w4_question_core_preflight_config(
                queue_url=config.queue_url,
                dlq_url=config.dlq_url,
                expected_producer="w4",
                expected_sender_id=config.expected_sender_id,
            ),
            session_factory=worker_factory,
            sqs=receiver,
        )
        _assert_empty_environment(
            session_factory=seed_factory, sqs_client=receiver_client, config=config
        )
        sender = Boto3SqsPort(
            client=_assumed_sender_client(
                config=config,
                sts_client=boto3.client("sts", region_name=config.aws_region),
            )
        )
        with seed_factory.begin() as session:
            applied_seed = _seed_waiting_question_job(
                session=session, run_id=config.run_id, scenario="apply"
            )
            redelivery_seed = _seed_waiting_question_job(
                session=session, run_id=config.run_id, scenario="redelivery"
            )
            ack_loss_seed = _seed_waiting_question_job(
                session=session, run_id=config.run_id, scenario="ack-loss"
            )
        worker = _worker(session_factory=worker_factory, sqs=receiver, config=config)

        applied_event = _event(seed=applied_seed, run_id=config.run_id, scenario="apply")
        _send(sender=sender, queue_url=config.queue_url, body=applied_event)
        _assert_result(
            _drain(worker), acknowledged=1, retry_scheduled=0, applied=1, context="apply"
        )
        _send(sender=sender, queue_url=config.queue_url, body=applied_event)
        _assert_result(
            _drain(worker), acknowledged=1, retry_scheduled=0, duplicate=1, context="duplicate"
        )
        _explicit_retry(factory=seed_factory, seed=applied_seed)

        redelivery_event = _event(seed=redelivery_seed, run_id=config.run_id, scenario="redelivery")
        _send(sender=sender, queue_url=config.queue_url, body=redelivery_event)
        failing_worker = _worker(
            session_factory=_InjectedDatabaseFailureFactory(), sqs=receiver, config=config
        )
        _assert_result(
            _drain(failing_worker),
            acknowledged=0,
            retry_scheduled=1,
            context="database failure retention",
        )
        _release_last_delivery(sqs=receiver, queue_url=config.queue_url)
        _assert_result(
            _drain(worker), acknowledged=1, retry_scheduled=0, applied=1, context="redelivery"
        )

        ack_loss_event = _event(seed=ack_loss_seed, run_id=config.run_id, scenario="ack-loss")
        _send(sender=sender, queue_url=config.queue_url, body=ack_loss_event)
        ack_loss_port = FailFirstDeleteSqsPort(Boto3SqsPort(client=receiver_client))
        ack_loss_worker = _worker(session_factory=worker_factory, sqs=ack_loss_port, config=config)
        _assert_result(
            _drain(ack_loss_worker),
            acknowledged=0,
            retry_scheduled=1,
            applied=1,
            context="commit before delete",
        )
        _release_last_delivery(sqs=ack_loss_port, queue_url=config.queue_url)
        _assert_result(
            _drain(worker),
            acknowledged=1,
            retry_scheduled=0,
            duplicate=1,
            context="commit before delete redelivery",
        )
        counts = _row_counts(session_factory=seed_factory)
    finally:
        seed_engine.dispose()
        worker_engine.dispose()

    return {
        "status": "ok",
        "run_id": config.run_id,
        "database": config.database_name,
        "apply": "durable_then_acknowledged",
        "duplicate": "applied_once_then_acknowledged",
        "explicit_retry": "one_w1_command_and_outbox",
        "redelivery": "database_failure_then_applied",
        "ack_loss": "commit_then_duplicate_acknowledged",
        "rows": asdict(counts),
        "w4_actual_producer": "not_proven_by_w1_only_synthetic",
    }


def main() -> None:
    print(json.dumps(run(), separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (W1W4Ct12ScenarioError, W4QuestionCorePreflightError) as error:
        raise SystemExit(str(error)) from error
