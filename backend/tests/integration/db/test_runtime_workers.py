from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.identity import User
from app.models.jobs import (
    Job,
    JobCommand,
    JobCoreDecisionBinding,
    JobExecutionLease,
    JobRequiredAction,
    OutboxMessage,
)
from app.models.lifecycle_operations import JobCheckpoint
from app.models.sources import AnalysisSourceDecision, JobSourceLink, Source
from app.runtime.lookup_adapter import LookupRequest, _lookup_command, create_lookup_app
from app.runtime.outbox_relay import OutboxRelay, QueueUrlRegistry
from app.runtime.sqs import InMemorySqsPort
from app.runtime.workers import CollectionResultWorker, JobWorker
from app.services.application_workspace import ApplicationWorkspaceService
from app.services.core_decision_inbound import CoreDecisionInboundService
from app.services.direct_source_registration import DirectSourceRegistrationService
from app.services.jobs import JobService
from app.services.lifecycle_operations import LifecycleOperationsService

EXECUTION_QUEUE_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/w1-execution"
W2_COMMAND_QUEUE_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/w2-command"
W2_RESULT_QUEUE_URL = "https://sqs.ap-northeast-2.amazonaws.com/123/w2-result"
BACKEND_ROOT = Path(__file__).parents[3]
RUNTIME_PRIVILEGES_SQL = BACKEND_ROOT / "infra" / "postgres" / "runtime_privileges.sql"


@pytest.fixture(autouse=True)
def clean_runtime_worker_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
        connection.execute(text("TRUNCATE companies CASCADE"))
    yield


def _factory(migrated_engine: Engine) -> sessionmaker:
    return sessionmaker(bind=migrated_engine, autoflush=False, expire_on_commit=False)


def _seed_dispatchable_job(
    db_session: Session,
    *,
    key: str,
    include_core_pin: bool = True,
) -> tuple[UUID, UUID, UUID, UUID]:
    owner = User(display_name=f"Runtime worker {key}", locale="ko-KR", timezone="Asia/Seoul")
    db_session.add(owner)
    db_session.flush()
    company = ApplicationWorkspaceService(db_session).create_company(
        legal_name=f"EPICK {key}", display_name=f"EPICK {key}"
    )
    source = Source(
        company_id=company.id,
        source_type="CAREERS",
        canonical_url=f"https://example.test/{key}",
        canonical_url_hash=f"hash-{key}",
        url_normalization_version="v1",
        policy_version="policy-v1",
        policy_checked_at=datetime.now(UTC),
    )
    db_session.add(source)
    db_session.flush()
    decision = AnalysisSourceDecision(
        decision_scope="COMPANY_KNOWLEDGE",
        company_id=company.id,
        question_version_id=None,
        source_id=source.id,
        source_version_id=None,
        analysis_input_version="company-input:v1",
        decision_version=1,
        decision_code="CORE_REQUIRED",
        decision_owner="W3",
        reason_code="REQUESTED_ANALYSIS_REQUIRED",
    )
    db_session.add(decision)
    accepted = JobService(db_session).accept_job(
        owner_user_id=owner.id,
        job_type="SOURCE_COLLECTION",
        idempotency_key=f"runtime-{key}",
        request_hash=f"runtime-{key}",
        analysis_input_version="company-input:v1",
    )
    assert accepted.command is not None
    assert accepted.outbox_message is not None
    db_session.flush()
    db_session.add(
        JobSourceLink(
            job_id=accepted.job.id,
            owner_user_id=owner.id,
            source_id=source.id,
            source_version_id=None,
            command_id=None,
            purpose_ref="SOURCE_COLLECTION",
            analysis_input_version="company-input:v1",
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
                "analysis_input_version": "company-input:v1",
                "decision_version": 1,
                "is_core": True,
                "decision_code": "CORE_REQUIRED",
                "reason_code": "REQUESTED_ANALYSIS_REQUIRED",
            },
        }
    db_session.flush()
    return owner.id, accepted.job.id, accepted.command.id, accepted.outbox_message.id


def _seed_direct_source_registration(
    db_session: Session,
    *,
    key: str,
) -> tuple[UUID, UUID, UUID, UUID, UUID]:
    owner = User(display_name=f"Direct registration {key}", locale="ko-KR", timezone="Asia/Seoul")
    db_session.add(owner)
    db_session.flush()
    company = ApplicationWorkspaceService(db_session).create_company(
        legal_name=f"Direct EPICK {key}", display_name=f"Direct EPICK {key}"
    )
    source = Source(
        company_id=company.id,
        source_type="CAREERS",
        canonical_url=f"https://example.test/direct/{key}",
        canonical_url_hash=f"direct-hash-{key}",
        url_normalization_version="v1",
        policy_version="policy-v1",
        policy_checked_at=datetime.now(UTC),
    )
    db_session.add(source)
    db_session.flush()
    accepted = DirectSourceRegistrationService(db_session).accept(
        owner_user_id=owner.id,
        company_id=company.id,
        source_id=source.id,
        idempotency_key=f"direct-registration-{key}",
        request_hash=f"direct-registration-{key}",
    )
    assert accepted.command.id is not None
    assert accepted.job.id is not None
    db_session.flush()
    return owner.id, accepted.job.id, accepted.command.id, source.id, accepted.decision.id


def _relay(migrated_engine: Engine, sqs: InMemorySqsPort) -> OutboxRelay:
    return OutboxRelay(
        session_factory=_factory(migrated_engine),
        sqs=sqs,
        queues=QueueUrlRegistry(
            w1_execution_queue_url=EXECUTION_QUEUE_URL,
            w2_collection_command_queue_url=W2_COMMAND_QUEUE_URL,
        ),
        relay_id="runtime-worker-test-relay",
    )


def _run_execution_to_w2_dispatch(
    migrated_engine: Engine,
    db_session: Session,
    *,
    key: str,
) -> tuple[InMemorySqsPort, UUID, UUID]:
    _, job_id, _, _ = _seed_dispatchable_job(db_session, key=key)
    db_session.commit()
    relay_sqs = InMemorySqsPort()
    assert _relay(migrated_engine, relay_sqs).drain_once(limit=10).published == 1
    execution_body = relay_sqs.sent_messages[0].body
    worker_sqs = InMemorySqsPort()
    worker_sqs.inject_message(queue_url=EXECUTION_QUEUE_URL, body=execution_body)
    worker = JobWorker(
        session_factory=_factory(migrated_engine),
        sqs=worker_sqs,
        execution_queue_url=EXECUTION_QUEUE_URL,
        worker_id="runtime-worker-test",
    )
    result = worker.drain_once()
    assert result.acknowledged == 1
    assert result.retry_scheduled == 0
    assert worker_sqs.visibility_extensions
    db_session.expire_all()
    w2_command = db_session.scalar(
        select(JobCommand)
        .where(JobCommand.job_id == job_id, JobCommand.command_type == "W2_SOURCE_COLLECTION")
        .limit(1)
    )
    assert w2_command is not None
    assert w2_command.payload["w2_command"]["resume_stage"] == "policy"
    assert w2_command.payload["w2_command"]["policy_revision"] is None
    return relay_sqs, job_id, w2_command.id


