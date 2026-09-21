from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models.deletion import DeletionRequest, DeletionTarget
from app.models.identity import User
from app.models.jobs import InboxReceipt, OutboxMessage
from app.runtime.outbox_relay import OutboxRelay, QueueUrlRegistry
from app.runtime.sqs import InMemorySqsPort
from app.runtime.w3_deletion_worker import (
    W3RetentionReceiptContractError,
    W3RetentionReceiptService,
    receipt_digest,
)
from app.services.deletion import DeletionOrchestrationService

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def clean_w3_deletion_dispatch_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
    yield


def _stage_w3_deletion(
    db_session: Session,
    *,
    deletion_epoch: int = 0,
) -> tuple[User, DeletionRequest, DeletionTarget, OutboxMessage]:
    owner = User(
        display_name="W3 receipt owner",
        locale="ko-KR",
        timezone="Asia/Seoul",
        deletion_epoch=deletion_epoch,
    )
    db_session.add(owner)
    db_session.flush()
    service = DeletionOrchestrationService(db_session)
    preview = service.create_account_deletion_preview(
        owner_user_id=owner.id,
        preview_token=f"w3-receipt-{owner.id}",
    )
    request = service.confirm_and_start_account_deletion(
        owner_user_id=owner.id,
        deletion_request_id=preview.request.id,
        preview_token=preview.preview_token,
    )
    target = db_session.scalar(
        select(DeletionTarget).where(
            DeletionTarget.deletion_request_id == request.id,
            DeletionTarget.store_type == "W3_CORE_RUNTIME",
        )
    )
    assert target is not None
    message = db_session.scalar(
        select(OutboxMessage).where(OutboxMessage.deletion_target_id == target.id)
    )
    assert message is not None
    return owner, request, target, message


def _receipt_body(message: OutboxMessage, target: DeletionTarget, *, outcome: str) -> str:
    occurred_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return json.dumps(
        {
            "schema_version": "w3.private.w1-lifecycle-receipt/1.0",
            "message_type": "w3.private.w1.lifecycle-receipt",
            "receipt_id": str(UUID(int=901)),
            "occurred_at": occurred_at,
            "visibility_scope": "PRIVATE",
            "producer": "w3",
            "command_id": str(message.id),
            "target_ref": str(target.id),
            "operation": "DELETE_OWNER",
            "outcome": outcome,
            "affected_count": 1 if outcome == "APPLIED" else 0,
            "applied_epoch": message.owner_deletion_epoch,
            "effective_at": None,
        }
    )


def test_confirmed_deletion_stages_exactly_one_w3_command_at_current_epoch(
    db_session: Session,
) -> None:
    owner = User(
        display_name="W3 deletion owner",
        locale="ko-KR",
        timezone="Asia/Seoul",
        deletion_epoch=7,
    )
    db_session.add(owner)
    db_session.flush()
    service = DeletionOrchestrationService(db_session)
    preview = service.create_account_deletion_preview(
        owner_user_id=owner.id,
        preview_token="w3-current-epoch-confirmation",
    )

    request = service.confirm_and_start_account_deletion(
        owner_user_id=owner.id,
        deletion_request_id=preview.request.id,
        preview_token=preview.preview_token,
    )

    w3_targets = list(
        db_session.scalars(
            select(DeletionTarget).where(
                DeletionTarget.deletion_request_id == request.id,
                DeletionTarget.store_type == "W3_CORE_RUNTIME",
            )
        )
    )
    assert len(w3_targets) == 1
    target = w3_targets[0]
    commands = list(
        db_session.scalars(
            select(OutboxMessage).where(
                OutboxMessage.deletion_request_id == request.id,
                OutboxMessage.deletion_target_id == target.id,
            )
        )
    )
    assert len(commands) == 1
    command = commands[0]

    assert request.owner_deletion_epoch == owner.deletion_epoch == 8
    assert command.owner_deletion_epoch == 8
    assert command.aggregate_revision == target.attempts == 1
    assert command.visibility_scope == "PRIVATE"
    assert command.payload == {
        "schema_version": "1.0",
        "command_id": str(command.id),
        "owner_id": str(owner.id),
        "owner_deletion_epoch": 8,
        "target_type": "W3_CORE_RUNTIME",
        "target_ref": str(target.id),
    }
    assert UUID(command.payload["command_id"]) == command.id


def test_w3_target_and_command_roll_back_with_deletion_transaction(db_session: Session) -> None:
    owner = User(display_name="Rollback owner", locale="ko-KR", timezone="Asia/Seoul")
    db_session.add(owner)
    db_session.flush()
    owner_id = owner.id
    service = DeletionOrchestrationService(db_session)
    preview = service.create_account_deletion_preview(
        owner_user_id=owner_id,
        preview_token="w3-rollback-confirmation",
    )
    request_id = preview.request.id
    service.confirm_and_start_account_deletion(
        owner_user_id=owner_id,
        deletion_request_id=request_id,
        preview_token=preview.preview_token,
    )
    db_session.rollback()

    assert db_session.scalar(
        select(DeletionTarget.id).where(
            DeletionTarget.deletion_request_id == request_id,
            DeletionTarget.store_type == "W3_CORE_RUNTIME",
        )
    ) is None
    assert db_session.scalar(
        select(OutboxMessage.id).where(OutboxMessage.deletion_request_id == request_id)
    ) is None


