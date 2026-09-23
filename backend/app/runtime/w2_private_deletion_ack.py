from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

_ACK_KEYS = frozenset(
    {"schema_version", "deletion_id", "owner_user_id", "deletion_epoch", "outcome"}
)
_MAX_EPOCH = 2**63 - 1


class W2PrivateDeletionAckError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class W2PrivateDeletionAck:
    deletion_id: UUID
    owner_user_id: UUID
    deletion_epoch: int
    outcome: Literal["APPLIED", "DUPLICATE", "STALE"]


def _uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise W2PrivateDeletionAckError("W2_DELETION_ACK_UUID_INVALID")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise W2PrivateDeletionAckError("W2_DELETION_ACK_UUID_INVALID") from error
    if str(parsed) != value:
        raise W2PrivateDeletionAckError("W2_DELETION_ACK_UUID_INVALID")
    return parsed


def parse_w2_private_deletion_ack(body: str) -> W2PrivateDeletionAck:
    if not isinstance(body, str) or len(body.encode("utf-8")) > 16 * 1024:
        raise W2PrivateDeletionAckError("W2_DELETION_ACK_BODY_INVALID")
    try:
        payload = json.loads(body)
    except (TypeError, ValueError) as error:
        raise W2PrivateDeletionAckError("W2_DELETION_ACK_BODY_INVALID") from error
    if not isinstance(payload, dict) or payload.keys() != _ACK_KEYS:
        raise W2PrivateDeletionAckError("W2_DELETION_ACK_FIELDS_INVALID")
    epoch = payload["deletion_epoch"]
    outcome = payload["outcome"]
    if (
        payload["schema_version"] != "w2.private-deletion-ack.v1"
        or isinstance(epoch, bool)
        or not isinstance(epoch, int)
        or not 1 <= epoch <= _MAX_EPOCH
        or outcome not in ("APPLIED", "DUPLICATE", "STALE")
    ):
        raise W2PrivateDeletionAckError("W2_DELETION_ACK_VALUES_INVALID")
    return W2PrivateDeletionAck(
        deletion_id=_uuid(payload["deletion_id"]),
        owner_user_id=_uuid(payload["owner_user_id"]),
        deletion_epoch=epoch,
        outcome=outcome,
    )


def require_w2_ack_completion(
    ack: W2PrivateDeletionAck,
    *,
    deletion_id: UUID,
    owner_user_id: UUID,
    deletion_epoch: int,
) -> UUID:
    if (
        ack.deletion_id != deletion_id
        or ack.owner_user_id != owner_user_id
        or ack.deletion_epoch != deletion_epoch
        or ack.outcome not in ("APPLIED", "DUPLICATE")
    ):
        raise W2PrivateDeletionAckError("W2_DELETION_ACK_BINDING_INVALID")
    return ack.deletion_id
