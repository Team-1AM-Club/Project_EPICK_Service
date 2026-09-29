from __future__ import annotations

import copy
import importlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest

from epick_engine.source_collection.private_deletion_v2 import (
    PrivateDeletionAckV2,
    PrivateDeletionCommandV2,
)
from epick_engine.source_collection.worker import WorkerContractViolation

_DELETION_ID = "11111111-1111-4111-8111-111111111111"
_REQUEST_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_OWNER_ID = "33333333-3333-4333-8333-333333333333"
_PROJECT_ID = "44444444-4444-4444-8444-444444444444"
_EXPECTED_SENDER_ID = "AROASYNTHETICW1ROLE12"


def _consumer_symbol(name: str) -> Any:
    try:
        module = importlib.import_module(
            "epick_engine.source_collection.private_deletion_consumer_v2"
        )
    except ModuleNotFoundError:
        pytest.fail("private deletion v2 consumer is not implemented")
    return getattr(module, name)


def _envelope() -> dict[str, object]:
    scope = {"type": "PROJECT", "project_id": _PROJECT_ID}
    payload = {
        "schema_version": "w2.private-deletion.v2",
        "deletion_id": _DELETION_ID,
        "owner_user_id": _OWNER_ID,
        "deletion_epoch": 7,
        "scope": copy.deepcopy(scope),
    }
    return {
        "schema_version": "w1.private.w2-deletion-dispatch.v2",
        "message_type": "w1.private.w2.deletion-command.v2",
        "message_id": _DELETION_ID,
        "producer": "w1",
        "occurred_at": "2026-09-27T00:00:00Z",
        "visibility_scope": "PRIVATE",
        "deletion_request_id": _REQUEST_ID,
        "deletion_target_id": _DELETION_ID,
        "owner_user_id": _OWNER_ID,
        "deletion_epoch": 7,
        "scope": scope,
        "payload_schema_version": "w2.private-deletion.v2",
        "payload": payload,
    }


def _command_ack(
    command: PrivateDeletionCommandV2, *, duplicate: bool = False
) -> PrivateDeletionAckV2:
    return PrivateDeletionAckV2(
        deletion_id=command.deletion_id,
        owner_user_id=command.owner_user_id,
        deletion_epoch=command.deletion_epoch,
        scope=command.scope,
        outcome="DUPLICATE" if duplicate else "APPLIED",
    )


@dataclass(frozen=True, slots=True)
class _Delivery:
    receipt_handle: str
    body: str
    sender_id: str | None = _EXPECTED_SENDER_ID + ":worker"


class _RecordingQueue:
    def __init__(self, deliveries: list[_Delivery], *, fail_delete: bool = False) -> None:
        self._deliveries = deliveries
        self.fail_delete = fail_delete
        self.delete_attempts: list[str] = []
        self.deleted: list[str] = []

    def receive(self) -> tuple[_Delivery, ...]:
        if not self._deliveries:
            return ()
        return (self._deliveries.pop(0),)

    def delete(self, receipt_handle: str) -> None:
        self.delete_attempts.append(receipt_handle)
        if self.fail_delete:
            raise RuntimeError("synthetic delete failure")
        self.deleted.append(receipt_handle)


class _RecordingProcessor:
    def __init__(
        self,
        result: Callable[[PrivateDeletionCommandV2, int], PrivateDeletionAckV2 | None],
    ) -> None:
        self._result = result
        self.commands: list[PrivateDeletionCommandV2] = []

    def __call__(self, command: PrivateDeletionCommandV2) -> PrivateDeletionAckV2 | None:
        self.commands.append(command)
        return self._result(command, len(self.commands))


def _delivery(*, receipt: str = "receipt-1", sender_id: str | None = None) -> _Delivery:
    sender = _EXPECTED_SENDER_ID + ":worker" if sender_id is None else sender_id
    return _Delivery(receipt_handle=receipt, body=json.dumps(_envelope()), sender_id=sender)


def test_parse_accepts_the_exact_v2_outer_binding() -> None:
    parse = _consumer_symbol("parse_w1_private_deletion_dispatch_v2")

    command = parse(_envelope())

    assert command.to_mapping() == _envelope()["payload"]


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("message_id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        ("deletion_target_id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        ("owner_user_id", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        ("deletion_epoch", 8),
        ("scope", {"type": "ACCOUNT"}),
        ("producer", "w3"),
        ("visibility_scope", "PUBLIC"),
        ("payload_schema_version", "w2.private-deletion.v1"),
        ("occurred_at", "2026-09-27T09:00:00+09:00"),
        ("deletion_request_id", _REQUEST_ID.upper()),
    ],
)
def test_parse_rejects_an_outer_field_that_is_not_bound_to_the_v2_payload(
    field: str,
    invalid_value: object,
) -> None:
    parse = _consumer_symbol("parse_w1_private_deletion_dispatch_v2")
    envelope = _envelope()
    envelope[field] = invalid_value

    with pytest.raises(WorkerContractViolation):
        parse(envelope)


def test_parse_rejects_extra_outer_fields() -> None:
    parse = _consumer_symbol("parse_w1_private_deletion_dispatch_v2")
    envelope = _envelope()
    envelope["untrusted"] = "data"

    with pytest.raises(WorkerContractViolation):
        parse(envelope)


@pytest.mark.parametrize(
    "occurred_at",
    (
        "2026-09-27T00:00:00z",
        "2026-09-27T00:00:00+00:00",
    ),
)
def test_parse_requires_the_w1_pinned_uppercase_z_timestamp(occurred_at: str) -> None:
    parse = _consumer_symbol("parse_w1_private_deletion_dispatch_v2")
    envelope = _envelope()
    envelope["occurred_at"] = occurred_at

    with pytest.raises(WorkerContractViolation):
        parse(envelope)


