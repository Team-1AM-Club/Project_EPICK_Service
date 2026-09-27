"""Fail-closed staging of one reconciled W2 deletion after DLQ investigation.

This module never reads or redrives SQS. A separate operator must confirm the
W2 receipt and pause the dedicated route before invoking it.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.deletion import DeletionRequest, DeletionTarget
from app.models.identity import User
from app.models.jobs import OutboxMessage
from app.runtime.outbox_relay import OutboxPayloadError, OutboxRelay
from app.services.deletion import DeletionOrchestrationService

_BODY_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_INCIDENT_ID = re.compile(r"[A-Z][A-Z0-9-]{5,63}\Z")
_RETRIABLE_409_CODES = frozenset(
    {
        "W2_DELETION_V2_ACK_INVALID",
        "W2_DELETION_V2_TARGET_NOT_FOUND",
        "W2_DELETION_V2_BINDING_INVALID",
        "W2_DELETION_V2_STATE_INVALID",
        "W2_DELETION_V2_CONFLICT",
    }
)


class ManualRetryRefused(RuntimeError):
    """A missing or stale operator proof must never stage another deletion."""


@dataclass(frozen=True)
class ManualRetryEvidence:
    deletion_id: UUID
    original_outbox_id: UUID
    expected_epoch: int
    expected_body_sha256: str
    ack_code: str
    w2_receipt_outcome: str
    incident_id: str
    route_paused: bool


@dataclass(frozen=True)
class ManualRetryResult:
    new_outbox_id: UUID
    deletion_id: UUID
    body_sha256: str


def _body_digest(body: str) -> str:
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def stage_reconciled_w2_deletion_retry(
    session: Session, *, evidence: ManualRetryEvidence
) -> ManualRetryResult:
    """Stage one new W1 outbox only when the original DLQ wire is current.

    The caller owns the transaction. Any refusal must roll it back. W2 receipt
    reconciliation is a named external approval, not inferred from W1 rows.
    """

    if not session.in_transaction():
        raise ManualRetryRefused("TRANSACTION_REQUIRED")
    if (
        not evidence.route_paused
        or not isinstance(evidence.expected_epoch, int)
        or isinstance(evidence.expected_epoch, bool)
        or evidence.expected_epoch < 1
        or _BODY_DIGEST.fullmatch(evidence.expected_body_sha256) is None
        or _INCIDENT_ID.fullmatch(evidence.incident_id) is None
        or evidence.ack_code not in _RETRIABLE_409_CODES
        or evidence.w2_receipt_outcome not in {"APPLIED", "DUPLICATE"}
    ):
        raise ManualRetryRefused("OPERATOR_EVIDENCE_INVALID")

    target_hint = session.get(DeletionTarget, evidence.deletion_id)
    request_hint = (
        session.get(DeletionRequest, target_hint.deletion_request_id)
        if target_hint is not None
        else None
    )
    if request_hint is None or request_hint.owner_user_id is None:
        raise ManualRetryRefused("TARGET_NOT_CURRENT")

    owner = session.scalar(
        select(User)
        .where(User.id == request_hint.owner_user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    request = session.scalar(
        select(DeletionRequest)
        .where(DeletionRequest.id == request_hint.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    target = session.scalar(
        select(DeletionTarget)
        .where(DeletionTarget.id == evidence.deletion_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        owner is None
        or request is None
        or target is None
        or request.owner_user_id != owner.id
        or target.deletion_request_id != request.id
        or target.store_type != "W2_SOURCE_RUNTIME"
        or target.status != "DISPATCHED"
        or request.status not in {"RUNNING", "PARTIALLY_COMPLETED", "FAILED_RETRYABLE"}
        or request.owner_deletion_epoch != evidence.expected_epoch
        or owner.deletion_epoch != evidence.expected_epoch
    ):
        raise ManualRetryRefused("TARGET_NOT_CURRENT")

    original = session.scalar(
        select(OutboxMessage)
        .where(
            OutboxMessage.id == evidence.original_outbox_id,
            OutboxMessage.deletion_target_id == target.id,
            OutboxMessage.aggregate_revision == target.attempts,
        )
        .with_for_update()
    )
    if original is None or original.status != "PUBLISHED":
        raise ManualRetryRefused("ORIGINAL_OUTBOX_NOT_CURRENT")
    try:
        original_body = OutboxRelay._serialize_w2_private_deletion_v2(
            session=session, message=original
        )
    except OutboxPayloadError as error:
        raise ManualRetryRefused("ORIGINAL_BINDING_INVALID") from error
    body_sha256 = _body_digest(original_body)
    if not hmac.compare_digest(body_sha256, evidence.expected_body_sha256):
        raise ManualRetryRefused("DLQ_BODY_MISMATCH")

    service = DeletionOrchestrationService(session)
    service.record_target_failure(
        owner_user_id=owner.id,
        deletion_request_id=request.id,
        deletion_target_id=target.id,
        failure_code="W2_ACK_MANUAL_RETRY",
    )
    service.retry_target(
        owner_user_id=owner.id,
        deletion_request_id=request.id,
        deletion_target_id=target.id,
    )
    replacement = session.scalar(
        select(OutboxMessage).where(
            OutboxMessage.deletion_target_id == target.id,
            OutboxMessage.aggregate_revision == target.attempts,
        )
    )
    if replacement is None or replacement.id == original.id or replacement.status != "PENDING":
        raise ManualRetryRefused("REPLACEMENT_OUTBOX_INVALID")
    try:
        replacement_body = OutboxRelay._serialize_w2_private_deletion_v2(
            session=session, message=replacement
        )
    except OutboxPayloadError as error:
        raise ManualRetryRefused("REPLACEMENT_BINDING_INVALID") from error
    if not hmac.compare_digest(original_body, replacement_body):
        raise ManualRetryRefused("REPLACEMENT_BODY_MISMATCH")
    return ManualRetryResult(
        new_outbox_id=replacement.id,
        deletion_id=target.id,
        body_sha256=body_sha256,
    )