def _run_direct_registration_to_w2_dispatch(
    migrated_engine: Engine,
    db_session: Session,
    *,
    key: str,
) -> tuple[InMemorySqsPort, UUID, UUID]:
    _, job_id, _, _, _ = _seed_direct_source_registration(db_session, key=key)
    db_session.commit()
    relay_sqs = InMemorySqsPort()
    assert _relay(migrated_engine, relay_sqs).drain_once(limit=10).published == 1
    worker_sqs = InMemorySqsPort()
    worker_sqs.inject_message(queue_url=EXECUTION_QUEUE_URL, body=relay_sqs.sent_messages[0].body)
    worker = JobWorker(
        session_factory=_factory(migrated_engine),
        sqs=worker_sqs,
        execution_queue_url=EXECUTION_QUEUE_URL,
        worker_id="direct-registration-test-worker",
    )
    result = worker.drain_once()
    assert result.acknowledged == 1
    assert result.retry_scheduled == 0
    db_session.expire_all()
    w2_command = db_session.scalar(
        select(JobCommand)
        .where(
            JobCommand.job_id == job_id,
            JobCommand.command_type == "W2_DIRECT_SOURCE_REGISTRATION",
        )
        .limit(1)
    )
    assert w2_command is not None
    assert w2_command.payload["w2_command"]["resume_stage"] == "policy"
    assert w2_command.payload["w2_command"]["policy_revision"] is None
    return relay_sqs, job_id, w2_command.id


