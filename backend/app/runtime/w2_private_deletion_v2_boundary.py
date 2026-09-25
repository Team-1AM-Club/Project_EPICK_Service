"""Fail-closed W1 authority binding for the pinned W2 deletion v2 payload.

This pure boundary does not publish or apply an ACK. A caller must recheck the
locked W1 rows at dispatch/application time and retain the same outbox body on
retry; production routing remains disabled until the complete seam is wired.
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

from app.models.application_workspace import ApplicationProject
from app.models.deletion import DeletionRequest, DeletionTarget
from app.models.identity import User
from app.runtime.w2_private_deletion_v2 import (
    W2PrivateDeletionV2Error,
    parse_w2_private_deletion_ack_v2,
    require_w2_ack_completion_v2,
    serialize_w2_private_deletion_command_v2,
)

_MAX_BODY_BYTES = 16 * 1024
_DISPATCH_STATUSES = frozenset({"RUNNING", "PARTIALLY_COMPLETED", "FAILED_RETRYABLE"})
_ENVELOPE_KEYS = frozenset(
    {
        "schema_version",
        "message_type",
        "message_id",
        "producer",
        "visibility_scope",
        "occurred_at",
        "deletion_request_id",
        "deletion_target_id",
        "owner_user_id",
        "deletion_epoch",
        "scope",
        "payload_schema_version",
        "payload",
    }
)


class W2PrivateDeletionV2BoundaryError(ValueError):
    pass


def validate_authorized_w2_deletion_envelope_v2(body: str) -> dict[str, object]:
    """Reject altered outer/inner bindings before W1 publishes a v2 command."""
    if not isinstance(body, str) or len(body.encode("utf-8")) > _MAX_BODY_BYTES:
        raise W2PrivateDeletionV2BoundaryError("W2_DELETION_V2_ENVELOPE_INVALID")
    try:
        envelope = json.loads(body)
        if not isinstance(envelope, dict) or envelope.keys() != _ENVELOPE_KEYS:
            raise ValueError
        payload = envelope["payload"]
        if not isinstance(payload, dict):
            raise ValueError
        scope = payload.get("scope")
        if scope == {"type": "ACCOUNT"}:
            project_id = None
        elif (
            isinstance(scope, dict)
            and scope.keys() == {"type", "project_id"}
            and scope.get("type") == "PROJECT"
        ):
            project_id = _exact_uuid(scope["project_id"])
        else:
            raise ValueError
        deletion_id = _exact_uuid(payload.get("deletion_id"))
        owner_id = _exact_uuid(payload.get("owner_user_id"))
        _exact_uuid(envelope["deletion_request_id"])
        expected_payload = json.loads(
            serialize_w2_private_deletion_command_v2(
                deletion_id=deletion_id,
                owner_user_id=owner_id,
                deletion_epoch=payload.get("deletion_epoch"),
                project_id=project_id,
            )
        )
        issued_at = envelope["occurred_at"]
        if not isinstance(issued_at, str) or not issued_at.endswith("Z"):
            raise ValueError
        timestamp = datetime.fromisoformat(issued_at.replace("Z", "+00:00"))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError
        if (
            payload != expected_payload
            or envelope["schema_version"] != "w1.private.w2-deletion-dispatch.v2"
            or envelope["message_type"] != "w1.private.w2.deletion-command.v2"
            or envelope["message_id"] != str(deletion_id)
            or envelope["producer"] != "w1"
            or envelope["visibility_scope"] != "PRIVATE"
            or envelope["deletion_target_id"] != str(deletion_id)
            or envelope["owner_user_id"] != str(owner_id)
            or envelope["deletion_epoch"] != expected_payload["deletion_epoch"]
            or envelope["scope"] != expected_payload["scope"]
            or envelope["payload_schema_version"] != "w2.private-deletion.v2"
        ):
            raise ValueError
    except (TypeError, ValueError, W2PrivateDeletionV2Error) as error:
        raise W2PrivateDeletionV2BoundaryError("W2_DELETION_V2_ENVELOPE_INVALID") from error
    return envelope


def _exact_uuid(value: object) -> UUID:
    if not isinstance(value, str):
        raise ValueError
    parsed = UUID(value)
    if str(parsed) != value:
        raise ValueError
    return parsed


def _scope_from_current_w1(
    *,
    owner: User,
    request: DeletionRequest,
    target: DeletionTarget,
    project: ApplicationProject | None,
    for_ack: bool,
) -> UUID | None:
    allowed_request_statuses = _DISPATCH_STATUSES | {"COMPLETED"} if for_ack else _DISPATCH_STATUSES
    allowed_target_statuses = (
        {"DISPATCHED", "ACKNOWLEDGED"} if for_ack else {"QUEUED", "DISPATCHED"}
    )
    if (
        request.owner_user_id != owner.id
        or request.owner_deletion_epoch is None
        or isinstance(request.owner_deletion_epoch, bool)
        or not 1 <= request.owner_deletion_epoch <= 2**63 - 1
        or request.owner_deletion_epoch != owner.deletion_epoch
        or request.status not in allowed_request_statuses
        or target.deletion_request_id != request.id
        or target.store_type != "W2_SOURCE_RUNTIME"
        or target.status not in allowed_target_statuses
    ):
        raise W2PrivateDeletionV2BoundaryError("W2_DELETION_V2_W1_BINDING_INVALID")

    if request.target_type == "ACCOUNT":
        owner_current = owner.account_status == "DELETION_PENDING" or (
            for_ack
            and request.status == "COMPLETED"
            and target.status == "ACKNOWLEDGED"
            and owner.account_status == "DELETED"
        )
        if (
            request.target_id is not None
            or project is not None
            or not owner_current
            or target.resource_type != "OWNER_PRIVATE_SCOPE"
            or target.resource_id != owner.id
        ):
            raise W2PrivateDeletionV2BoundaryError("W2_DELETION_V2_ACCOUNT_SCOPE_INVALID")
        return None
    if request.target_type == "PROJECT":
        if (
            project is None
            or request.target_id != project.id
            or project.owner_user_id != owner.id
            or target.resource_type != "PROJECT_PRIVATE_SCOPE"
            or target.resource_id != project.id
        ):
            raise W2PrivateDeletionV2BoundaryError("W2_DELETION_V2_PROJECT_SCOPE_INVALID")
        return project.id
    raise W2PrivateDeletionV2BoundaryError("W2_DELETION_V2_SCOPE_UNSUPPORTED")


def serialize_authorized_w2_deletion_envelope_v2(
    *,
    owner: User,
    request: DeletionRequest,
    target: DeletionTarget,
    project: ApplicationProject | None,
    issued_at: datetime,
) -> str:
    project_id = _scope_from_current_w1(
        owner=owner, request=request, target=target, project=project, for_ack=False
    )
    if issued_at.tzinfo is None or issued_at.utcoffset() is None:
        raise W2PrivateDeletionV2BoundaryError("W2_DELETION_V2_ISSUED_AT_INVALID")
    try:
        payload = json.loads(
            serialize_w2_private_deletion_command_v2(
                deletion_id=target.id,
                owner_user_id=owner.id,
                deletion_epoch=request.owner_deletion_epoch,
                project_id=project_id,
            )
        )
    except W2PrivateDeletionV2Error as error:
        raise W2PrivateDeletionV2BoundaryError("W2_DELETION_V2_COMMAND_INVALID") from error
    envelope = {
        "schema_version": "w1.private.w2-deletion-dispatch.v2",
        "message_type": "w1.private.w2.deletion-command.v2",
        "message_id": str(target.id),
        "producer": "w1",
        "visibility_scope": "PRIVATE",
        "occurred_at": issued_at.isoformat().replace("+00:00", "Z"),
        "deletion_request_id": str(request.id),
        "deletion_target_id": str(target.id),
        "owner_user_id": str(owner.id),
        "deletion_epoch": request.owner_deletion_epoch,
        "scope": payload["scope"],
        "payload_schema_version": "w2.private-deletion.v2",
        "payload": payload,
    }
    body = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(body.encode("utf-8")) > _MAX_BODY_BYTES:
        raise W2PrivateDeletionV2BoundaryError("W2_DELETION_V2_ENVELOPE_TOO_LARGE")
    return body


def require_authorized_w2_deletion_ack_v2(
    *,
    body: str,
    owner: User,
    request: DeletionRequest,
    target: DeletionTarget,
    project: ApplicationProject | None,
) -> UUID:
    project_id = _scope_from_current_w1(
        owner=owner, request=request, target=target, project=project, for_ack=True
    )
    try:
        return require_w2_ack_completion_v2(
            parse_w2_private_deletion_ack_v2(body),
            deletion_id=target.id,
            owner_user_id=owner.id,
            deletion_epoch=request.owner_deletion_epoch,
            project_id=project_id,
        )
    except W2PrivateDeletionV2Error as error:
        raise W2PrivateDeletionV2BoundaryError("W2_DELETION_V2_ACK_INVALID") from error
