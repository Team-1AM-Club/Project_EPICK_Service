from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.identity import User
from app.models.jobs import Job, JobCommand, OutboxMessage
from app.runtime.outbox_relay import OutboxRelay, QueueUrlRegistry
from app.runtime.sqs import InMemorySqsPort, SqsRetryableError
from app.services.jobs import JobService


class SimulatedProcessDeath(BaseException):
    """Represents a kill after SQS accepts the message but before the DB completion write."""


class CrashAfterSendSqsPort(InMemorySqsPort):
    def send_message(
        self,
        *,
        queue_url: str,
        body: str,
        message_attributes: dict[str, str],
    ) -> str:
        super().send_message(
            queue_url=queue_url,
            body=body,
            message_attributes=message_attributes,
        )
        raise SimulatedProcessDeath()


@pytest.fixture(autouse=True)
def clean_relay_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
    yield


def _factory(migrated_engine: Engine) -> sessionmaker:
    return sessionmaker(bind=migrated_engine, autoflush=False, expire_on_commit=False)


def _accept_job(session: Session, *, key: str) -> tuple[UUID, UUID, UUID, UUID]:
    owner = User(display_name=f"Relay owner {key}", locale="ko-KR", timezone="Asia/Seoul")
    session.add(owner)
    session.flush()
    accepted = JobService(session).accept_job(
        owner_user_id=owner.id,
        job_type="SOURCE_COLLECTION",
        idempotency_key=key,
        request_hash=f"hash-{key}",
        analysis_input_version="analysis-v1",
    )
    assert accepted.command is not None
    assert accepted.outbox_message is not None
    return owner.id, accepted.job.id, accepted.command.id, accepted.outbox_message.id


def _relay(
    migrated_engine: Engine,
    sqs: InMemorySqsPort,
    *,
    execution_queue_url: str | None = "https://sqs.ap-northeast-2.amazonaws.com/123/w1-execution",
    relay_id: str = "relay-test",
) -> OutboxRelay:
    return OutboxRelay(
        session_factory=_factory(migrated_engine),
        sqs=sqs,
        queues=QueueUrlRegistry(w1_execution_queue_url=execution_queue_url),
        relay_id=relay_id,
        lease_seconds=120,
        retry_base_seconds=5,
        retry_max_seconds=20,
    )