@pytest.mark.postgres
def test_worker_role_can_relay_claim_and_dispatch_with_owner_row_lock(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    """Exercise the real T059 failure path under epick_worker, not the DB owner."""

    with migrated_engine.begin() as connection:
        connection.execute(text(RUNTIME_PRIVILEGES_SQL.read_text(encoding="utf-8")))

    _, job_id, _, _ = _seed_dispatchable_job(db_session, key="worker-role-lock")
    db_session.commit()

    worker_engine = create_engine(migrated_engine.url, pool_pre_ping=True)

    @event.listens_for(worker_engine, "begin")
    def _set_worker_role(connection) -> None:
        connection.exec_driver_sql("SET LOCAL ROLE epick_worker")

    worker_factory = sessionmaker(
        bind=worker_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    sqs = InMemorySqsPort()
    try:
        relay_result = OutboxRelay(
            session_factory=worker_factory,
            sqs=sqs,
            queues=QueueUrlRegistry(
                w1_execution_queue_url=EXECUTION_QUEUE_URL,
                w2_collection_command_queue_url=W2_COMMAND_QUEUE_URL,
            ),
            relay_id="worker-role-lock-relay",
        ).drain_once(limit=1)
        assert relay_result.published == 1

        execution_body = sqs.sent_messages[0].body
        sqs.inject_message(queue_url=EXECUTION_QUEUE_URL, body=execution_body)
        result = JobWorker(
            session_factory=worker_factory,
            sqs=sqs,
            execution_queue_url=EXECUTION_QUEUE_URL,
            worker_id="worker-role-lock-worker",
        ).drain_once(max_messages=1)

        assert result.acknowledged == 1
        assert result.retry_scheduled == 0
        with migrated_engine.connect() as connection:
            assert connection.scalar(
                select(func.count())
                .select_from(JobCommand)
                .where(
                    JobCommand.job_id == job_id,
                    JobCommand.command_type == "W2_SOURCE_COLLECTION",
                )
            ) == 1
    finally:
        worker_engine.dispose()


def _w2_result_envelope(
    *, command: JobCommand, message_id: UUID | None = None
) -> dict[str, object]:
    w2_payload = command.payload["w2_command"]
    assert isinstance(w2_payload, dict)
    return {
        "schema_version": "w1.private.v1",
        "message_id": str(message_id or uuid4()),
        "message_type": "w2.collection.result.v1",
        "producer": "w2",
        "occurred_at": "2026-09-16T01:00:00Z",
        "visibility_scope": "PRIVATE",
        "channel": "w1.private.w2.collection-result.v1",
        "payload": {
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
            "message_ko": "수집을 완료했습니다.",
            "source_id": w2_payload["source_id"],
            "policy_revision": 1,
            "required_actions": [],
        },
    }


def _lookup_headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer test-token",
        "X-EPICK-Service-Principal": "w2",
    }


@pytest.mark.postgres
def test_job_worker_creates_one_w2_outbox_and_lookup_uses_only_private_adapter(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    relay_sqs, job_id, w2_command_id = _run_execution_to_w2_dispatch(
        migrated_engine, db_session, key="dispatch"
    )
    result = _relay(migrated_engine, relay_sqs).drain_once(limit=10)
    assert result.published == 1
    w2_delivery = json.loads(relay_sqs.sent_messages[-1].body)
    assert w2_delivery["message_type"] == "w1.private.w2.collection-command.v1"
    assert w2_delivery["message_id"] == w2_delivery["payload"]["command_id"] == str(w2_command_id)
    assert "lease_id" not in w2_delivery
    assert "token" not in w2_delivery
    pin = w2_delivery["core_decision_pin"]
    payload = w2_delivery["payload"]
    assert isinstance(pin, dict) and isinstance(payload, dict)
    assert payload["source_id"] == pin["source_id"]
    assert payload["input_version"] == pin["decision_version"]
    assert payload["core_source_decision"] == {
        "is_core": True,
        "decided_by": "W3",
        "rationale": pin["reason_code"],
        "decision_revision": pin["decision_version"],
        "analysis_input_version": pin["decision_version"],
    }

    db_session.expire_all()
    job = db_session.get(Job, job_id)
    command = db_session.get(JobCommand, w2_command_id)
    assert job is not None and job.status == "RUNNING" and job.active_lease_id is not None
    assert command is not None and command.status == "ENQUEUED"

    lookup = TestClient(
        create_lookup_app(
            session_factory=_factory(migrated_engine),
            expected_bearer_token="test-token",
        )
    )
    body = {
        "schema_version": "w1.private.command-lookup.v1",
        "command_id": str(w2_command_id),
        "execution_fence": command.execution_fence,
        "owner_deletion_epoch": command.owner_deletion_epoch,
    }
    unauthenticated = lookup.post("/internal/v1/job-commands/lookup", json=body)
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["code"] == "UNAUTHENTICATED_SERVICE_PRINCIPAL"
    forbidden = lookup.post(
        "/internal/v1/job-commands/lookup",
        json=body,
        headers={
            "Authorization": "Bearer test-token",
            "X-EPICK-Service-Principal": "w3",
        },
    )
    assert forbidden.status_code == 403
    available = lookup.post(
        "/internal/v1/job-commands/lookup",
        json=body,
        headers=_lookup_headers(),
    )
    assert available.status_code == 200
    assert available.json()["status"] == "AVAILABLE"
    assert available.json()["command"]["command_id"] == str(w2_command_id)
    assert available.json()["command"] == payload


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_id", "00000000-0000-4000-8000-000000000099"),
        ("is_core", False),
        ("decision_version", 2),
    ],
)
def test_ct14_blocks_pin_db_mismatch_before_creating_a_w2_command(
    migrated_engine: Engine,
    db_session: Session,
    field: str,
    value: object,
) -> None:
    _, job_id, execution_command_id, _ = _seed_dispatchable_job(db_session, key=f"ct14-{field}")
    execution_command = db_session.get(JobCommand, execution_command_id)
    assert execution_command is not None
    payload = deepcopy(execution_command.payload)
    pin = payload["core_decision_pin"]
    assert isinstance(pin, dict)
    pin[field] = value
    execution_command.payload = payload
    db_session.commit()

    relay_sqs = InMemorySqsPort()
    assert _relay(migrated_engine, relay_sqs).drain_once(limit=10).published == 1
    worker_sqs = InMemorySqsPort()
    worker_sqs.inject_message(queue_url=EXECUTION_QUEUE_URL, body=relay_sqs.sent_messages[0].body)
    result = JobWorker(
        session_factory=_factory(migrated_engine),
        sqs=worker_sqs,
        execution_queue_url=EXECUTION_QUEUE_URL,
        worker_id=f"ct14-worker-{field}",
    ).drain_once()

    assert result.acknowledged == 1
    db_session.expire_all()
    job = db_session.get(Job, job_id)
    assert job is not None and job.status == "WAITING_USER" and job.active_lease_id is None
    assert db_session.scalar(
        select(JobCommand).where(
            JobCommand.job_id == job_id,
            JobCommand.command_type == "W2_SOURCE_COLLECTION",
        )
    ) is None
    assert len(relay_sqs.sent_messages) == 1


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("source_id",), "00000000-0000-4000-8000-000000000099"),
        (("core_source_decision", "is_core"), False),
        (("core_source_decision", "decision_revision"), 2),
        (("core_source_decision", "decided_by"), "W4"),
        (("core_source_decision", "decided_by"), "w3"),
    ],
)
def test_ct14_relay_and_lookup_reject_payload_mismatches_before_w2_execution(
    migrated_engine: Engine,
    db_session: Session,
    path: tuple[str, ...],
    value: object,
) -> None:
    relay_sqs, job_id, w2_command_id = _run_execution_to_w2_dispatch(
        migrated_engine, db_session, key="ct14-relay-" + "-".join(path)
    )
    command = db_session.get(JobCommand, w2_command_id)
    assert command is not None
    command_payload = deepcopy(command.payload)
    w2_command = command_payload["w2_command"]
    assert isinstance(w2_command, dict)
    target: dict[str, object] = w2_command
    for key in path[:-1]:
        nested = target[key]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = value
    command.payload = command_payload
    db_session.commit()

    lookup = TestClient(
        create_lookup_app(
            session_factory=_factory(migrated_engine),
            expected_bearer_token="test-token",
        )
    )
    lookup_response = lookup.post(
        "/internal/v1/job-commands/lookup",
        json={
            "schema_version": "w1.private.command-lookup.v1",
            "command_id": str(w2_command_id),
            "execution_fence": command.execution_fence,
            "owner_deletion_epoch": command.owner_deletion_epoch,
        },
        headers=_lookup_headers(),
    )
    assert lookup_response.status_code == 200
    assert lookup_response.json() == {
        "schema_version": "w1.private.command-lookup.v1",
        "command_id": str(w2_command_id),
        "status": "EXPIRED",
        "reason_code": "COMMAND_BINDING_INVALID",
        "command": None,
    }

    result = _relay(migrated_engine, relay_sqs).drain_once(limit=10)
    assert result.failed_final == 1
    assert len(relay_sqs.sent_messages) == 1
    db_session.expire_all()
    outbox = db_session.scalar(
        select(OutboxMessage).where(OutboxMessage.command_id == w2_command_id)
    )
    command = db_session.get(JobCommand, w2_command_id)
    job = db_session.get(Job, job_id)
    assert outbox is not None and outbox.status == "FAILED_FINAL"
    assert outbox.last_error_code == "OUTBOX_W2_DECISION_BINDING_MISMATCH"
    assert command is not None and command.status == "FAILED"
    assert job is not None and job.dispatch_status == "BLOCKED"


