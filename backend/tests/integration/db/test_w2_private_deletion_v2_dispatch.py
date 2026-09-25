from __future__ import annotations

import json

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.deletion import DeletionTarget
from app.models.identity import User
from app.models.jobs import OutboxMessage
from app.runtime.outbox_relay import OutboxRelay, QueueUrlRegistry
from app.services.deletion import DeletionOrchestrationService


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
