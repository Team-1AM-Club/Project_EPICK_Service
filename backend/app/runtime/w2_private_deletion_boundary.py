"""Fail-closed W1 binding for a future W2 private-deletion transport.

This module does not discover W2-owned IDs or publish messages. Those actions
require a W2-owned, completeness-proving scope contract before activation.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from app.models.application_workspace import ApplicationProject
from app.models.deletion import DeletionRequest, DeletionTarget
from app.models.identity import User
from app.runtime.w2_private_deletion_ack import (
    W2PrivateDeletionAckError,
    parse_w2_private_deletion_ack,
    require_w2_ack_completion,
)
from app.runtime.w2_private_deletion_command import (
    W2PrivateDeletionCommandError,
    serialize_w2_private_deletion_command,
)

_MAX_BODY_BYTES = 16 * 1024
_DISPATCH_STATUSES = frozenset({"RUNNING", "PARTIALLY_COMPLETED", "FAILED_RETRYABLE"})
_ACK_STATUSES = _DISPATCH_STATUSES | {"COMPLETED"}


class W2PrivateDeletionBoundaryError(ValueError):
    """W1 cannot prove this deletion belongs to the current private scope."""


def _require_w1_binding(
    *,
    owner: User,
    request: DeletionRequest,
    target: DeletionTarget,
    project: ApplicationProject | None,
    for_ack: bool,
) -> tuple[str, UUID]:
    if (
        request.owner_user_id != owner.id
        or request.owner_deletion_epoch is None
        or isinstance(request.owner_deletion_epoch, bool)
        or not 1 <= request.owner_deletion_epoch <= 2**63 - 1
        or request.owner_deletion_epoch != owner.deletion_epoch
        or request.status not in (_ACK_STATUSES if for_ack else _DISPATCH_STATUSES)
        or target.deletion_request_id != request.id
        or target.store_type != "W2_SOURCE_RUNTIME"
        or target.status
        not in ({"DISPATCHED", "ACKNOWLEDGED"} if for_ack else {"QUEUED", "DISPATCHED"})
    ):
        raise W2PrivateDeletionBoundaryError("W2_DELETION_W1_BINDING_INVALID")

    if request.target_type == "ACCOUNT":
        owner_status_current = owner.account_status == "DELETION_PENDING" or (
            for_ack
            and request.status == "COMPLETED"
            and target.status == "ACKNOWLEDGED"
            and owner.account_status == "DELETED"
        )
        if (
            request.target_id is not None
            or project is not None
            or not owner_status_current
            or target.resource_type != "OWNER_PRIVATE_SCOPE"
            or target.resource_id != owner.id
        ):
            raise W2PrivateDeletionBoundaryError("W2_DELETION_ACCOUNT_SCOPE_INVALID")
        return "ACCOUNT", owner.id

    if request.target_type == "PROJECT":
        if (
            project is None
            or request.target_id != project.id
            or project.owner_user_id != owner.id
            or target.resource_type != "PROJECT_PRIVATE_SCOPE"
            or target.resource_id != project.id
        ):
            raise W2PrivateDeletionBoundaryError("W2_DELETION_PROJECT_SCOPE_INVALID")
        return "PROJECT", project.id

    raise W2PrivateDeletionBoundaryError("W2_DELETION_SCOPE_UNSUPPORTED")


def serialize_authorized_w2_deletion_envelope(
    *,
    owner: User,
    request: DeletionRequest,
    target: DeletionTarget,
    project: ApplicationProject | None,
    attempt_ids: Iterable[UUID],
    request_deduplication_ids: Iterable[UUID],
    private_reference_keys: Iterable[str],
    issued_at: datetime,
) -> str:
    """Bind an externally proven W2 scope to W1's current deletion target.

    The caller must obtain the *complete* W2 scope through an approved W2
    interface. This function deliberately cannot infer it from W1 data.
    """
    scope_type, scope_ref = _require_w1_binding(
        owner=owner, request=request, target=target, project=project, for_ack=False
    )
    if issued_at.tzinfo is None or issued_at.utcoffset() is None:
        raise W2PrivateDeletionBoundaryError("W2_DELETION_ISSUED_AT_INVALID")
    try:
        payload = json.loads(
            serialize_w2_private_deletion_command(
                deletion_id=target.id,
                owner_user_id=owner.id,
                deletion_epoch=request.owner_deletion_epoch,
                attempt_ids=attempt_ids,
                request_deduplication_ids=request_deduplication_ids,
                private_reference_keys=private_reference_keys,
            )
        )
    except W2PrivateDeletionCommandError as error:
        raise W2PrivateDeletionBoundaryError("W2_DELETION_SCOPE_INVALID") from error

    envelope = {
        "schema_version": "w1.private.w2-deletion-dispatch.v1",
        "message_id": str(target.id),
        "message_type": "w1.private.w2.deletion-command.v1",
        "producer": "w1",
        "visibility_scope": "PRIVATE",
        "occurred_at": issued_at.isoformat().replace("+00:00", "Z"),
        "deletion_request_id": str(request.id),
        "scope": {"type": scope_type, "ref": str(scope_ref)},
        "payload_schema_version": "w2.private-deletion.v1",
        "payload": payload,
    }
    body = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(body.encode("utf-8")) > _MAX_BODY_BYTES:
        raise W2PrivateDeletionBoundaryError("W2_DELETION_ENVELOPE_TOO_LARGE")
    return body


def require_authorized_w2_deletion_ack(
    *,
    body: str,
    owner: User,
    request: DeletionRequest,
    target: DeletionTarget,
    project: ApplicationProject | None = None,
) -> UUID:
    """Check a W2 ACK against the still-current W1 target; do not mutate DB."""
    _require_w1_binding(owner=owner, request=request, target=target, project=project, for_ack=True)
    try:
        return require_w2_ack_completion(
            parse_w2_private_deletion_ack(body),
            deletion_id=target.id,
            owner_user_id=owner.id,
            deletion_epoch=request.owner_deletion_epoch,
        )
    except W2PrivateDeletionAckError as error:
        raise W2PrivateDeletionBoundaryError("W2_DELETION_ACK_INVALID") from error
