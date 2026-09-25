"""Pinned W2 private-deletion v2 payload boundary; v1 remains historical only."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

_MAX_BODY_BYTES = 16 * 1024
_MAX_EPOCH = 2**63 - 1
_ACK_KEYS = frozenset(
    {"schema_version", "deletion_id", "owner_user_id", "deletion_epoch", "scope", "outcome"}
)


class W2PrivateDeletionV2Error(ValueError):
    pass


def _canonical_uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise W2PrivateDeletionV2Error("W2_DELETION_V2_UUID_INVALID")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise W2PrivateDeletionV2Error("W2_DELETION_V2_UUID_INVALID") from error
    if str(parsed) != value:
        raise W2PrivateDeletionV2Error("W2_DELETION_V2_UUID_INVALID")
    return parsed


def _epoch(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_EPOCH:
        raise W2PrivateDeletionV2Error("W2_DELETION_V2_EPOCH_INVALID")
    return value


def _scope(value: object) -> tuple[Literal["ACCOUNT", "PROJECT"], UUID | None]:
    if not isinstance(value, dict):
        raise W2PrivateDeletionV2Error("W2_DELETION_V2_SCOPE_INVALID")
    if value == {"type": "ACCOUNT"}:
        return "ACCOUNT", None
    if value.keys() == {"type", "project_id"} and value["type"] == "PROJECT":
        return "PROJECT", _canonical_uuid(value["project_id"])
    raise W2PrivateDeletionV2Error("W2_DELETION_V2_SCOPE_INVALID")


def _scope_payload(project_id: UUID | None) -> dict[str, str]:
    if project_id is None:
        return {"type": "ACCOUNT"}
    if not isinstance(project_id, UUID):
        raise W2PrivateDeletionV2Error("W2_DELETION_V2_PROJECT_INVALID")
    return {"type": "PROJECT", "project_id": str(project_id)}


def serialize_w2_private_deletion_command_v2(
    *, deletion_id: UUID, owner_user_id: UUID, deletion_epoch: int, project_id: UUID | None
) -> str:
    """Return the same canonical body for each delivery of one authorized deletion."""
    if not isinstance(deletion_id, UUID) or not isinstance(owner_user_id, UUID):
        raise W2PrivateDeletionV2Error("W2_DELETION_V2_UUID_INVALID")
    payload = {
        "schema_version": "w2.private-deletion.v2",
        "deletion_id": str(deletion_id),
        "owner_user_id": str(owner_user_id),
        "deletion_epoch": _epoch(deletion_epoch),
        "scope": _scope_payload(project_id),
    }
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(body.encode("utf-8")) > _MAX_BODY_BYTES:
        raise W2PrivateDeletionV2Error("W2_DELETION_V2_BODY_TOO_LARGE")
    return body


@dataclass(frozen=True, slots=True)
class W2PrivateDeletionAckV2:
    deletion_id: UUID
    owner_user_id: UUID
    deletion_epoch: int
    scope_type: Literal["ACCOUNT", "PROJECT"]
    project_id: UUID | None
    outcome: Literal["APPLIED", "DUPLICATE"]


def parse_w2_private_deletion_ack_v2(body: str) -> W2PrivateDeletionAckV2:
    if not isinstance(body, str) or len(body.encode("utf-8")) > _MAX_BODY_BYTES:
        raise W2PrivateDeletionV2Error("W2_DELETION_V2_ACK_BODY_INVALID")
    try:
        payload = json.loads(body)
    except (TypeError, ValueError) as error:
        raise W2PrivateDeletionV2Error("W2_DELETION_V2_ACK_BODY_INVALID") from error
    if not isinstance(payload, dict) or payload.keys() != _ACK_KEYS:
        raise W2PrivateDeletionV2Error("W2_DELETION_V2_ACK_FIELDS_INVALID")
    if payload["schema_version"] != "w2.private-deletion-ack.v2" or payload["outcome"] not in (
        "APPLIED",
        "DUPLICATE",
    ):
        raise W2PrivateDeletionV2Error("W2_DELETION_V2_ACK_VALUES_INVALID")
    scope_type, project_id = _scope(payload["scope"])
    return W2PrivateDeletionAckV2(
        deletion_id=_canonical_uuid(payload["deletion_id"]),
        owner_user_id=_canonical_uuid(payload["owner_user_id"]),
        deletion_epoch=_epoch(payload["deletion_epoch"]),
        scope_type=scope_type,
        project_id=project_id,
        outcome=payload["outcome"],
    )


def require_w2_ack_completion_v2(
    ack: W2PrivateDeletionAckV2,
    *,
    deletion_id: UUID,
    owner_user_id: UUID,
    deletion_epoch: int,
    project_id: UUID | None,
) -> UUID:
    if (
        ack.deletion_id != deletion_id
        or ack.owner_user_id != owner_user_id
        or ack.deletion_epoch != deletion_epoch
        or ack.project_id != project_id
        or ack.scope_type != ("ACCOUNT" if project_id is None else "PROJECT")
        or ack.outcome not in ("APPLIED", "DUPLICATE")
    ):
        raise W2PrivateDeletionV2Error("W2_DELETION_V2_ACK_BINDING_INVALID")
    return ack.deletion_id
