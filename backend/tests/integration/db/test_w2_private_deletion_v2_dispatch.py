from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.deletion import DeletionTarget
from app.models.identity import User
from app.models.jobs import OutboxMessage
from app.runtime.outbox_relay import OutboxRelay, QueueUrlRegistry
from app.runtime.w2_deletion_manual_retry import (
    ManualRetryEvidence,
    ManualRetryRefused,
    stage_reconciled_w2_deletion_retry,
)
from app.services.deletion import DeletionOrchestrationService

RUNTIME_PRIVILEGES_SQL = Path(__file__).parents[3] / "infra" / "postgres" / "runtime_privileges.sql"


def _start_account_deletion(session: Session) -> OutboxMessage:
    owner = User(display_name="W2 deletion owner", locale="ko-KR", timezone="Asia/Seoul")
    session.add(owner)
    session.flush()
    service = DeletionOrchestrationService(session)
    preview = service.create_account_deletion_preview(
        owner_user_id=owner.id, preview_token="one-time-test-token"
    )
    request = service.confirm_and_start_account_deletion(
        owner_user_id=owner.id,
        deletion_request_id=preview.request.id,
        preview_token=preview.preview_token,
    )
    message = session.scalar(
        select(OutboxMessage).where(
            OutboxMessage.deletion_request_id == request.id,
            OutboxMessage.message_type == "w1.private.w2.deletion-command.v2",
        )
    )
    assert message is not None
    session.commit()
    return message


def _relay(engine: Engine) -> OutboxRelay:
    return OutboxRelay(
        session_factory=sessionmaker(bind=engine, expire_on_commit=False),
        sqs=object(),
        queues=QueueUrlRegistry(
            w1_execution_queue_url=None,
            w2_deletion_command_queue_url="dedicated-w2-deletion-queue",
            deletion_only=True,
        ),
        relay_id="w1-w2-deletion-test",
    )


