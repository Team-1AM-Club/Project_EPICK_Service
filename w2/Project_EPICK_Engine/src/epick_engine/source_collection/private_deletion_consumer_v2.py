"""Fail-closed W1 queue boundary for W2 private deletion v2 commands."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Protocol, cast

from epick_engine.source_collection.commit_gate_runtime import (
    ConsumeResult,
    _sender_matches,
    _strict_json_object,
)
from epick_engine.source_collection.private_deletion_v2 import (
    PrivateDeletionAckV2,
    PrivateDeletionCommandV2,
)
from epick_engine.source_collection.worker import (
    WorkerContractViolation,
    _require_private_deletion_keys,
    _require_private_deletion_uuid,
)

_ENVELOPE_KEYS = frozenset(
    {
        "schema_version",
        "message_type",
        "message_id",
        "producer",
        "occurred_at",
        "visibility_scope",
        "deletion_request_id",
        "deletion_target_id",
        "owner_user_id",
        "deletion_epoch",
        "scope",
        "payload_schema_version",
        "payload",
    }
)
_ENVELOPE_SCHEMA_VERSION = "w1.private.w2-deletion-dispatch.v2"
_MESSAGE_TYPE = "w1.private.w2.deletion-command.v2"
_PAYLOAD_SCHEMA_VERSION = "w2.private-deletion.v2"
_UTC_TIMESTAMP = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?Z"
)


class PrivateDeletionDeliveryV2(Protocol):
    @property
    def receipt_handle(self) -> str: ...

    @property
    def body(self) -> str: ...

    @property
    def sender_id(self) -> str | None: ...


class PrivateDeletionQueueV2(Protocol):
    def receive(self) -> Sequence[PrivateDeletionDeliveryV2]: ...

    def delete(self, receipt_handle: str) -> None: ...


class PrivateDeletionProcessorV2(Protocol):
    def __call__(self, command: PrivateDeletionCommandV2) -> PrivateDeletionAckV2 | None: ...


def _require_utc_timestamp(raw: object) -> None:
    if not isinstance(raw, str) or _UTC_TIMESTAMP.fullmatch(raw) is None:
        raise WorkerContractViolation("occurred_at must be an RFC3339 UTC timestamp")
    normalized = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise WorkerContractViolation("occurred_at must be an RFC3339 UTC timestamp") from error
    if parsed.utcoffset() != timedelta(0):
        raise WorkerContractViolation("occurred_at must be UTC")


def parse_w1_private_deletion_dispatch_v2(raw: object) -> PrivateDeletionCommandV2:
    """Validate W1's exact v2 envelope and return its bound W2 command."""

    if not isinstance(raw, Mapping):
        raise WorkerContractViolation("private deletion v2 envelope must be an object")
    envelope = cast(Mapping[str, object], raw)
    _require_private_deletion_keys(
        envelope,
        expected=_ENVELOPE_KEYS,
        payload_name="private deletion v2 envelope",
    )
    if (
        envelope["schema_version"] != _ENVELOPE_SCHEMA_VERSION
        or envelope["message_type"] != _MESSAGE_TYPE
        or envelope["producer"] != "w1"
        or envelope["visibility_scope"] != "PRIVATE"
        or envelope["payload_schema_version"] != _PAYLOAD_SCHEMA_VERSION
    ):
        raise WorkerContractViolation("private deletion v2 envelope constants are invalid")
    _require_utc_timestamp(envelope["occurred_at"])
    _require_private_deletion_uuid(envelope, "deletion_request_id")

    payload = envelope["payload"]
    if not isinstance(payload, Mapping):
        raise WorkerContractViolation("private deletion v2 payload must be an object")
    command = PrivateDeletionCommandV2.from_mapping(cast(Mapping[str, object], payload))
    deletion_id = str(command.deletion_id)
    if (
        envelope["message_id"] != deletion_id
        or envelope["deletion_target_id"] != deletion_id
        or envelope["owner_user_id"] != str(command.owner_user_id)
        or isinstance(envelope["deletion_epoch"], bool)
        or envelope["deletion_epoch"] != command.deletion_epoch
        or envelope["scope"] != command.scope.to_mapping()
    ):
        raise WorkerContractViolation("private deletion v2 envelope binding is invalid")
    return command


def _ack_matches_command(
    ack: PrivateDeletionAckV2 | None,
    command: PrivateDeletionCommandV2,
) -> bool:
    return (
        isinstance(ack, PrivateDeletionAckV2)
        and ack.deletion_id == command.deletion_id
        and ack.owner_user_id == command.owner_user_id
        and ack.deletion_epoch == command.deletion_epoch
        and ack.scope == command.scope
    )


def consume_private_deletion_once(
    queue: PrivateDeletionQueueV2,
    expected_sender_id: str,
    processor: PrivateDeletionProcessorV2,
) -> ConsumeResult:
    """Process at most one authenticated deletion and delete only after its ACK succeeds."""

    try:
        deliveries = queue.receive()
    except Exception:
        return ConsumeResult(status="RECEIVE_FAILED")
    if not deliveries:
        return ConsumeResult(status="EMPTY")
    delivery = deliveries[0]
    if not _sender_matches(delivery.sender_id, expected_sender_id):
        return ConsumeResult(status="UNAUTHENTICATED")
    try:
        envelope = _strict_json_object(delivery.body)
        command = parse_w1_private_deletion_dispatch_v2(envelope)
    except (TypeError, ValueError, UnicodeError, RecursionError, WorkerContractViolation):
        return ConsumeResult(status="MALFORMED")

    try:
        ack = processor(command)
    except Exception:
        return ConsumeResult(status="REJECTED")
    if not _ack_matches_command(ack, command):
        return ConsumeResult(status="REJECTED")

    try:
        queue.delete(delivery.receipt_handle)
    except Exception:
        return ConsumeResult(status="DELETE_FAILED")
    return ConsumeResult(status="APPLIED")