def test_w3_command_relay_uses_dedicated_queue_and_marks_target_dispatched(
    db_session: Session,
    migrated_engine: Engine,
) -> None:
    _, _, target, command = _stage_w3_deletion(db_session)
    target_id = target.id
    command_id = command.id
    expected_payload = dict(command.payload)
    for unrelated in db_session.scalars(
        select(OutboxMessage).where(OutboxMessage.id != command.id)
    ):
        unrelated.status = "PUBLISHED"
    db_session.commit()

    queue_url = "https://sqs.ap-northeast-2.amazonaws.com/123/w3-retention-command"
    sqs = InMemorySqsPort()
    factory = sessionmaker(bind=migrated_engine, autoflush=False, expire_on_commit=False)
    relay = OutboxRelay(
        session_factory=factory,
        sqs=sqs,
        queues=QueueUrlRegistry(
            w1_execution_queue_url=None,
            w3_retention_command_queue_url=queue_url,
        ),
        relay_id="w3-retention-test",
    )

    result = relay.drain_once(limit=10)

    assert result.claimed == result.published == 1
    sent = sqs.sent_messages[0]
    assert sent.queue_url == queue_url
    assert json.loads(sent.body) == expected_payload
    assert sent.message_attributes == {
        "epick_message_type": "w1.private.w3.owner-deletion.v1",
        "epick_schema_version": "1.0",
        "epick_message_id": str(command_id),
        "epick_command_id": str(command_id),
    }
    assert "owner_id" not in sent.message_attributes
    with factory() as verification:
        persisted_target = verification.get(DeletionTarget, target_id)
        assert persisted_target is not None
        assert persisted_target.status == "DISPATCHED"


@pytest.mark.parametrize("outcome", ["APPLIED", "DUPLICATE"])
def test_authenticated_current_w3_success_receipt_acknowledges_once(
    db_session: Session,
    outcome: str,
) -> None:
    _, request, target, command = _stage_w3_deletion(db_session, deletion_epoch=4)
    body = _receipt_body(command, target, outcome=outcome)
    service = W3RetentionReceiptService(db_session)

    first = service.apply(body=body)
    repeated = service.apply(body=body)

    assert first.duplicate_receipt is False
    assert repeated.duplicate_receipt is True
    assert target.status == "ACKNOWLEDGED"
    assert target.ack_epoch == request.owner_deletion_epoch == 5
    assert target.ack_event_id == command.id
    receipt = db_session.get(InboxReceipt, ("w1.w3.retention-receipt", command.id))
    assert receipt is not None
    assert receipt.outcome_code == outcome
    assert receipt.payload_digest == receipt_digest(body)


def test_same_command_with_different_receipt_body_is_terminally_rejected(
    db_session: Session,
) -> None:
    _, _, target, command = _stage_w3_deletion(db_session)
    original = _receipt_body(command, target, outcome="APPLIED")
    service = W3RetentionReceiptService(db_session)
    service.apply(body=original)
    altered = json.loads(original)
    altered["receipt_id"] = str(UUID(int=902))

    with pytest.raises(
        W3RetentionReceiptContractError,
        match="W3_RETENTION_RECEIPT_CONFLICT",
    ):
        service.apply(body=json.dumps(altered))

    assert target.status == "ACKNOWLEDGED"
    receipt = db_session.get(InboxReceipt, ("w1.w3.retention-receipt", command.id))
    assert receipt is not None
    assert receipt.payload_digest == receipt_digest(original)


@pytest.mark.parametrize("outcome", ["STALE"])
def test_w3_rejection_receipt_records_retryable_target_failure(
    db_session: Session,
    outcome: str,
) -> None:
    _, request, target, command = _stage_w3_deletion(db_session)

    W3RetentionReceiptService(db_session).apply(
        body=_receipt_body(command, target, outcome=outcome)
    )

    assert target.status == "FAILED_RETRYABLE"
    assert target.error_code == f"W3_DELETE_{outcome}"
    assert request.status == "FAILED_RETRYABLE"
    assert request.failure_code == f"W3_DELETE_{outcome}"


def test_w3_success_receipt_rejects_wrong_applied_epoch(
    db_session: Session,
) -> None:
    _, _, target, command = _stage_w3_deletion(db_session)
    body = json.loads(_receipt_body(command, target, outcome="APPLIED"))
    body["applied_epoch"] = int(command.owner_deletion_epoch or 0) + 1

    with pytest.raises(
        W3RetentionReceiptContractError,
        match="W3_DELETION_RECEIPT_EPOCH_MISMATCH",
    ):
        W3RetentionReceiptService(db_session).apply(body=json.dumps(body))

    assert target.status == "QUEUED"


def test_w3_receipt_rejects_non_current_owner_epoch_without_mutation(
    db_session: Session,
) -> None:
    owner, _, target, command = _stage_w3_deletion(db_session)
    owner.deletion_epoch += 1
    db_session.flush()

    with pytest.raises(
        W3RetentionReceiptContractError,
        match="W3_DELETION_CURRENT_EPOCH_MISMATCH",
    ):
        W3RetentionReceiptService(db_session).apply(
            body=_receipt_body(command, target, outcome="APPLIED")
        )

    assert target.status == "QUEUED"
    assert db_session.get(
        InboxReceipt,
        ("w1.w3.retention-receipt", command.id),
    ) is None