def test_parse_preserves_w1_acceptance_of_a_lowercase_datetime_separator() -> None:
    parse = _consumer_symbol("parse_w1_private_deletion_dispatch_v2")
    envelope = _envelope()
    envelope["occurred_at"] = "2026-09-27t00:00:00Z"

    command = parse(envelope)

    assert command.deletion_id == UUID(_DELETION_ID)


def test_parse_rejects_the_v1_envelope_and_payload() -> None:
    parse = _consumer_symbol("parse_w1_private_deletion_dispatch_v2")
    envelope = _envelope()
    envelope["schema_version"] = "w1.private.w2-deletion-dispatch.v1"
    envelope["message_type"] = "w1.private.w2.deletion-command.v1"
    envelope["payload_schema_version"] = "w2.private-deletion.v1"
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    payload["schema_version"] = "w2.private-deletion.v1"

    with pytest.raises(WorkerContractViolation):
        parse(envelope)


def test_callback_failure_keeps_the_receipt_for_retry() -> None:
    consume = _consumer_symbol("consume_private_deletion_once")
    queue = _RecordingQueue([_delivery()])

    def fail_after_commit(_: PrivateDeletionCommandV2, __: int) -> PrivateDeletionAckV2:
        raise RuntimeError("synthetic W1 callback failure")

    processor = _RecordingProcessor(fail_after_commit)

    result = consume(queue, _EXPECTED_SENDER_ID, processor)

    assert result.status == "REJECTED"
    assert queue.delete_attempts == []


def test_none_processor_result_is_fail_closed_for_stale_semantics() -> None:
    consume = _consumer_symbol("consume_private_deletion_once")
    queue = _RecordingQueue([_delivery()])
    processor = _RecordingProcessor(lambda _command, _attempt: None)

    result = consume(queue, _EXPECTED_SENDER_ID, processor)

    assert result.status == "REJECTED"
    assert queue.delete_attempts == []


def test_malformed_envelope_is_not_processed_or_deleted() -> None:
    consume = _consumer_symbol("consume_private_deletion_once")
    envelope = _envelope()
    envelope["deletion_target_id"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    queue = _RecordingQueue([_Delivery(receipt_handle="receipt-1", body=json.dumps(envelope))])

    def unexpected_processor(
        command: PrivateDeletionCommandV2,
        attempt: int,
    ) -> PrivateDeletionAckV2:
        raise AssertionError((command, attempt))

    processor = _RecordingProcessor(unexpected_processor)

    result = consume(queue, _EXPECTED_SENDER_ID, processor)

    assert result.status == "MALFORMED"
    assert processor.commands == []
    assert queue.delete_attempts == []


def test_ack_for_a_different_command_is_fail_closed() -> None:
    consume = _consumer_symbol("consume_private_deletion_once")
    queue = _RecordingQueue([_delivery()])

    def mismatched_ack(
        command: PrivateDeletionCommandV2,
        _attempt: int,
    ) -> PrivateDeletionAckV2:
        ack = _command_ack(command)
        return PrivateDeletionAckV2(
            deletion_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            owner_user_id=ack.owner_user_id,
            deletion_epoch=ack.deletion_epoch,
            scope=ack.scope,
            outcome=ack.outcome,
        )

    processor = _RecordingProcessor(mismatched_ack)

    result = consume(queue, _EXPECTED_SENDER_ID, processor)

    assert result.status == "REJECTED"
    assert queue.delete_attempts == []


def test_duplicate_delivery_is_reprocessed_and_deleted_only_after_each_ack() -> None:
    consume = _consumer_symbol("consume_private_deletion_once")
    queue = _RecordingQueue(
        [
            _delivery(receipt="receipt-1"),
            _delivery(receipt="receipt-2"),
        ]
    )
    processor = _RecordingProcessor(
        lambda command, attempt: _command_ack(command, duplicate=attempt == 2)
    )

    first = consume(queue, _EXPECTED_SENDER_ID, processor)
    second = consume(queue, _EXPECTED_SENDER_ID, processor)

    assert (first.status, second.status) == ("APPLIED", "APPLIED")
    assert [command.deletion_id for command in processor.commands] == [
        UUID(_DELETION_ID),
        UUID(_DELETION_ID),
    ]
    assert queue.deleted == ["receipt-1", "receipt-2"]


def test_queue_delete_failure_is_reported_after_processor_success() -> None:
    consume = _consumer_symbol("consume_private_deletion_once")
    queue = _RecordingQueue([_delivery()], fail_delete=True)
    processor = _RecordingProcessor(lambda command, _attempt: _command_ack(command))

    result = consume(queue, _EXPECTED_SENDER_ID, processor)

    assert result.status == "DELETE_FAILED"
    assert len(processor.commands) == 1
    assert queue.delete_attempts == ["receipt-1"]
    assert queue.deleted == []


def test_sender_mismatch_does_not_parse_process_or_delete() -> None:
    consume = _consumer_symbol("consume_private_deletion_once")
    queue = _RecordingQueue([_delivery(sender_id="AROADIFFERENTROLE:worker")])

    def unexpected_processor(
        command: PrivateDeletionCommandV2,
        attempt: int,
    ) -> PrivateDeletionAckV2:
        raise AssertionError((command, attempt))

    processor = _RecordingProcessor(unexpected_processor)

    result = consume(queue, _EXPECTED_SENDER_ID, processor)

    assert result.status == "UNAUTHENTICATED"
    assert processor.commands == []
    assert queue.delete_attempts == []