@pytest.mark.postgres
def test_direct_source_registration_uses_its_own_w1_dispatch_and_lookup_contract(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    relay_sqs, job_id, w2_command_id = _run_direct_registration_to_w2_dispatch(
        migrated_engine, db_session, key="normal"
    )
    assert _relay(migrated_engine, relay_sqs).drain_once(limit=10).published == 1
    delivery = json.loads(relay_sqs.sent_messages[-1].body)
    assert delivery["message_type"] == "w1.private.w2.direct-source-registration.v1"
    assert delivery["message_id"] == delivery["payload"]["command_id"] == str(w2_command_id)
    pin = delivery["direct_source_registration_pin"]
    payload = delivery["payload"]
    assert pin["decision_scope"] == "DIRECT_SOURCE_REGISTRATION"
    assert pin["is_core"] is False
    assert pin["decision_owner"] == "W1"
    assert payload["company_id"] == pin["company_id"]
    assert payload["source_id"] == pin["source_id"]
    assert payload["input_version"] == pin["decision_version"]
    assert payload["core_source_decision"] == {
        "is_core": False,
        "decided_by": "W1",
        "rationale": pin["reason_code"],
        "decision_revision": pin["decision_version"],
        "analysis_input_version": pin["decision_version"],
    }

    db_session.expire_all()
    command = db_session.get(JobCommand, w2_command_id)
    job = db_session.get(Job, job_id)
    assert command is not None and job is not None
    lookup = TestClient(
        create_lookup_app(
            session_factory=_factory(migrated_engine),
            expected_bearer_token="test-token",
        )
    )
    available = lookup.post(
        "/internal/v1/job-commands/lookup",
        json={
            "schema_version": "w1.private.command-lookup.v1",
            "command_id": str(command.id),
            "execution_fence": command.execution_fence,
            "owner_deletion_epoch": command.owner_deletion_epoch,
        },
        headers=_lookup_headers(),
    )
    assert available.status_code == 200
    assert available.json()["status"] == "AVAILABLE"
    assert available.json()["command"] == payload


@pytest.mark.postgres
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("is_core", True),
        ("decision_owner", "W2"),
        ("decision_scope", "COMPANY_KNOWLEDGE"),
    ],
)
def test_ct13_rejects_client_or_w2_injected_direct_registration_pin_values_before_w2_dispatch(
    migrated_engine: Engine,
    db_session: Session,
    field: str,
    value: object,
) -> None:
    _, job_id, command_id, _, _ = _seed_direct_source_registration(db_session, key="injected")
    command = db_session.get(JobCommand, command_id)
    assert command is not None
    payload = dict(command.payload)
    pin = dict(payload["direct_source_registration_pin"])
    pin[field] = value
    payload["direct_source_registration_pin"] = pin
    command.payload = payload
    db_session.commit()

    relay_sqs = InMemorySqsPort()
    assert _relay(migrated_engine, relay_sqs).drain_once(limit=10).published == 1
    worker_sqs = InMemorySqsPort()
    worker_sqs.inject_message(queue_url=EXECUTION_QUEUE_URL, body=relay_sqs.sent_messages[0].body)
    worker = JobWorker(
        session_factory=_factory(migrated_engine),
        sqs=worker_sqs,
        execution_queue_url=EXECUTION_QUEUE_URL,
        worker_id="direct-registration-test-worker",
    )
    assert worker.drain_once().acknowledged == 1
    db_session.expire_all()
    job = db_session.get(Job, job_id)
    assert job is not None and job.status == "WAITING_USER" and job.active_lease_id is None
    assert db_session.scalar(
        select(JobCommand).where(
            JobCommand.job_id == job_id,
            JobCommand.command_type == "W2_DIRECT_SOURCE_REGISTRATION",
        )
    ) is None


