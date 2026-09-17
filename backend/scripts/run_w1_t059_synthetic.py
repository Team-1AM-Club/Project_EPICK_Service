"""Run the W1-owned T059 scenario only against disposable PostgreSQL and SQS resources.

This is an operator tool, not a public API or a persistent worker entrypoint.  It deliberately
does not start the compose ``w1-execution`` profile: the scenario owns its synthetic rows and
stops after proving one bounded relay/worker pass.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import boto3  # noqa: E402
from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.engine import URL, make_url  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.models.identity import User  # noqa: E402
from app.models.jobs import Job, JobCommand, JobExecutionLease, OutboxMessage  # noqa: E402
from app.models.sources import AnalysisSourceDecision, JobSourceLink, Source  # noqa: E402
from app.runtime.outbox_relay import OutboxRelay, QueueUrlRegistry  # noqa: E402
from app.runtime.sqs import Boto3SqsPort  # noqa: E402
from app.runtime.t059_isolation_preflight import (  # noqa: E402
    T059IsolationPreflightError,
    build_t059_isolation_preflight_config,
    verify_t059_isolation,
)
from app.runtime.workers import JobWorker  # noqa: E402
from app.services.application_workspace import ApplicationWorkspaceService  # noqa: E402
from app.services.jobs import JobService  # noqa: E402


class T059ScenarioError(RuntimeError):
    """A safe reason why the synthetic scenario refused to run or did not prove its invariant."""


@dataclass(frozen=True)
class T059ScenarioConfiguration:
    seed_database_url: str
    worker_database_url: str
    execution_queue_url: str
    command_queue_url: str
    run_id: str
    aws_region: str


@dataclass(frozen=True)
class T059Seed:
    owner_id: UUID
    dispatchable_job_ids: tuple[UUID, UUID, UUID, UUID]
    missing_pin_job_id: UUID


def _environment(name: str) -> str | None:
    return os.environ.get(name)


def _require_configuration() -> T059ScenarioConfiguration:
    if _environment("T059_EXECUTE_SYNTHETIC") != "YES":
        raise T059ScenarioError("set T059_EXECUTE_SYNTHETIC=YES to permit a synthetic T059 run")

    aws_session = boto3.session.Session()
    worker_config = build_t059_isolation_preflight_config(
        worker_database_url=_environment("T059_WORKER_DATABASE_URL"),
        execution_queue_url=_environment("T059_EXECUTION_QUEUE_URL"),
        command_queue_url=_environment("T059_W2_COMMAND_QUEUE_URL"),
        run_id=_environment("T059_RUN_ID"),
        aws_region=aws_session.region_name,
    )
    seed_database_url = _environment("T059_SEED_DATABASE_URL")
    if not seed_database_url:
        raise T059ScenarioError("T059_SEED_DATABASE_URL must be set for disposable fixture setup")

    seed_config = build_t059_isolation_preflight_config(
        worker_database_url=seed_database_url,
        execution_queue_url=worker_config.execution_queue_url,
        command_queue_url=worker_config.command_queue_url,
        run_id=worker_config.run_id,
        aws_region=worker_config.aws_region,
    )
    if _database_identity(seed_config.worker_database_url) != _database_identity(
        worker_config.worker_database_url
    ):
        raise T059ScenarioError(
            "T059_SEED_DATABASE_URL and T059_WORKER_DATABASE_URL must target the same database"
        )

    return T059ScenarioConfiguration(
        seed_database_url=seed_config.worker_database_url,
        worker_database_url=worker_config.worker_database_url,
        execution_queue_url=worker_config.execution_queue_url,
        command_queue_url=worker_config.command_queue_url,
        run_id=worker_config.run_id,
        aws_region=worker_config.aws_region,
    )


def _database_identity(value: str) -> tuple[str | None, int | None, str | None]:
    url: URL = make_url(value)
    return url.host, url.port, url.database


def _assert_empty_fixture_database(*, session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        if session.scalar(select(func.count()).select_from(User)) != 0:
            raise T059ScenarioError(
                "T059 fixture database is not empty; use a new disposable database"
            )
        if session.scalar(select(func.count()).select_from(OutboxMessage)) != 0:
            raise T059ScenarioError("T059 fixture database has existing outbox rows")


def _queue_depth(*, sqs_client: Any, queue_url: str) -> int:
    response = sqs_client.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )
    attributes = response.get("Attributes")
    if not isinstance(attributes, dict):
        raise T059ScenarioError("isolated queue returned an invalid attributes response")
    try:
        return int(attributes["ApproximateNumberOfMessages"]) + int(
            attributes["ApproximateNumberOfMessagesNotVisible"]
        )
    except (KeyError, TypeError, ValueError) as error:
        raise T059ScenarioError("isolated queue depth attributes are invalid") from error


def _assert_empty_fixture_queues(*, sqs_client: Any, config: T059ScenarioConfiguration) -> None:
    if _queue_depth(sqs_client=sqs_client, queue_url=config.execution_queue_url) != 0:
        raise T059ScenarioError("T059 execution queue is not empty; create a fresh dedicated queue")
    if _queue_depth(sqs_client=sqs_client, queue_url=config.command_queue_url) != 0:
        raise T059ScenarioError("T059 command queue is not empty; create a fresh dedicated queue")


def _seed_source_collection_job(
    *, session: Session, owner: User, run_id: str, sequence: int, include_core_pin: bool
) -> UUID:
    suffix = f"{run_id}-{sequence}"
    company = ApplicationWorkspaceService(session).create_company(
        legal_name=f"EPICK T059 {suffix}", display_name=f"EPICK T059 {suffix}"
    )
    source = Source(
        company_id=company.id,
        source_type="CAREERS",
        canonical_url=f"https://t059.invalid/{suffix}",
        canonical_url_hash=f"t059-{suffix}",
        url_normalization_version="v1",
        policy_version="t059-isolated",
        policy_checked_at=datetime.now(UTC),
    )
    session.add(source)
    session.flush()
    decision = AnalysisSourceDecision(
        decision_scope="COMPANY_KNOWLEDGE",
        company_id=company.id,
        question_version_id=None,
        source_id=source.id,
        source_version_id=None,
        analysis_input_version="t059-input:v1",
        decision_version=1,
        decision_code="CORE_REQUIRED",
        # COMPANY_KNOWLEDGE decisions are canonically W3-owned.  This is a synthetic
        # fixture of that contract, not a new decision owner.
        decision_owner="W3",
        reason_code="ISOLATED_RUNTIME_PROBE",
    )
    session.add(decision)
    accepted = JobService(session).accept_job(
        owner_user_id=owner.id,
        job_type="SOURCE_COLLECTION",
        idempotency_key=f"t059-{suffix}",
        request_hash=f"t059-{suffix}",
        analysis_input_version="t059-input:v1",
    )
    if accepted.command is None:
        raise T059ScenarioError("synthetic Job did not create an execution command")
    session.flush()
    session.add(
        JobSourceLink(
            job_id=accepted.job.id,
            owner_user_id=owner.id,
            source_id=source.id,
            source_version_id=None,
            command_id=None,
            purpose_ref="SOURCE_COLLECTION",
            analysis_input_version="t059-input:v1",
        )
    )
    accepted.command.analysis_source_decision_id = decision.id
    if include_core_pin:
        accepted.command.payload = {
            "command_type": "EXECUTE_JOB",
            "core_decision_pin": {
                "origin_message_id": str(uuid4()),
                "decision_id": str(decision.id),
                "decision_scope": "COMPANY_KNOWLEDGE",
                "company_id": str(company.id),
                "question_version_id": None,
                "source_id": str(source.id),
                "analysis_input_version": "t059-input:v1",
                "decision_version": 1,
                "is_core": True,
                "decision_code": "CORE_REQUIRED",
                "reason_code": "ISOLATED_RUNTIME_PROBE",
            },
        }
    session.flush()
    return accepted.job.id


def _seed_fixture(*, session_factory: sessionmaker[Session], run_id: str) -> T059Seed:
    with session_factory.begin() as session:
        owner = User(display_name=f"T059 synthetic {run_id}", locale="ko-KR", timezone="Asia/Seoul")
        session.add(owner)
        session.flush()
        dispatchable_job_ids = tuple(
            _seed_source_collection_job(
                session=session,
                owner=owner,
                run_id=run_id,
                sequence=sequence,
                include_core_pin=True,
            )
            for sequence in range(1, 5)
        )
        missing_pin_job_id = _seed_source_collection_job(
            session=session,
            owner=owner,
            run_id=run_id,
            sequence=5,
            include_core_pin=False,
        )
        return T059Seed(
            owner_id=owner.id,
            dispatchable_job_ids=(
                dispatchable_job_ids[0],
                dispatchable_job_ids[1],
                dispatchable_job_ids[2],
                dispatchable_job_ids[3],
            ),
            missing_pin_job_id=missing_pin_job_id,
        )


def _assert_worker_outcomes(*, session_factory: sessionmaker[Session], seed: T059Seed) -> UUID:
    with session_factory.begin() as session:
        jobs = [session.get(Job, job_id) for job_id in seed.dispatchable_job_ids]
        if any(job is None for job in jobs):
            raise T059ScenarioError("a synthetic dispatchable Job was not found")
        running = [job for job in jobs if job is not None and job.status == "RUNNING"]
        queued = [job for job in jobs if job is not None and job.status == "QUEUED"]
        if len(running) != 3 or len(queued) != 1:
            raise T059ScenarioError(
                "T059 slot invariant failed: expected exactly 3 RUNNING and 1 QUEUED"
            )
        if any(job is None or job.active_lease_id is None for job in running):
            raise T059ScenarioError("T059 RUNNING Jobs do not each own a lease")

        missing_pin_job = session.get(Job, seed.missing_pin_job_id)
        if (
            missing_pin_job is None
            or missing_pin_job.status != "WAITING_USER"
            or missing_pin_job.dispatch_status != "BLOCKED"
        ):
            raise T059ScenarioError("T059 missing Core pin was not blocked before W2 dispatch")

        w2_command_count = session.scalar(
            select(func.count())
            .select_from(JobCommand)
            .where(
                JobCommand.owner_user_id == seed.owner_id,
                JobCommand.command_type == "W2_SOURCE_COLLECTION",
            )
        )
        if w2_command_count != 3:
            raise T059ScenarioError("T059 W2 dispatch count does not match the three claimed Jobs")
        return running[0].id


def _prove_explicit_retry(
    *, session_factory: sessionmaker[Session], owner_id: UUID, job_id: UUID
) -> None:
    with session_factory.begin() as session:
        job = session.get(Job, job_id)
        if job is None or job.active_lease_id is None:
            raise T059ScenarioError("T059 retry target has no current lease")
        lease = session.get(JobExecutionLease, job.active_lease_id)
        if lease is None:
            raise T059ScenarioError("T059 retry target lease was not found")
        old_fence = job.execution_fence
        if not JobService(session).commit_result(
            owner_user_id=owner_id,
            job_id=job.id,
            lease_id=lease.id,
            execution_fence=old_fence,
            owner_deletion_epoch=job.owner_deletion_epoch,
            final_status="FAILED_RETRYABLE",
            completeness="partial",
        ):
            raise T059ScenarioError("T059 could not create a retryable synthetic result")
        retried = JobService(session).retry_job(
            owner_user_id=owner_id,
            job_id=job.id,
            idempotency_key=f"t059-retry-{job.id}",
            request_hash=f"t059-retry-{job.id}",
        )
        if retried.command is None or retried.job.execution_fence != old_fence + 1:
            raise T059ScenarioError("T059 explicit retry did not create a new fenced command")
        if JobService(session).commit_result(
            owner_user_id=owner_id,
            job_id=job.id,
            lease_id=lease.id,
            execution_fence=old_fence,
            owner_deletion_epoch=job.owner_deletion_epoch,
            final_status="SUCCEEDED",
            completeness="complete",
        ):
            raise T059ScenarioError("T059 accepted a late result from the pre-retry fence")


def _drain_synthetic_execution(*, worker: JobWorker) -> tuple[int, int, int]:
    """Consume five fresh deliveries one at a time without depending on Standard-SQS ordering."""

    received = 0
    acknowledged = 0
    retry_scheduled = 0
    for _ in range(20):
        result = worker.drain_once(max_messages=1)
        received += result.received
        acknowledged += result.acknowledged
        retry_scheduled += result.retry_scheduled
        if received == 5:
            return received, acknowledged, retry_scheduled
        if result.received == 0:
            time.sleep(1)
    raise T059ScenarioError("T059 did not receive all five synthetic execution deliveries")


def run() -> dict[str, str]:
    config = _require_configuration()
    seed_engine = create_engine(config.seed_database_url, pool_pre_ping=True)
    worker_engine = create_engine(config.worker_database_url, pool_pre_ping=True)
    seed_factory = sessionmaker(bind=seed_engine, autoflush=False, expire_on_commit=False)
    worker_factory = sessionmaker(bind=worker_engine, autoflush=False, expire_on_commit=False)
    aws_session = boto3.session.Session(region_name=config.aws_region)
    sqs_client = aws_session.client("sqs")
    sqs = Boto3SqsPort(client=sqs_client)
    try:
        verify_t059_isolation(
            config=build_t059_isolation_preflight_config(
                worker_database_url=config.worker_database_url,
                execution_queue_url=config.execution_queue_url,
                command_queue_url=config.command_queue_url,
                run_id=config.run_id,
                aws_region=config.aws_region,
            ),
            session_factory=worker_factory,
            sqs_client=sqs_client,
        )
        _assert_empty_fixture_database(session_factory=seed_factory)
        _assert_empty_fixture_queues(sqs_client=sqs_client, config=config)
        seed = _seed_fixture(session_factory=seed_factory, run_id=config.run_id)
        relay_result = OutboxRelay(
            session_factory=worker_factory,
            sqs=sqs,
            queues=QueueUrlRegistry(
                w1_execution_queue_url=config.execution_queue_url,
                w2_collection_command_queue_url=config.command_queue_url,
            ),
            relay_id=f"{config.run_id}-relay",
        ).drain_once(limit=5)
        if relay_result.published != 5 or relay_result.retry_scheduled or relay_result.failed_final:
            raise T059ScenarioError("T059 initial execution dispatch relay result was not clean")
        worker = JobWorker(
            session_factory=worker_factory,
            sqs=sqs,
            execution_queue_url=config.execution_queue_url,
            worker_id=f"{config.run_id}-worker",
        )
        received, acknowledged, retry_scheduled = _drain_synthetic_execution(worker=worker)
        if (
            received != 5
            or acknowledged != 4
            or retry_scheduled != 1
        ):
            raise T059ScenarioError(
                "T059 execution queue result did not retain exactly one fourth delivery"
            )
        retry_job_id = _assert_worker_outcomes(session_factory=seed_factory, seed=seed)
        _prove_explicit_retry(
            session_factory=seed_factory,
            owner_id=seed.owner_id,
            job_id=retry_job_id,
        )
    finally:
        seed_engine.dispose()
        worker_engine.dispose()

    return {
        "status": "ok",
        "run_id": config.run_id,
        "slots": "3_running_1_redelivery",
        "core_pin": "blocked_without_w2_send",
        "retry": "new_fence_created_late_result_rejected",
    }


def main() -> None:
    print(json.dumps(run(), separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (T059IsolationPreflightError, T059ScenarioError) as error:
        raise SystemExit(str(error)) from error
