"""Run T060 restart, duplicate, checkpoint, cancellation, and lease recovery probes.

The command is intentionally restricted to disposable ``epick_t060_*`` PostgreSQL databases
and three dedicated SQS queues whose names contain ``t060``.  It is an operator probe, not a
persistent worker.  Child phases are separate Linux processes so no Python in-memory state can
survive the relay/result-worker restart boundaries being verified.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import boto3  # noqa: E402
from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.engine import URL, make_url  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app.models.deletion import DeletionRequest  # noqa: F401, E402
from app.models.identity import User  # noqa: E402
from app.models.jobs import (  # noqa: E402
    InboxReceipt,
    Job,
    JobCommand,
    JobExecutionLease,
    JobRequiredAction,
    OutboxMessage,
    OwnerExecutionSlot,
)
from app.models.lifecycle_operations import JobCheckpoint  # noqa: E402
from app.models.sources import AnalysisSourceDecision, JobSourceLink, Source  # noqa: E402
from app.runtime.outbox_relay import OutboxRelay, QueueUrlRegistry  # noqa: E402
from app.runtime.sqs import Boto3SqsPort  # noqa: E402
from app.runtime.t060_isolation_preflight import (  # noqa: E402
    T060IsolationPreflightError,
    build_t060_isolation_preflight_config,
    verify_t060_isolation,
)
from app.runtime.workers import CollectionResultWorker, JobWorker  # noqa: E402
from app.services.application_workspace import ApplicationWorkspaceService  # noqa: E402
from app.services.jobs import JobService  # noqa: E402


class T060ScenarioError(RuntimeError):
    """A safe reason why T060 refused to run or failed an invariant."""


@dataclass(frozen=True)
class T060Configuration:
    seed_database_url: str
    worker_database_url: str
    execution_queue_url: str
    command_queue_url: str
    result_queue_url: str
    run_id: str
    aws_region: str


@dataclass(frozen=True)
class SeededJob:
    owner_id: UUID
    job_id: UUID
    execution_command_id: UUID
    execution_outbox_id: UUID


def _database_identity(value: str) -> tuple[str | None, int | None, str | None]:
    url: URL = make_url(value)
    return url.host, url.port, url.database


def _require_configuration() -> T060Configuration:
    if os.environ.get("T060_EXECUTE_SYNTHETIC") != "YES":
        raise T060ScenarioError("set T060_EXECUTE_SYNTHETIC=YES to permit a synthetic T060 run")
    aws_session = boto3.session.Session()
    worker = build_t060_isolation_preflight_config(
        worker_database_url=os.environ.get("T060_WORKER_DATABASE_URL"),
        execution_queue_url=os.environ.get("T060_EXECUTION_QUEUE_URL"),
        command_queue_url=os.environ.get("T060_W2_COMMAND_QUEUE_URL"),
        result_queue_url=os.environ.get("T060_W2_RESULT_QUEUE_URL"),
        run_id=os.environ.get("T060_RUN_ID"),
        aws_region=aws_session.region_name,
    )
    seed_database_url = os.environ.get("T060_SEED_DATABASE_URL")
    if not seed_database_url:
        raise T060ScenarioError("T060_SEED_DATABASE_URL must be set for disposable fixture setup")
    seed = build_t060_isolation_preflight_config(
        worker_database_url=seed_database_url,
        execution_queue_url=worker.execution_queue_url,
        command_queue_url=worker.command_queue_url,
        result_queue_url=worker.result_queue_url,
        run_id=worker.run_id,
        aws_region=worker.aws_region,
    )
    if _database_identity(seed.worker_database_url) != _database_identity(
        worker.worker_database_url
    ):
        raise T060ScenarioError(
            "T060_SEED_DATABASE_URL and T060_WORKER_DATABASE_URL must target the same database"
        )
    return T060Configuration(
        seed_database_url=seed.worker_database_url,
        worker_database_url=worker.worker_database_url,
        execution_queue_url=worker.execution_queue_url,
        command_queue_url=worker.command_queue_url,
        result_queue_url=worker.result_queue_url,
        run_id=worker.run_id,
        aws_region=worker.aws_region,
    )


def _factories(
    config: T060Configuration,
) -> tuple[Any, Any, sessionmaker[Session], sessionmaker[Session]]:
    seed_engine = create_engine(config.seed_database_url, pool_pre_ping=True)
    worker_engine = create_engine(config.worker_database_url, pool_pre_ping=True)
    return (
        seed_engine,
        worker_engine,
        sessionmaker(bind=seed_engine, autoflush=False, expire_on_commit=False),
        sessionmaker(bind=worker_engine, autoflush=False, expire_on_commit=False),
    )


def _queue_depth(*, client: Any, queue_url: str) -> int:
    response = client.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )
    attributes = response.get("Attributes")
    if not isinstance(attributes, dict):
        raise T060ScenarioError("isolated queue returned invalid depth attributes")
    try:
        return int(attributes["ApproximateNumberOfMessages"]) + int(
            attributes["ApproximateNumberOfMessagesNotVisible"]
        )
    except (KeyError, TypeError, ValueError) as error:
        raise T060ScenarioError("isolated queue depth attributes are invalid") from error


def _assert_empty_environment(
    *, config: T060Configuration, seed_factory: sessionmaker[Session], sqs_client: Any
) -> None:
    with seed_factory.begin() as session:
        if session.scalar(select(func.count()).select_from(User)) != 0:
            raise T060ScenarioError("T060 database is not empty; use a new disposable database")
        if session.scalar(select(func.count()).select_from(OutboxMessage)) != 0:
            raise T060ScenarioError("T060 database contains existing outbox rows")
    for label, queue_url in (
        ("execution", config.execution_queue_url),
        ("result", config.result_queue_url),
    ):
        if _queue_depth(client=sqs_client, queue_url=queue_url) != 0:
            raise T060ScenarioError(
                f"T060 {label} queue is not empty; use a fresh dedicated queue"
            )


def _seed_job(
    *, session_factory: sessionmaker[Session], run_id: str, scenario: str
) -> SeededJob:
    suffix = f"{run_id}-{scenario}"
    with session_factory.begin() as session:
        owner = User(display_name=f"T060 {scenario}", locale="ko-KR", timezone="Asia/Seoul")
        session.add(owner)
        session.flush()
        company = ApplicationWorkspaceService(session).create_company(
            legal_name=f"EPICK T060 {scenario}", display_name=f"EPICK T060 {scenario}"
        )
        source = Source(
            company_id=company.id,
            source_type="CAREERS",
            canonical_url=f"https://t060.invalid/{suffix}",
            canonical_url_hash=f"t060-{suffix}",
            url_normalization_version="v1",
            policy_version="t060-isolated",
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
            analysis_input_version="t060-input:v1",
            decision_version=1,
            decision_code="CORE_REQUIRED",
            decision_owner="W3",
            reason_code="ISOLATED_RUNTIME_PROBE",
        )
        session.add(decision)
        accepted = JobService(session).accept_job(
            owner_user_id=owner.id,
            job_type="SOURCE_COLLECTION",
            idempotency_key=f"t060-{suffix}",
            request_hash=f"t060-{suffix}",
            analysis_input_version="t060-input:v1",
        )
        if accepted.command is None or accepted.outbox_message is None:
            raise T060ScenarioError("synthetic Job did not create an execution command")
        session.flush()
        session.add(
            JobSourceLink(
                job_id=accepted.job.id,
                owner_user_id=owner.id,
                source_id=source.id,
                source_version_id=None,
                command_id=None,
                purpose_ref="SOURCE_COLLECTION",
                analysis_input_version="t060-input:v1",
            )
        )
        accepted.command.analysis_source_decision_id = decision.id
        accepted.command.payload = {
            "command_type": "EXECUTE_JOB",
            "core_decision_pin": {
                "origin_message_id": str(uuid5(NAMESPACE_URL, f"{suffix}:origin")),
                "decision_id": str(decision.id),
                "decision_scope": "COMPANY_KNOWLEDGE",
                "company_id": str(company.id),
                "question_version_id": None,
                "source_id": str(source.id),
                "analysis_input_version": "t060-input:v1",
                "decision_version": 1,
                "is_core": True,
                "decision_code": "CORE_REQUIRED",
                "reason_code": "ISOLATED_RUNTIME_PROBE",
            },
        }
        session.flush()
        return SeededJob(
            owner_id=owner.id,
            job_id=accepted.job.id,
            execution_command_id=accepted.command.id,
            execution_outbox_id=accepted.outbox_message.id,
        )


def _child_payload() -> dict[str, str]:
    try:
        payload = json.loads(os.environ["T060_CHILD_PAYLOAD"])
    except (KeyError, json.JSONDecodeError) as error:
        raise T060ScenarioError("T060 child payload is missing or invalid") from error
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in payload.items()
    ):
        raise T060ScenarioError("T060 child payload must be a string map")
    return payload


def _run_child(
    phase: str,
    *,
    payload: dict[str, str] | None = None,
    expected_returncode: int = 0,
) -> dict[str, object]:
    environment = os.environ.copy()
    environment["T060_CHILD_PAYLOAD"] = json.dumps(payload or {}, separators=(",", ":"))
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--phase", phase],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if completed.returncode != expected_returncode:
        safe_stderr = completed.stderr.strip().splitlines()[-1:] or ["no child error output"]
        raise T060ScenarioError(
            f"T060 child phase {phase} exited {completed.returncode}: {safe_stderr[0][:300]}"
        )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise T060ScenarioError(f"T060 child phase {phase} returned no status")
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError as error:
        raise T060ScenarioError(f"T060 child phase {phase} returned invalid status") from error
    if not isinstance(result, dict):
        raise T060ScenarioError(f"T060 child phase {phase} returned a non-object status")
    return result


def _relay(
    config: T060Configuration,
    factory: sessionmaker[Session],
    *,
    relay_id: str,
) -> OutboxRelay:
    return OutboxRelay(
        session_factory=factory,
        sqs=Boto3SqsPort(region_name=config.aws_region),
        queues=QueueUrlRegistry(
            w1_execution_queue_url=config.execution_queue_url,
            w2_collection_command_queue_url=config.command_queue_url,
        ),
        relay_id=relay_id,
        lease_seconds=1,
        retry_base_seconds=1,
        retry_max_seconds=5,
    )


def _child_relay_crash(config: T060Configuration) -> None:
    payload = _child_payload()
    expected_outbox_id = UUID(payload["outbox_id"])
    _, worker_engine, _, worker_factory = _factories(config)
    try:
        batch = _relay(config, worker_factory, relay_id=f"{config.run_id}-crash").claim_due(
            limit=1
        )
        if len(batch.claims) != 1 or batch.claims[0].outbox_id != expected_outbox_id:
            raise T060ScenarioError("relay crash phase did not claim the expected outbox")
        claim = batch.claims[0]
        Boto3SqsPort(region_name=config.aws_region).send_message(
            queue_url=claim.queue_url,
            body=claim.body,
            message_attributes=claim.message_attributes,
        )
        print(
            json.dumps(
                {"status": "expected_process_exit", "phase": "relay_after_send_before_mark"},
                separators=(",", ":"),
            ),
            flush=True,
        )
        os._exit(75)
    finally:
        worker_engine.dispose()


def _child_relay_drain(config: T060Configuration) -> dict[str, object]:
    _, worker_engine, _, worker_factory = _factories(config)
    try:
        result = _relay(
            config, worker_factory, relay_id=f"{config.run_id}-restarted-relay"
        ).drain_once(limit=10)
        return {"status": "ok", **result.__dict__}
    finally:
        worker_engine.dispose()


def _job_worker(config: T060Configuration, factory: sessionmaker[Session]) -> JobWorker:
    return JobWorker(
        session_factory=factory,
        sqs=Boto3SqsPort(region_name=config.aws_region),
        execution_queue_url=config.execution_queue_url,
        worker_id=f"{config.run_id}-restarted-worker",
        visibility_timeout_seconds=30,
        long_poll_seconds=2,
        lease_heartbeat_seconds=5,
    )


def _child_job_once(config: T060Configuration) -> dict[str, object]:
    _, worker_engine, _, worker_factory = _factories(config)
    try:
        result = _job_worker(config, worker_factory).drain_once(max_messages=1)
        return {"status": "ok", **result.__dict__}
    finally:
        worker_engine.dispose()


def _child_recover_lease(config: T060Configuration) -> dict[str, object]:
    _, worker_engine, _, worker_factory = _factories(config)
    try:
        recovered = _job_worker(config, worker_factory).recover_expired_leases()
        return {"status": "ok", "lease_recovered": recovered}
    finally:
        worker_engine.dispose()


def _child_result_once(config: T060Configuration) -> dict[str, object]:
    _, worker_engine, _, worker_factory = _factories(config)
    try:
        result = CollectionResultWorker(
            session_factory=worker_factory,
            sqs=Boto3SqsPort(region_name=config.aws_region),
            result_queue_url=config.result_queue_url,
            visibility_timeout_seconds=30,
            long_poll_seconds=2,
        ).drain_once(max_messages=1)
        return {"status": "ok", **result.__dict__}
    finally:
        worker_engine.dispose()


def _get_w2_command(
    *, session_factory: sessionmaker[Session], job_id: UUID
) -> JobCommand:
    with session_factory() as session:
        command = session.scalar(
            select(JobCommand)
            .where(JobCommand.job_id == job_id, JobCommand.command_type == "W2_SOURCE_COLLECTION")
            .order_by(JobCommand.command_sequence.desc())
            .limit(1)
        )
        if command is None:
            raise T060ScenarioError("synthetic Job did not create a W2 command")
        session.expunge(command)
        return command


def _drain_relay_clean(*, context: str) -> dict[str, object]:
    result = _run_child("relay-drain")
    if (
        int(result.get("published", 0)) < 1
        or result.get("retry_scheduled") != 0
        or result.get("failed_final") != 0
    ):
        raise T060ScenarioError(
            f"{context}: claimed={result.get('claimed', 0)}, "
            f"published={result.get('published', 0)}, "
            f"retry_scheduled={result.get('retry_scheduled', 0)}, "
            f"failed_final={result.get('failed_final', 0)}, "
            f"stale_completion={result.get('stale_completion', 0)}"
        )
    return result


def _dispatch_job(
    *, config: T060Configuration, seed_factory: sessionmaker[Session], seeded: SeededJob
) -> JobCommand:
    _drain_relay_clean(context="execution relay did not publish cleanly")
    for _ in range(10):
        worker = _run_child("job-once")
        with seed_factory() as session:
            job = session.get(Job, seeded.job_id)
            if job is not None and job.status == "RUNNING" and job.active_lease_id is not None:
                break
        if worker.get("received") == 0:
            time.sleep(1)
    else:
        raise T060ScenarioError("restarted Job worker did not claim the expected execution")
    command = _get_w2_command(session_factory=seed_factory, job_id=seeded.job_id)
    # A synthetic result must not overtake the W2 command's broker publication.
    # Otherwise the result consumes the DB command while its outbox is still
    # PENDING, and the next relay correctly rejects that stale outbox.
    _drain_relay_clean(context="W2 command relay did not publish cleanly")
    with seed_factory() as session:
        job = session.get(Job, seeded.job_id)
        if job is None or job.status != "RUNNING" or job.active_lease_id is None:
            raise T060ScenarioError("synthetic Job is not running with a current lease")
    return command


def _result_envelope(
    *, command: JobCommand, message_id: UUID, partial: bool = False
) -> dict[str, object]:
    w2_payload = command.payload.get("w2_command")
    if not isinstance(w2_payload, dict):
        raise T060ScenarioError("W2 command payload is missing")
    payload: dict[str, object] = {
        "schema_version": "w2.collection.v1",
        "command_id": str(command.id),
        "job_id": str(command.job_id),
        "input_version": w2_payload["input_version"],
        "result_version": 1,
        "successful_source_refs": [
            {
                "source_id": w2_payload["source_id"],
                "source_version_id": str(uuid4()),
                "extraction_revision_id": str(uuid4()),
            }
        ],
        "failures": [],
        "completion_kind": "complete",
        "resume_stage": None,
        "checkpoint_ref": None,
        "retry_not_before": None,
        "message_ko": "T060 합성 결과",
        "source_id": w2_payload["source_id"],
        "policy_revision": 1,
        "required_actions": [],
    }
    if partial:
        payload.update(
            {
                "completion_kind": "partial",
                "resume_stage": "fetch",
                "checkpoint_ref": f"t060:{command.job_id}:checkpoint-1",
                "failures": [
                    {
                        "source_id": w2_payload["source_id"],
                        "stage": "fetch",
                        "code": "T060_SYNTHETIC_INTERRUPTION",
                        "missing_sections": [],
                        "impact": "재시작 검증을 위한 합성 중단입니다.",
                        "core_decision_revision": 1,
                    }
                ],
            }
        )
    return {
        "schema_version": "w1.private.v1",
        "message_id": str(message_id),
        "message_type": "w2.collection.result.v1",
        "producer": "w2",
        "occurred_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "visibility_scope": "PRIVATE",
        "channel": "w1.private.w2.collection-result.v1",
        "payload": payload,
    }


def _send_result(*, config: T060Configuration, envelope: dict[str, object]) -> None:
    Boto3SqsPort(region_name=config.aws_region).send_message(
        queue_url=config.result_queue_url,
        body=json.dumps(envelope, separators=(",", ":")),
        message_attributes={},
    )


def _prove_relay_and_duplicate_result(
    *, config: T060Configuration, seed_factory: sessionmaker[Session]
) -> None:
    seeded = _seed_job(session_factory=seed_factory, run_id=config.run_id, scenario="restart")
    crash = _run_child(
        "relay-crash",
        payload={"outbox_id": str(seeded.execution_outbox_id)},
        expected_returncode=75,
    )
    if crash.get("phase") != "relay_after_send_before_mark":
        raise T060ScenarioError("relay crash phase did not reach the expected boundary")
    time.sleep(2)
    recovered = _run_child("relay-drain")
    if recovered.get("published") != 1:
        raise T060ScenarioError("restarted relay did not recover the expired publish lease")
    first = _run_child("job-once")
    second = _run_child("job-once")
    if first.get("acknowledged") != 1 or second.get("duplicate") != 1:
        raise T060ScenarioError("duplicate execution was not acknowledged as one durable claim")

    with seed_factory() as session:
        outbox = session.get(OutboxMessage, seeded.execution_outbox_id)
        command_count = session.scalar(
            select(func.count())
            .select_from(JobCommand)
            .where(
                JobCommand.job_id == seeded.job_id,
                JobCommand.command_type == "W2_SOURCE_COLLECTION",
            )
        )
        if outbox is None or outbox.status != "PUBLISHED" or outbox.attempts != 2:
            raise T060ScenarioError("relay restart did not preserve the canonical outbox")
        if command_count != 1:
            raise T060ScenarioError("duplicate execution created more than one W2 command")

    command = _get_w2_command(session_factory=seed_factory, job_id=seeded.job_id)
    _drain_relay_clean(context="restart scenario W2 command relay did not publish cleanly")
    message_id = uuid5(NAMESPACE_URL, f"{config.run_id}:duplicate-result")
    envelope = _result_envelope(command=command, message_id=message_id)
    _send_result(config=config, envelope=envelope)
    applied = _run_child("result-once")
    if applied.get("acknowledged") != 1 or applied.get("duplicate") != 0:
        raise T060ScenarioError("first result process did not apply the result")
    _send_result(config=config, envelope=envelope)
    duplicate = _run_child("result-once")
    if duplicate.get("acknowledged") != 1 or duplicate.get("duplicate") != 1:
        raise T060ScenarioError("restarted result worker did not deduplicate the result")
    with seed_factory() as session:
        receipt = session.get(InboxReceipt, ("w1.collection-result", message_id))
        job = session.get(Job, seeded.job_id)
        if receipt is None or receipt.outcome_code != "APPLIED":
            raise T060ScenarioError("result inbox receipt did not retain the APPLIED outcome")
        if job is None or job.status != "SUCCEEDED" or job.active_lease_id is not None:
            raise T060ScenarioError("duplicate-result Job did not finish exactly once")


def _prove_cancel_and_late_result(
    *, config: T060Configuration, seed_factory: sessionmaker[Session]
) -> None:
    seeded = _seed_job(session_factory=seed_factory, run_id=config.run_id, scenario="cancel")
    command = _dispatch_job(config=config, seed_factory=seed_factory, seeded=seeded)
    envelope = _result_envelope(
        command=command,
        message_id=uuid5(NAMESPACE_URL, f"{config.run_id}:late-cancel-result"),
    )
    with seed_factory.begin() as session:
        JobService(session).request_cancellation(
            owner_user_id=seeded.owner_id,
            job_id=seeded.job_id,
        )
    _send_result(config=config, envelope=envelope)
    result = _run_child("result-once")
    if result.get("acknowledged") != 1 or result.get("stale_discarded") != 1:
        raise T060ScenarioError("late result was not acknowledged as stale after cancellation")
    with seed_factory() as session:
        job = session.get(Job, seeded.job_id)
        occupied_slots = session.scalar(
            select(func.count())
            .select_from(OwnerExecutionSlot)
            .where(
                OwnerExecutionSlot.owner_user_id == seeded.owner_id,
                OwnerExecutionSlot.lease_id.is_not(None),
            )
        )
        if job is None or job.status != "CANCELLED" or job.active_lease_id is not None:
            raise T060ScenarioError("cancelled Job retained an active lease")
        if occupied_slots != 0:
            raise T060ScenarioError("cancelled Job retained its owner execution slot")


def _prove_expired_lease_recovery(
    *, config: T060Configuration, seed_factory: sessionmaker[Session]
) -> None:
    seeded = _seed_job(session_factory=seed_factory, run_id=config.run_id, scenario="lease")
    _dispatch_job(config=config, seed_factory=seed_factory, seeded=seeded)
    with seed_factory.begin() as session:
        job = session.get(Job, seeded.job_id)
        if job is None or job.active_lease_id is None:
            raise T060ScenarioError("lease recovery fixture has no active lease")
        lease = session.get(JobExecutionLease, job.active_lease_id)
        if lease is None:
            raise T060ScenarioError("lease recovery fixture lease was not found")
        lease.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
    recovered = _run_child("recover-lease")
    if recovered.get("lease_recovered") != 1:
        raise T060ScenarioError("restarted Job worker did not recover exactly one expired lease")
    with seed_factory() as session:
        job = session.get(Job, seeded.job_id)
        if (
            job is None
            or job.status != "FAILED_RETRYABLE"
            or job.active_lease_id is not None
            or job.execution_fence != 2
        ):
            raise T060ScenarioError("expired lease did not fence and release the Job")


def _prove_checkpoint_resume(
    *, config: T060Configuration, seed_factory: sessionmaker[Session]
) -> None:
    seeded = _seed_job(session_factory=seed_factory, run_id=config.run_id, scenario="checkpoint")
    command = _dispatch_job(config=config, seed_factory=seed_factory, seeded=seeded)
    _send_result(
        config=config,
        envelope=_result_envelope(
            command=command,
            message_id=uuid5(NAMESPACE_URL, f"{config.run_id}:partial-result"),
            partial=True,
        ),
    )
    applied = _run_child("result-once")
    if applied.get("acknowledged") != 1 or applied.get("stale_discarded") != 0:
        raise T060ScenarioError("partial result was not applied by the restarted result worker")
    with seed_factory.begin() as session:
        job = session.get(Job, seeded.job_id)
        checkpoint = session.scalar(
            select(JobCheckpoint)
            .where(JobCheckpoint.job_id == seeded.job_id)
            .order_by(JobCheckpoint.checkpoint_revision.desc())
            .limit(1)
        )
        action = session.scalar(
            select(JobRequiredAction).where(
                JobRequiredAction.job_id == seeded.job_id,
                JobRequiredAction.action_code == "RETRY",
                JobRequiredAction.action_status == "OPEN",
            )
        )
        if job is None or checkpoint is None or action is None:
            raise T060ScenarioError("partial result did not create a retryable checkpoint")
        if (
            checkpoint.execution_fence != job.execution_fence
            or checkpoint.owner_deletion_epoch != job.owner_deletion_epoch
            or checkpoint.analysis_input_version != job.analysis_input_version
        ):
            raise T060ScenarioError("checkpoint does not match the current fence, epoch, and input")
        retried = JobService(session).retry_job(
            owner_user_id=seeded.owner_id,
            job_id=seeded.job_id,
            idempotency_key=f"{config.run_id}-checkpoint-retry",
            request_hash=f"{config.run_id}-checkpoint-retry",
            checkpoint_id=checkpoint.id,
        )
        if retried.command is None:
            raise T060ScenarioError("explicit checkpoint retry did not create a command")
        if retried.command.payload.get("checkpoint_id") != str(checkpoint.id):
            raise T060ScenarioError("retry command does not reference the current checkpoint")
        if retried.job.execution_fence != checkpoint.execution_fence + 1:
            raise T060ScenarioError("checkpoint retry did not advance the execution fence")


def run() -> dict[str, str]:
    config = _require_configuration()
    seed_engine, worker_engine, seed_factory, worker_factory = _factories(config)
    aws_session = boto3.session.Session(region_name=config.aws_region)
    sqs_client = aws_session.client("sqs")
    try:
        verify_t060_isolation(
            config=build_t060_isolation_preflight_config(
                worker_database_url=config.worker_database_url,
                execution_queue_url=config.execution_queue_url,
                command_queue_url=config.command_queue_url,
                result_queue_url=config.result_queue_url,
                run_id=config.run_id,
                aws_region=config.aws_region,
            ),
            session_factory=worker_factory,
            sqs_client=sqs_client,
        )
        _assert_empty_environment(
            config=config,
            seed_factory=seed_factory,
            sqs_client=sqs_client,
        )
        _prove_relay_and_duplicate_result(config=config, seed_factory=seed_factory)
        _prove_cancel_and_late_result(config=config, seed_factory=seed_factory)
        _prove_expired_lease_recovery(config=config, seed_factory=seed_factory)
        _prove_checkpoint_resume(config=config, seed_factory=seed_factory)
    finally:
        seed_engine.dispose()
        worker_engine.dispose()
    return {
        "status": "ok",
        "run_id": config.run_id,
        "relay_restart": "same_message_republished_one_w2_transition",
        "result_restart": "applied_once_then_duplicate",
        "checkpoint": "current_binding_retried_with_new_fence",
        "cancel_recovery": "late_stale_and_slots_released",
        "lease_recovery": "expired_lease_fenced_and_released",
    }


def _run_phase(phase: str) -> None:
    config = _require_configuration()
    if phase == "relay-crash":
        _child_relay_crash(config)
        return
    handlers = {
        "relay-drain": _child_relay_drain,
        "job-once": _child_job_once,
        "recover-lease": _child_recover_lease,
        "result-once": _child_result_once,
    }
    try:
        handler = handlers[phase]
    except KeyError as error:
        raise T060ScenarioError("unsupported T060 child phase") from error
    print(json.dumps(handler(config), separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=("relay-crash", "relay-drain", "job-once", "recover-lease", "result-once"),
    )
    args = parser.parse_args()
    if args.phase:
        _run_phase(args.phase)
        return
    print(json.dumps(run(), separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (T060IsolationPreflightError, T060ScenarioError) as error:
        raise SystemExit(str(error)) from error