@pytest.mark.postgres
def test_ct13_rejects_direct_registration_exception_on_an_analysis_job(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    _, job_id, command_id, _, _ = _seed_direct_source_registration(db_session, key="analysis")
    job = db_session.get(Job, job_id)
    assert job is not None
    job.job_type = "SOURCE_COLLECTION"
    db_session.commit()

    relay_sqs = InMemorySqsPort()
    assert _relay(migrated_engine, relay_sqs).drain_once(limit=10).published == 1
    worker_sqs = InMemorySqsPort()
    worker_sqs.inject_message(queue_url=EXECUTION_QUEUE_URL, body=relay_sqs.sent_messages[0].body)
    worker = JobWorker(
        session_factory=_factory(migrated_engine),
        sqs=worker_sqs,
        execution_queue_url=EXECUTION_QUEUE_URL,
        worker_id="direct-registration-test-worker",
    )
    assert worker.drain_once().acknowledged == 1
    db_session.expire_all()
    job = db_session.get(Job, job_id)
    assert job is not None and job.status == "WAITING_USER" and job.active_lease_id is None
    assert db_session.scalar(
        select(JobCommand).where(
            JobCommand.job_id == job_id,
            JobCommand.command_type == "W2_DIRECT_SOURCE_REGISTRATION",
        )
    ) is None


@pytest.mark.postgres
def test_w2_result_is_not_lost_between_broker_send_and_relay_publish_mark(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    relay_sqs, job_id, w2_command_id = _run_execution_to_w2_dispatch(
        migrated_engine, db_session, key="post-send-race"
    )
    relay = _relay(migrated_engine, relay_sqs)
    batch = relay.claim_due(limit=10)
    assert len(batch.claims) == 1
    claim = batch.claims[0]
    # Simulate the broker accepting the command before the relay starts its post-send DB commit.
    relay_sqs.send_message(
        queue_url=claim.queue_url,
        body=claim.body,
        message_attributes=claim.message_attributes,
    )
    db_session.expire_all()
    command = db_session.get(JobCommand, w2_command_id)
    assert command is not None and command.status == "PENDING"

    lookup = TestClient(
        create_lookup_app(
            session_factory=_factory(migrated_engine),
            expected_bearer_token="test-token",
        )
    )
    available = lookup.post(
        "/internal/v1/job-commands/lookup",
        json={
            "schema_version": "w1.private.command-lookup.v1",
            "command_id": str(command.id),
            "execution_fence": command.execution_fence,
            "owner_deletion_epoch": command.owner_deletion_epoch,
        },
        headers=_lookup_headers(),
    )
    assert available.status_code == 200
    assert available.json()["status"] == "AVAILABLE"

    result_sqs = InMemorySqsPort()
    result_sqs.inject_message(
        queue_url=W2_RESULT_QUEUE_URL,
        body=json.dumps(_w2_result_envelope(command=command)),
    )
    result_worker = CollectionResultWorker(
        session_factory=_factory(migrated_engine),
        sqs=result_sqs,
        result_queue_url=W2_RESULT_QUEUE_URL,
    )
    assert result_worker.drain_once().acknowledged == 1
    assert relay._mark_published(claim=claim)

    db_session.expire_all()
    job = db_session.get(Job, job_id)
    command = db_session.get(JobCommand, w2_command_id)
    outbox = db_session.get(OutboxMessage, claim.outbox_id)
    assert job is not None and job.status == "SUCCEEDED" and job.active_lease_id is None
    assert command is not None and command.status == "CONSUMED"
    assert outbox is not None and outbox.status == "PUBLISHED"


@pytest.mark.postgres
def test_lookup_returns_every_semantic_status_without_exposing_private_command_on_errors(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    relay_sqs, _, w2_command_id = _run_execution_to_w2_dispatch(
        migrated_engine, db_session, key="lookup-status"
    )
    assert _relay(migrated_engine, relay_sqs).drain_once(limit=10).published == 1
    db_session.expire_all()
    command = db_session.get(JobCommand, w2_command_id)
    assert command is not None
    job = db_session.get(Job, command.job_id)
    owner = db_session.get(User, command.owner_user_id)
    assert job is not None and owner is not None
    lookup = TestClient(
        create_lookup_app(
            session_factory=_factory(migrated_engine),
            expected_bearer_token="test-token",
        )
    )

    def request(*, fence: int = command.execution_fence, epoch: int = command.owner_deletion_epoch):
        return lookup.post(
            "/internal/v1/job-commands/lookup",
            json={
                "schema_version": "w1.private.command-lookup.v1",
                "command_id": str(command.id),
                "execution_fence": fence,
                "owner_deletion_epoch": epoch,
            },
            headers=_lookup_headers(),
        )

    assert request(fence=command.execution_fence + 1).json()["status"] == "STALE_FENCE"
    assert (
        request(epoch=command.owner_deletion_epoch + 1).json()["status"]
        == "STALE_DELETION_EPOCH"
    )
    assert request().json()["status"] == "AVAILABLE"

    command.status = "INVALIDATED"
    db_session.commit()
    assert request().json()["status"] == "INVALIDATED"
    command.status = "CONSUMED"
    db_session.commit()
    assert request().json()["status"] == "EXPIRED"
    owner.account_status = "DELETED"
    db_session.commit()
    assert request().json()["status"] == "DELETED"
    not_found = lookup.post(
        "/internal/v1/job-commands/lookup",
        json={
            "schema_version": "w1.private.command-lookup.v1",
            "command_id": str(uuid4()),
            "execution_fence": 1,
            "owner_deletion_epoch": 0,
        },
        headers=_lookup_headers(),
    )
    assert not_found.status_code == 200
    assert not_found.json()["status"] == "NOT_FOUND"
    invalid_request = lookup.post(
        "/internal/v1/job-commands/lookup",
        json={"schema_version": "wrong"},
        headers=_lookup_headers(),
    )
    assert invalid_request.status_code == 422
    assert invalid_request.json()["code"] == "INVALID_LOOKUP_REQUEST"
    assert "command" not in invalid_request.json()


@pytest.mark.postgres
def test_lookup_adapter_works_with_the_real_column_limited_lookup_role(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    relay_sqs, _, w2_command_id = _run_execution_to_w2_dispatch(
        migrated_engine, db_session, key="lookup-role"
    )
    assert _relay(migrated_engine, relay_sqs).drain_once(limit=10).published == 1
    db_session.expire_all()
    command = db_session.get(JobCommand, w2_command_id)
    assert command is not None
    with migrated_engine.begin() as connection:
        connection.execute(text(RUNTIME_PRIVILEGES_SQL.read_text(encoding="utf-8")))

    lookup_engine = create_engine(
        migrated_engine.url.render_as_string(hide_password=False),
        pool_size=1,
        max_overflow=0,
    )
    lookup_sessions = sessionmaker(
        bind=lookup_engine,
        autoflush=False,
        expire_on_commit=False,
    )

    @event.listens_for(lookup_engine, "checkout")
    def set_lookup_role(dbapi_connection, connection_record, connection_proxy) -> None:
        del connection_record, connection_proxy
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET ROLE epick_lookup")

    try:
        request = LookupRequest(
            schema_version="w1.private.command-lookup.v1",
            command_id=command.id,
            execution_fence=command.execution_fence,
            owner_deletion_epoch=command.owner_deletion_epoch,
        )
        with lookup_sessions.begin() as session:
            assert _lookup_command(session=session, request=request).status == "AVAILABLE"
        lookup = TestClient(
            create_lookup_app(
                session_factory=lookup_sessions,
                expected_bearer_token="test-token",
            )
        )
        response = lookup.post(
            "/internal/v1/job-commands/lookup",
            json={
                "schema_version": "w1.private.command-lookup.v1",
                "command_id": str(command.id),
                "execution_fence": command.execution_fence,
                "owner_deletion_epoch": command.owner_deletion_epoch,
            },
            headers=_lookup_headers(),
        )
    finally:
        lookup_engine.dispose()
    assert response.status_code == 200
    assert response.json()["status"] == "AVAILABLE"


@pytest.mark.postgres
def test_missing_core_pin_blocks_without_creating_w2_dispatch(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    _, job_id, _, _ = _seed_dispatchable_job(db_session, key="missing-pin", include_core_pin=False)
    db_session.commit()
    relay_sqs = InMemorySqsPort()
    assert _relay(migrated_engine, relay_sqs).drain_once(limit=10).published == 1
    worker_sqs = InMemorySqsPort()
    worker_sqs.inject_message(queue_url=EXECUTION_QUEUE_URL, body=relay_sqs.sent_messages[0].body)
    worker = JobWorker(
        session_factory=_factory(migrated_engine),
        sqs=worker_sqs,
        execution_queue_url=EXECUTION_QUEUE_URL,
        worker_id="runtime-worker-test",
    )
    assert worker.drain_once().acknowledged == 1
    db_session.expire_all()
    job = db_session.get(Job, job_id)
    assert job is not None and job.status == "WAITING_USER" and job.active_lease_id is None
    assert db_session.scalar(
        select(JobRequiredAction).where(
            JobRequiredAction.job_id == job_id,
            JobRequiredAction.action_code == "CORE_DECISION_REQUIRED",
            JobRequiredAction.context_code == "CORE_DECISION_BINDING_MISMATCH",
        )
    )
    assert db_session.scalar(
        select(OutboxMessage).where(
            OutboxMessage.message_type == "w1.private.w2.collection-command.v1"
        )
    ) is None


@pytest.mark.postgres
def test_explicit_retry_after_w3_core_decision_creates_new_fenced_pinned_command(
    db_session: Session,
) -> None:
    owner_id, job_id, command_id, outbox_id = _seed_dispatchable_job(
        db_session,
        key="w3-explicit-retry",
        include_core_pin=False,
    )
    job = db_session.get(Job, job_id)
    command = db_session.get(JobCommand, command_id)
    outbox = db_session.get(OutboxMessage, outbox_id)
    assert job is not None and command is not None and outbox is not None
    source_link = db_session.scalar(select(JobSourceLink).where(JobSourceLink.job_id == job_id))
    assert source_link is not None
    source = db_session.get(Source, source_link.source_id)
    assert source is not None
    job.status = "WAITING_USER"
    job.dispatch_status = "BLOCKED"
    command.status = "CONSUMED"
    command.consumed_at = datetime.now(UTC)
    outbox.status = "PUBLISHED"
    outbox.published_at = datetime.now(UTC)
    missing_action = JobRequiredAction(
        job_id=job.id,
        owner_user_id=owner_id,
        action_code="CORE_DECISION_REQUIRED",
        action_status="OPEN",
        context_code="CORE_DECISION_BINDING_MISMATCH",
        expected_input_version=job.analysis_input_version,
    )
    db_session.add(missing_action)
    db_session.flush()
    original_fence = job.execution_fence

    receipt = CoreDecisionInboundService(db_session).apply(
        body={
            "schema_version": "w3.private.core-decision/0.1-candidate",
            "message_type": "w3.private.w1.core-decision",
            "message_id": str(uuid4()),
            "occurred_at": "2026-09-18T00:00:00Z",
            "visibility_scope": "PRIVATE",
            "producer": "w3",
            "job_id": str(job.id),
            "company_id": str(source.company_id),
            "source_id": str(source.id),
            "analysis_input_version": job.analysis_input_version,
            "decision_scope": "COMPANY_KNOWLEDGE",
            "decision_owner": "W3",
            "question_version_id": None,
            "decision_version": 2,
            "is_core": True,
            "decision_code": "CORE_REQUIRED",
            "reason_code": "REQUIRED_COMPANY_EVIDENCE",
        },
        authenticated_principal="w3",
    )
    retry_action = db_session.scalar(
        select(JobRequiredAction).where(
            JobRequiredAction.job_id == job.id,
            JobRequiredAction.action_code == "RETRY",
            JobRequiredAction.action_status == "OPEN",
        )
    )
    assert retry_action is not None
    assert db_session.scalar(
        select(func.count()).select_from(JobCommand).where(JobCommand.job_id == job.id)
    ) == 1

    accepted = JobService(db_session).apply_required_action(
        owner_user_id=owner_id,
        job_id=job.id,
        required_action_id=retry_action.id,
        action_code="RETRY",
        expected_input_version=retry_action.expected_input_version,
        expected_result_version=retry_action.expected_result_version,
        acknowledge_rate_limit=False,
    )
    db_session.flush()

    assert accepted.command is not None and accepted.outbox_message is not None
    assert accepted.job.status == "QUEUED"
    assert accepted.job.execution_fence == original_fence + 1
    assert accepted.command.execution_fence == accepted.job.execution_fence
    assert accepted.command.analysis_source_decision_id == receipt.decision_id
    binding = db_session.scalar(
        select(JobCoreDecisionBinding).where(
            JobCoreDecisionBinding.analysis_source_decision_id == receipt.decision_id
        )
    )
    assert binding is not None
    pin = accepted.command.payload["core_decision_pin"]
    assert pin["origin_message_id"] == str(binding.origin_message_id)
    assert pin["decision_id"] == str(receipt.decision_id)
    assert pin["decision_version"] == binding.decision_version
    assert accepted.outbox_message.payload["core_decision_pin"] == pin


@pytest.mark.postgres
def test_job_worker_leaves_the_fourth_owner_execution_for_sqs_redelivery(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    owner = User(display_name="Four slot owner", locale="ko-KR", timezone="Asia/Seoul")
    db_session.add(owner)
    db_session.flush()
    service = JobService(db_session)
    accepted = []
    for sequence in range(4):
        acceptance = service.accept_job(
            owner_user_id=owner.id,
            job_type="SOURCE_COLLECTION",
            idempotency_key=f"four-slot-{sequence}",
            request_hash=f"four-slot-{sequence}",
        )
        service.mark_dispatch_enqueued(owner_user_id=owner.id, job_id=acceptance.job.id)
        accepted.append(acceptance)
    for acceptance in accepted[:3]:
        assert service.claim_execution(owner_user_id=owner.id, job_id=acceptance.job.id) is not None
    fourth = accepted[3]
    assert fourth.command is not None and fourth.outbox_message is not None
    db_session.commit()
    delivery_body = {
        "schema_version": "1.0",
        "message_id": str(fourth.command.id),
        "command_id": str(fourth.command.id),
        "job_id": str(fourth.job.id),
        "execution_fence": fourth.command.execution_fence,
        "owner_deletion_epoch": fourth.command.owner_deletion_epoch,
        "issued_at": "2026-09-16T01:00:00Z",
        "payload_ref": str(fourth.outbox_message.id),
    }
    sqs = InMemorySqsPort()
    sqs.inject_message(queue_url=EXECUTION_QUEUE_URL, body=json.dumps(delivery_body))
    worker = JobWorker(
        session_factory=_factory(migrated_engine),
        sqs=sqs,
        execution_queue_url=EXECUTION_QUEUE_URL,
        worker_id="runtime-worker-test",
    )
    run = worker.drain_once()
    assert run.received == run.retry_scheduled == 1
    assert run.acknowledged == 0
    db_session.expire_all()
    job = db_session.get(Job, fourth.job.id)
    assert job is not None and job.status == "QUEUED" and job.active_lease_id is None


@pytest.mark.postgres
@pytest.mark.w1_isolated_commit_gate
def test_result_worker_applies_once_and_late_cancel_result_only_releases_the_slot(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    relay_sqs, job_id, w2_command_id = _run_execution_to_w2_dispatch(
        migrated_engine, db_session, key="result"
    )
    assert _relay(migrated_engine, relay_sqs).drain_once(limit=10).published == 1
    db_session.expire_all()
    command = db_session.get(JobCommand, w2_command_id)
    assert command is not None
    result_message_id = uuid4()
    envelope = _w2_result_envelope(command=command, message_id=result_message_id)
    result_sqs = InMemorySqsPort()
    result_sqs.inject_message(queue_url=W2_RESULT_QUEUE_URL, body=json.dumps(envelope))
    worker = CollectionResultWorker(
        session_factory=_factory(migrated_engine),
        sqs=result_sqs,
        result_queue_url=W2_RESULT_QUEUE_URL,
    )
    assert worker.drain_once().acknowledged == 1
    db_session.expire_all()
    job = db_session.get(Job, job_id)
    assert job is not None and job.status == "SUCCEEDED" and job.active_lease_id is None
    assert db_session.get(JobCommand, w2_command_id).status == "CONSUMED"

    result_sqs.inject_message(queue_url=W2_RESULT_QUEUE_URL, body=json.dumps(envelope))
    duplicate = worker.drain_once()
    assert duplicate.duplicate == duplicate.acknowledged == 1


@pytest.mark.postgres
@pytest.mark.w1_isolated_commit_gate
def test_late_w2_result_after_cancel_is_discarded_but_confirms_slot_release(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    relay_sqs, job_id, w2_command_id = _run_execution_to_w2_dispatch(
        migrated_engine, db_session, key="cancel-race"
    )
    assert _relay(migrated_engine, relay_sqs).drain_once(limit=10).published == 1
    db_session.expire_all()
    job = db_session.get(Job, job_id)
    command = db_session.get(JobCommand, w2_command_id)
    assert job is not None and command is not None
    JobService(db_session).request_cancellation(owner_user_id=job.owner_user_id, job_id=job.id)
    db_session.commit()
    result_sqs = InMemorySqsPort()
    result_sqs.inject_message(
        queue_url=W2_RESULT_QUEUE_URL,
        body=json.dumps(_w2_result_envelope(command=command)),
    )
    result_worker = CollectionResultWorker(
        session_factory=_factory(migrated_engine),
        sqs=result_sqs,
        result_queue_url=W2_RESULT_QUEUE_URL,
    )
    result = result_worker.drain_once()
    assert result.acknowledged == result.stale_discarded == 1
    db_session.expire_all()
    job = db_session.get(Job, job_id)
    assert job is not None and job.status == "CANCELLED" and job.active_lease_id is None


@pytest.mark.postgres
def test_w2_core_partial_result_appends_checkpoint_and_opens_continue_limited_action(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    relay_sqs, job_id, w2_command_id = _run_execution_to_w2_dispatch(
        migrated_engine, db_session, key="checkpoint"
    )
    assert _relay(migrated_engine, relay_sqs).drain_once(limit=10).published == 1
    db_session.expire_all()
    command = db_session.get(JobCommand, w2_command_id)
    assert command is not None
    envelope = _w2_result_envelope(command=command)
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    payload["completion_kind"] = "partial"
    payload["failures"] = [
        {
            "source_id": payload["source_id"],
            "stage": "parse",
            "code": "PARTIAL_REQUIRED_SECTION",
            "missing_sections": ["preferred"],
            "impact": "선호 요건을 모두 추출하지 못했습니다.",
            "core_decision_revision": 1,
        }
    ]
    payload["resume_stage"] = "fetch"
    payload["checkpoint_ref"] = "fetch:checkpoint-0001"
    payload["policy_revision"] = 7
    payload["required_actions"] = [
        {
            "code": "core_failure_decision",
            "label_ko": "결정 필요",
            "context": {
                "source_id": payload["source_id"],
                "core_decision_revision": 1,
                "choices": ["continue_limited", "stop", "retry"],
            },
        }
    ]
    result_sqs = InMemorySqsPort()
    result_sqs.inject_message(queue_url=W2_RESULT_QUEUE_URL, body=json.dumps(envelope))
    result_worker = CollectionResultWorker(
        session_factory=_factory(migrated_engine),
        sqs=result_sqs,
        result_queue_url=W2_RESULT_QUEUE_URL,
    )
    assert result_worker.drain_once().acknowledged == 1
    db_session.expire_all()
    checkpoint = db_session.scalar(
        select(JobCheckpoint).where(JobCheckpoint.job_id == job_id).limit(1)
    )
    job = db_session.get(Job, job_id)
    assert checkpoint is not None
    assert checkpoint.checkpoint_revision == 1
    assert checkpoint.resume_stage == "fetch"
    assert checkpoint.policy_revision == 7
    assert checkpoint.state_ref == "fetch:checkpoint-0001"
    assert job is not None and job.status == "WAITING_USER" and job.active_lease_id is None
    assert db_session.scalar(
        select(JobRequiredAction).where(
            JobRequiredAction.job_id == job_id,
            JobRequiredAction.action_code == "CONTINUE_LIMITED",
        )
    )


@pytest.mark.postgres
@pytest.mark.parametrize("direct", [False, True], ids=["core", "direct"])
def test_w2_fetch_checkpoint_retry_preserves_revision_pin_and_current_fence(
    migrated_engine: Engine,
    db_session: Session,
    direct: bool,
) -> None:
    if direct:
        relay_sqs, job_id, w2_command_id = _run_direct_registration_to_w2_dispatch(
            migrated_engine, db_session, key="fetch-resume-direct"
        )
    else:
        relay_sqs, job_id, w2_command_id = _run_execution_to_w2_dispatch(
            migrated_engine, db_session, key="fetch-resume-core"
        )
    assert _relay(migrated_engine, relay_sqs).drain_once(limit=10).published == 1
    db_session.expire_all()
    first_command = db_session.get(JobCommand, w2_command_id)
    assert first_command is not None
    envelope = _w2_result_envelope(command=first_command)
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    payload["successful_source_refs"] = []
    payload["failures"] = [
        {
            "source_id": payload["source_id"],
            "stage": "fetch",
            "code": "RATE_LIMITED",
            "missing_sections": [],
            "impact": "수집 재시도가 필요합니다.",
            "core_decision_revision": 1,
        }
    ]
    payload["completion_kind"] = "none"
    payload["resume_stage"] = "fetch"
    payload["checkpoint_ref"] = "fetch:checkpoint-0001"
    payload["policy_revision"] = 7
    payload["retry_not_before"] = "2026-09-21T12:00:00Z"
    payload["required_actions"] = [
        {
            "code": "user_retry",
            "label_ko": "재시도",
            "context": {
                "source_id": payload["source_id"],
                "resume_stage": "fetch",
                "retry_not_before": None,
            },
        }
    ]
    result_sqs = InMemorySqsPort()
    result_sqs.inject_message(queue_url=W2_RESULT_QUEUE_URL, body=json.dumps(envelope))
    assert CollectionResultWorker(
        session_factory=_factory(migrated_engine),
        sqs=result_sqs,
        result_queue_url=W2_RESULT_QUEUE_URL,
    ).drain_once().acknowledged == 1
    db_session.expire_all()
    checkpoint = db_session.scalar(
        select(JobCheckpoint).where(JobCheckpoint.job_id == job_id)
    )
    job = db_session.get(Job, job_id)
    assert checkpoint is not None and job is not None
    assert checkpoint.policy_revision == 7
    assert job.status == "PAUSED_RATE_LIMIT"
    first_fence = job.execution_fence
    accepted = LifecycleOperationsService(db_session).resume_rate_limited_job_from_checkpoint(
        owner_user_id=job.owner_user_id,
        job_id=job.id,
        checkpoint_id=checkpoint.id,
        from_stage="fetch",
        idempotency_key="fetch-resume-retry",
        request_hash="f" * 64,
    )
    assert accepted.command is not None
    db_session.commit()
    assert _relay(migrated_engine, relay_sqs).drain_once(limit=10).published == 1
    execution_sqs = InMemorySqsPort()
    execution_sqs.inject_message(
        queue_url=EXECUTION_QUEUE_URL, body=relay_sqs.sent_messages[-1].body
    )
    assert JobWorker(
        session_factory=_factory(migrated_engine),
        sqs=execution_sqs,
        execution_queue_url=EXECUTION_QUEUE_URL,
        worker_id="fetch-resume-worker",
    ).drain_once().acknowledged == 1
    db_session.expire_all()
    resumed = db_session.scalar(
        select(JobCommand)
        .where(
            JobCommand.job_id == job_id,
            JobCommand.command_type == (
                "W2_DIRECT_SOURCE_REGISTRATION" if direct else "W2_SOURCE_COLLECTION"
            ),
        )
        .order_by(JobCommand.command_sequence.desc())
        .limit(1)
    )
    assert resumed is not None and resumed.id != first_command.id
    resumed_payload = resumed.payload["w2_command"]
    assert resumed_payload["resume_stage"] == "fetch"
    assert resumed_payload["policy_revision"] == 7
    assert resumed_payload["execution_fence"] == str(first_fence + 1)
    assert resumed_payload["owner_deletion_epoch"] == job.owner_deletion_epoch
    links = db_session.scalars(
        select(JobSourceLink)
        .where(JobSourceLink.job_id == job_id)
        .order_by(JobSourceLink.created_at)
    ).all()
    assert len(links) == 2
    assert {link.command_id for link in links} == {first_command.id, resumed.id}
    assert _relay(migrated_engine, relay_sqs).drain_once(limit=10).published == 1
    delivery = json.loads(relay_sqs.sent_messages[-1].body)
    lookup = TestClient(
        create_lookup_app(
            session_factory=_factory(migrated_engine),
            expected_bearer_token="test-token",
        )
    )
    available = lookup.post(
        "/internal/v1/job-commands/lookup",
        json={
            "schema_version": "w1.private.command-lookup.v1",
            "command_id": str(resumed.id),
            "execution_fence": resumed.execution_fence,
            "owner_deletion_epoch": resumed.owner_deletion_epoch,
        },
        headers=_lookup_headers(),
    )
    assert available.status_code == 200
    assert available.json()["status"] == "AVAILABLE"
    assert available.json()["command"] == delivery["payload"]
    w2_source_root = os.environ.get("W2_ENGINE_SOURCE_ROOT")
    if w2_source_root:
        parser_environment = dict(os.environ)
        parser_environment["PYTHONPATH"] = os.pathsep.join(
            filter(None, (w2_source_root, parser_environment.get("PYTHONPATH")))
        )
        parsed = subprocess.run(
            [
                os.environ.get("W2_SEMANTIC_PARSER_PYTHON", sys.executable),
                "-c",
                "import sys; from epick_engine.source_collection.contracts import "
                "CollectionCommand; CollectionCommand.model_validate_json(sys.stdin.read())",
            ],
            input=json.dumps(available.json()["command"]),
            text=True,
            capture_output=True,
            env=parser_environment,
            timeout=20,
            check=False,
        )
        assert parsed.returncode == 0, parsed.stderr
    stale_fence = lookup.post(
        "/internal/v1/job-commands/lookup",
        json={
            "schema_version": "w1.private.command-lookup.v1",
            "command_id": str(resumed.id),
            "execution_fence": first_fence,
            "owner_deletion_epoch": resumed.owner_deletion_epoch,
        },
        headers=_lookup_headers(),
    )
    assert stale_fence.json()["status"] == "STALE_FENCE"
    stale_epoch = lookup.post(
        "/internal/v1/job-commands/lookup",
        json={
            "schema_version": "w1.private.command-lookup.v1",
            "command_id": str(resumed.id),
            "execution_fence": resumed.execution_fence,
            "owner_deletion_epoch": resumed.owner_deletion_epoch + 1,
        },
        headers=_lookup_headers(),
    )
    assert stale_epoch.json()["status"] == "STALE_DELETION_EPOCH"
    JobService(db_session).request_cancellation(
        owner_user_id=job.owner_user_id, job_id=job.id
    )
    db_session.commit()
    cancelled = lookup.post(
        "/internal/v1/job-commands/lookup",
        json={
            "schema_version": "w1.private.command-lookup.v1",
            "command_id": str(resumed.id),
            "execution_fence": resumed.execution_fence,
            "owner_deletion_epoch": resumed.owner_deletion_epoch,
        },
        headers=_lookup_headers(),
    )
    assert cancelled.json()["status"] == "STALE_FENCE"


@pytest.mark.postgres
def test_w2_policy_stage_failure_with_null_policy_revision_is_accepted(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    relay_sqs, job_id, w2_command_id = _run_execution_to_w2_dispatch(
        migrated_engine, db_session, key="policy-null"
    )
    assert _relay(migrated_engine, relay_sqs).drain_once(limit=10).published == 1
    db_session.expire_all()
    command = db_session.get(JobCommand, w2_command_id)
    assert command is not None
    envelope = _w2_result_envelope(command=command)
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    payload["successful_source_refs"] = []
    payload["failures"] = [
        {
            "source_id": payload["source_id"],
            "stage": "policy",
            "code": "SOURCE_POLICY_BLOCKED",
            "missing_sections": [],
            "impact": "정책 판정 전에 수집을 시작하지 않았습니다.",
            "core_decision_revision": 1,
        }
    ]
    payload["completion_kind"] = "none"
    payload["resume_stage"] = "policy"
    payload["policy_revision"] = None
    result_sqs = InMemorySqsPort()
    result_sqs.inject_message(queue_url=W2_RESULT_QUEUE_URL, body=json.dumps(envelope))
    result_worker = CollectionResultWorker(
        session_factory=_factory(migrated_engine),
        sqs=result_sqs,
        result_queue_url=W2_RESULT_QUEUE_URL,
    )

    result = result_worker.drain_once()

    assert result.acknowledged == 1
    assert result.rejected_schema == 0
    db_session.expire_all()
    job = db_session.get(Job, job_id)
    assert job is not None and job.status == "FAILED_RETRYABLE" and job.active_lease_id is None


@pytest.mark.postgres
def test_expired_lease_fences_late_w2_result_and_opens_retry(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    _, job_id, _ = _run_execution_to_w2_dispatch(migrated_engine, db_session, key="expired")
    db_session.expire_all()
    lease = db_session.scalar(
        select(JobExecutionLease).where(JobExecutionLease.job_id == job_id).limit(1)
    )
    assert lease is not None
    lease.heartbeat_at = datetime.now(UTC) - timedelta(hours=1)
    db_session.commit()
    worker = JobWorker(
        session_factory=_factory(migrated_engine),
        sqs=InMemorySqsPort(),
        execution_queue_url=EXECUTION_QUEUE_URL,
        worker_id="runtime-worker-test",
        visibility_timeout_seconds=120,
        lease_heartbeat_seconds=60,
    )
    assert worker.recover_expired_leases() == 1
    db_session.expire_all()
    job = db_session.get(Job, job_id)
    assert job is not None and job.status == "FAILED_RETRYABLE"
    assert job.execution_fence == 2
    assert job.active_lease_id is None