def test_w2_v2_dispatch_claims_only_current_exact_body(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
    with Session(migrated_engine, expire_on_commit=False) as session:
        message = _start_account_deletion(session)
        expected = message.payload
    batch = _relay(migrated_engine).claim_due(limit=1)
    assert batch.failed_final == 0
    assert len(batch.claims) == 1
    assert batch.claims[0].queue_url == "dedicated-w2-deletion-queue"
    assert json.loads(batch.claims[0].body) == expected
    assert batch.claims[0].message_attributes["epick_message_id"] == str(message.id)


def test_w2_v2_dispatch_rejects_outer_owner_tampering(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
    with Session(migrated_engine, expire_on_commit=False) as session:
        message = _start_account_deletion(session)
        message.payload = {**message.payload, "owner_user_id": str(message.id)}
        session.commit()
    batch = _relay(migrated_engine).claim_due(limit=1)
    assert batch.claims == ()
    assert batch.failed_final == 1


def test_w2_v2_published_command_marks_target_dispatched(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
    with Session(migrated_engine, expire_on_commit=False) as session:
        message = _start_account_deletion(session)
    relay = _relay(migrated_engine)
    claim = relay.claim_due(limit=1).claims[0]
    assert relay._mark_published(claim=claim)
    with Session(migrated_engine) as session:
        target = session.get(DeletionTarget, message.deletion_target_id)
        assert target is not None
        assert target.status == "DISPATCHED"


def test_w2_v2_retry_reuses_exact_message_and_deletion_id(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
    with Session(migrated_engine, expire_on_commit=False) as session:
        message = _start_account_deletion(session)
    relay = _relay(migrated_engine)
    first = relay.claim_due(limit=1).claims[0]
    assert relay._mark_retryable(claim=first, error_code="SQS_TRANSIENT")
    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE outbox_messages SET available_at = now() - interval '1 second' "
                "WHERE id = :id"
            ),
            {"id": message.id},
        )
    second = relay.claim_due(limit=1).claims[0]
    assert second.outbox_id == first.outbox_id
    assert second.body == first.body
    assert json.loads(second.body)["payload"]["deletion_id"] == str(message.deletion_target_id)


def test_w2_v2_operator_retry_stages_same_payload_without_dlq_redrive(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
    with Session(migrated_engine, expire_on_commit=False) as session:
        original = _start_account_deletion(session)
        original_payload = original.payload
        owner_id = original.owner_user_id
        request_id = original.deletion_request_id
        target_id = original.deletion_target_id
    assert owner_id is not None and request_id is not None and target_id is not None
    relay = _relay(migrated_engine)
    first = relay.claim_due(limit=1).claims[0]
    assert relay._mark_published(claim=first)
    with Session(migrated_engine, expire_on_commit=False) as session:
        service = DeletionOrchestrationService(session)
        service.record_target_failure(
            owner_user_id=owner_id,
            deletion_request_id=request_id,
            deletion_target_id=target_id,
            failure_code="W2_ACK_REQUIRES_MANUAL_RECONCILIATION",
        )
        service.retry_target(
            owner_user_id=owner_id,
            deletion_request_id=request_id,
            deletion_target_id=target_id,
        )
        retry = session.scalar(
            select(OutboxMessage).where(
                OutboxMessage.deletion_target_id == target_id,
                OutboxMessage.aggregate_revision == original.aggregate_revision + 1,
            )
        )
        assert retry is not None
        assert retry.id != original.id
        assert retry.payload == original_payload
        assert retry.payload["message_id"] == str(target_id)
        assert retry.payload["payload"]["deletion_id"] == str(target_id)
        session.commit()


def _published_manual_retry_case(engine: Engine) -> ManualRetryEvidence:
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
    with Session(engine, expire_on_commit=False) as session:
        original = _start_account_deletion(session)
    relay = _relay(engine)
    claim = relay.claim_due(limit=1).claims[0]
    assert relay._mark_published(claim=claim)
    assert original.deletion_target_id is not None
    return ManualRetryEvidence(
        deletion_id=original.deletion_target_id,
        original_outbox_id=original.id,
        expected_epoch=1,
        expected_body_sha256="sha256:" + sha256(claim.body.encode("utf-8")).hexdigest(),
        ack_code="W2_DELETION_V2_BINDING_INVALID",
        w2_receipt_outcome="APPLIED",
        incident_id="INC-T050-409-001",
        route_paused=True,
    )


def test_w2_v2_manual_dlq_retry_requires_current_original_and_stages_exact_body(
    migrated_engine: Engine,
) -> None:
    evidence = _published_manual_retry_case(migrated_engine)
    with Session(migrated_engine, expire_on_commit=False) as session:
        with session.begin():
            result = stage_reconciled_w2_deletion_retry(session, evidence=evidence)
            original = session.get(OutboxMessage, evidence.original_outbox_id)
            replacement = session.get(OutboxMessage, result.new_outbox_id)
            assert original is not None and replacement is not None
            assert original.status == "PUBLISHED"
            assert replacement.status == "PENDING"
            assert replacement.payload == original.payload
            assert result.deletion_id == evidence.deletion_id
            assert result.body_sha256 == evidence.expected_body_sha256
    with Session(migrated_engine) as session:
        with session.begin(), pytest.raises(ManualRetryRefused, match="TARGET_NOT_CURRENT"):
            stage_reconciled_w2_deletion_retry(session, evidence=evidence)


@pytest.mark.parametrize(
    ("change", "rejection"),
    [
        ({"route_paused": False}, "OPERATOR_EVIDENCE_INVALID"),
        ({"ack_code": "W2_DELETION_V2_STALE_EPOCH"}, "OPERATOR_EVIDENCE_INVALID"),
        ({"w2_receipt_outcome": "STALE"}, "OPERATOR_EVIDENCE_INVALID"),
        ({"expected_epoch": 2}, "TARGET_NOT_CURRENT"),
        ({"expected_body_sha256": "sha256:" + "0" * 64}, "DLQ_BODY_MISMATCH"),
        ({"original_outbox_id": uuid4()}, "ORIGINAL_OUTBOX_NOT_CURRENT"),
    ],
)
def test_w2_v2_manual_dlq_retry_rejects_missing_or_stale_evidence(
    migrated_engine: Engine, change: dict[str, object], rejection: str
) -> None:
    evidence = replace(_published_manual_retry_case(migrated_engine), **change)
    with Session(migrated_engine) as session:
        with session.begin(), pytest.raises(ManualRetryRefused, match=rejection):
            stage_reconciled_w2_deletion_retry(session, evidence=evidence)
    with Session(migrated_engine) as session:
        target = session.get(DeletionTarget, evidence.deletion_id)
        assert target is not None and target.status == "DISPATCHED"
        messages = session.scalars(
            select(OutboxMessage).where(OutboxMessage.deletion_target_id == evidence.deletion_id)
        ).all()
        assert len(messages) == 1


def test_w2_v2_manual_dlq_retry_rejects_acknowledged_target(migrated_engine: Engine) -> None:
    evidence = _published_manual_retry_case(migrated_engine)
    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE deletion_targets SET status='ACKNOWLEDGED', ack_epoch=1, "
                "ack_event_id=id, completed_at=now() WHERE id=:id"
            ),
            {"id": evidence.deletion_id},
        )
    with Session(migrated_engine) as session:
        with session.begin(), pytest.raises(ManualRetryRefused, match="TARGET_NOT_CURRENT"):
            stage_reconciled_w2_deletion_retry(session, evidence=evidence)


def test_w2_v2_manual_dlq_retry_uses_deletion_only_database_role(migrated_engine: Engine) -> None:
    evidence = _published_manual_retry_case(migrated_engine)
    with migrated_engine.begin() as connection:
        connection.execute(text(RUNTIME_PRIVILEGES_SQL.read_text(encoding="utf-8")))
    role_engine = create_engine(migrated_engine.url.render_as_string(hide_password=False))

    @event.listens_for(role_engine, "checkout")
    def set_deleter_role(dbapi_connection, connection_record, connection_proxy) -> None:
        del connection_record, connection_proxy
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET ROLE epick_deleter")

    try:
        with Session(role_engine) as session:
            with session.begin():
                staged = stage_reconciled_w2_deletion_retry(session, evidence=evidence)
                assert staged.body_sha256 == evidence.expected_body_sha256
    finally:
        role_engine.dispose()


def test_w2_v2_deletion_relay_role_claims_only_dedicated_outbox(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
    with Session(migrated_engine, expire_on_commit=False) as session:
        message = _start_account_deletion(session)
        session.add(
            OutboxMessage(
                schema_version="1.0",
                visibility_scope="PRIVATE",
                aggregate_type="DELETION_TARGET",
                aggregate_id=message.deletion_target_id,
                aggregate_revision=99,
                message_type="w1.private.unrelated.v1",
                owner_user_id=message.owner_user_id,
                owner_deletion_epoch=1,
                deletion_request_id=message.deletion_request_id,
                deletion_target_id=message.deletion_target_id,
                payload={},
                status="PENDING",
            )
        )
        session.commit()
    with migrated_engine.begin() as connection:
        connection.execute(text(RUNTIME_PRIVILEGES_SQL.read_text(encoding="utf-8")))
    role_engine = create_engine(migrated_engine.url.render_as_string(hide_password=False))

    @event.listens_for(role_engine, "checkout")
    def set_deleter_role(dbapi_connection, connection_record, connection_proxy) -> None:
        del connection_record, connection_proxy
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET ROLE epick_deleter")

    try:
        with role_engine.connect() as connection:
            rows = (
                connection.execute(
                    text("SELECT message_type FROM outbox_messages ORDER BY message_type")
                )
                .scalars()
                .all()
            )
        assert "w1.private.w2.deletion-command.v2" in rows
        assert "w1.private.unrelated.v1" not in rows
        assert set(rows) == {
            "w1.private.w2.deletion-command.v2",
            "w1.private.w3.owner-deletion.v1",
        }
        batch = _relay(role_engine).claim_due(limit=1)
        assert batch.failed_final == 0
        assert len(batch.claims) == 1
        assert batch.claims[0].outbox_id == message.id
    finally:
        role_engine.dispose()