@pytest.mark.postgres
def test_relay_does_not_send_until_the_job_acceptance_transaction_commits(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    _, _, _, _ = _accept_job(db_session, key="not-committed")
    sqs = InMemorySqsPort()

    result = _relay(migrated_engine, sqs).drain_once(limit=10)

    assert result.claimed == 0
    assert sqs.sent_messages == []
    db_session.rollback()


@pytest.mark.postgres
def test_relay_sends_only_the_private_reference_and_marks_dispatch_enqueued(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    _, job_id, command_id, outbox_id = _accept_job(db_session, key="publish")
    checkpoint_id = uuid4()
    outbox = db_session.get(OutboxMessage, outbox_id)
    command = db_session.get(JobCommand, command_id)
    assert outbox is not None and command is not None
    outbox.payload = {**outbox.payload, "checkpoint_id": str(checkpoint_id)}
    command.payload = {**command.payload, "checkpoint_id": str(checkpoint_id)}
    db_session.commit()
    sqs = InMemorySqsPort()

    result = _relay(migrated_engine, sqs).drain_once(limit=10)

    assert result.claimed == result.published == 1
    assert result.retry_scheduled == result.failed_final == result.stale_completion == 0
    assert len(sqs.sent_messages) == 1
    sent = sqs.sent_messages[0]
    body = json.loads(sent.body)
    assert body == {
        "schema_version": "1.0",
        "message_id": str(command_id),
        "command_id": str(command_id),
        "job_id": str(job_id),
        "execution_fence": 1,
        "owner_deletion_epoch": 0,
        "issued_at": body["issued_at"],
        "payload_ref": str(outbox_id),
    }
    assert set(body).isdisjoint({"owner_id", "owner_user_id", "payload", "token", "secret"})
    assert sent.message_attributes["epick_message_id"] == str(command_id)
    assert sent.message_attributes["epick_command_id"] == str(command_id)

    db_session.expire_all()
    outbox = db_session.get(OutboxMessage, outbox_id)
    command = db_session.get(JobCommand, command_id)
    job = db_session.get(Job, job_id)
    assert outbox is not None and outbox.status == "PUBLISHED"
    assert outbox.relay_claim_token is None
    assert command is not None and command.status == "ENQUEUED"
    assert job is not None and job.dispatch_status == "ENQUEUED"


@pytest.mark.postgres
def test_relay_retries_after_throttling_without_changing_dispatch_truth(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    _, job_id, command_id, outbox_id = _accept_job(db_session, key="throttle")
    db_session.commit()
    sqs = InMemorySqsPort(failures=[SqsRetryableError("SQS_THROTTLED")])

    result = _relay(migrated_engine, sqs).drain_once(limit=10)

    assert result.claimed == result.retry_scheduled == 1
    assert sqs.sent_messages == []
    db_session.expire_all()
    outbox = db_session.get(OutboxMessage, outbox_id)
    command = db_session.get(JobCommand, command_id)
    job = db_session.get(Job, job_id)
    assert outbox is not None
    assert outbox.status == "FAILED_RETRYABLE"
    assert outbox.attempts == 1
    assert outbox.last_error_code == "SQS_THROTTLED"
    assert outbox.relay_claim_token is None
    assert outbox.available_at > datetime.now(UTC)
    assert command is not None and command.status == "PENDING"
    assert job is not None and job.dispatch_status == "OUTBOX_PENDING"


@pytest.mark.postgres
def test_relay_recovers_an_expired_publishing_lease_as_a_duplicate_delivery(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    _, _, command_id, outbox_id = _accept_job(db_session, key="crash-recovery")
    db_session.commit()
    crashing_sqs = CrashAfterSendSqsPort()
    relay = _relay(migrated_engine, crashing_sqs)

    with pytest.raises(SimulatedProcessDeath):
        relay.drain_once(limit=1)
    assert len(crashing_sqs.sent_messages) == 1

    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE outbox_messages SET relay_lease_expires_at = :expired_at "
                "WHERE id = :outbox_id"
            ),
            {
                "outbox_id": outbox_id,
                "expired_at": datetime.now(UTC) - timedelta(seconds=1),
            },
        )

    recovered_sqs = InMemorySqsPort()
    result = _relay(migrated_engine, recovered_sqs, relay_id="relay-recovered").drain_once(limit=1)

    assert result.claimed == result.published == 1
    assert len(recovered_sqs.sent_messages) == 1
    first_body = json.loads(crashing_sqs.sent_messages[0].body)
    second_body = json.loads(recovered_sqs.sent_messages[0].body)
    assert first_body["message_id"] == second_body["message_id"] == str(command_id)
    assert first_body["command_id"] == second_body["command_id"] == str(command_id)
    assert first_body["payload_ref"] == second_body["payload_ref"] == str(outbox_id)

    db_session.expire_all()
    outbox = db_session.get(OutboxMessage, outbox_id)
    assert outbox is not None and outbox.status == "PUBLISHED"
    assert outbox.attempts == 2


@pytest.mark.postgres
def test_relay_finalizes_malformed_private_payload_without_sending(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    _, job_id, command_id, outbox_id = _accept_job(db_session, key="malformed")
    db_session.flush()
    outbox = db_session.get(OutboxMessage, outbox_id)
    assert outbox is not None
    outbox.payload = {"command_id": str(uuid4())}
    db_session.commit()
    sqs = InMemorySqsPort()

    result = _relay(migrated_engine, sqs).drain_once(limit=10)

    assert result.claimed == 0
    assert result.failed_final == 1
    assert sqs.sent_messages == []
    db_session.expire_all()
    outbox = db_session.get(OutboxMessage, outbox_id)
    command = db_session.get(JobCommand, command_id)
    job = db_session.get(Job, job_id)
    assert outbox is not None and outbox.status == "FAILED_FINAL"
    assert outbox.last_error_code == "OUTBOX_PRIVATE_PAYLOAD_INVALID"
    assert command is not None and command.status == "FAILED"
    assert job is not None and job.dispatch_status == "BLOCKED"


@pytest.mark.postgres
def test_relay_finalizes_an_unconfigured_route_without_sending(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    _, job_id, command_id, outbox_id = _accept_job(db_session, key="missing-route")
    db_session.commit()
    sqs = InMemorySqsPort()

    result = _relay(migrated_engine, sqs, execution_queue_url=None).drain_once(limit=10)

    assert result.claimed == 0
    assert result.failed_final == 1
    assert sqs.sent_messages == []
    db_session.expire_all()
    outbox = db_session.get(OutboxMessage, outbox_id)
    command = db_session.get(JobCommand, command_id)
    job = db_session.get(Job, job_id)
    assert outbox is not None and outbox.status == "FAILED_FINAL"
    assert outbox.last_error_code == "OUTBOX_ROUTE_NOT_CONFIGURED"
    assert command is not None and command.status == "FAILED"
    assert job is not None and job.dispatch_status == "BLOCKED"


@pytest.mark.postgres
def test_two_relays_claim_distinct_private_messages_from_different_owners(
    migrated_engine: Engine,
    db_session: Session,
) -> None:
    _accept_job(db_session, key="owner-one")
    _accept_job(db_session, key="owner-two")
    db_session.commit()

    first = _relay(migrated_engine, InMemorySqsPort(), relay_id="relay-one").claim_due(limit=1)
    second = _relay(migrated_engine, InMemorySqsPort(), relay_id="relay-two").claim_due(limit=1)

    assert len(first.claims) == len(second.claims) == 1
    assert first.claims[0].outbox_id != second.claims[0].outbox_id
    assert first.claims[0].claim_token != second.claims[0].claim_token

    # Migration history tests may later downgrade below the PUBLISHING enum state.  This test
    # exercises only claim exclusion, so leave its fixture rows representable by earlier heads.
    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE outbox_messages SET status = 'PUBLISHED', published_at = now(), "
                "relay_claim_token = NULL, relay_claimed_by = NULL, "
                "relay_lease_expires_at = NULL"
            )
        )
