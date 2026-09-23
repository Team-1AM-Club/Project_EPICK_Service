from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.models.application_workspace import ApplicationProject
from app.models.deletion import DeletionRequest, DeletionTarget
from app.models.identity import User
from app.runtime import w2_private_deletion_boundary as boundary

OWNER_ID = UUID("22222222-2222-4222-8222-222222222222")
REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")
TARGET_ID = UUID("33333333-3333-4333-8333-333333333333")
PROJECT_ID = UUID("44444444-4444-4444-8444-444444444444")
ATTEMPT_ID = UUID("55555555-5555-4555-8555-555555555555")
DEDUP_ID = UUID("66666666-6666-4666-8666-666666666666")
ISSUED_AT = datetime(2026, 9, 23, 0, 0, tzinfo=UTC)


def _context(
    *, project: bool = False
) -> tuple[User, DeletionRequest, DeletionTarget, ApplicationProject | None]:
    owner = User(id=OWNER_ID, account_status="DELETION_PENDING", deletion_epoch=7)
    request = DeletionRequest(
        id=REQUEST_ID,
        owner_user_id=OWNER_ID,
        owner_deletion_epoch=7,
        target_type="PROJECT" if project else "ACCOUNT",
        target_id=PROJECT_ID if project else None,
        status="RUNNING",
    )
    target = DeletionTarget(
        id=TARGET_ID,
        deletion_request_id=REQUEST_ID,
        store_type="W2_SOURCE_RUNTIME",
        resource_type="PROJECT_PRIVATE_SCOPE" if project else "OWNER_PRIVATE_SCOPE",
        resource_id=PROJECT_ID if project else OWNER_ID,
        status="QUEUED",
    )
    project_row = ApplicationProject(id=PROJECT_ID, owner_user_id=OWNER_ID) if project else None
    return owner, request, target, project_row


def _serialize(
    *,
    owner: User | None = None,
    request: DeletionRequest | None = None,
    target: DeletionTarget | None = None,
    project: ApplicationProject | None = None,
) -> str:
    default_owner, default_request, default_target, _ = _context()
    return boundary.serialize_authorized_w2_deletion_envelope(
        owner=owner or default_owner,
        request=request or default_request,
        target=target or default_target,
        project=project,
        attempt_ids=(ATTEMPT_ID,),
        request_deduplication_ids=(DEDUP_ID,),
        private_reference_keys=("owner/2222/private/1",),
        issued_at=ISSUED_AT,
    )


def test_account_envelope_binds_w1_authority_and_replays_identically() -> None:
    first = _serialize()
    body = json.loads(first)

    assert body["schema_version"] == "w1.private.w2-deletion-dispatch.v1"
    assert body["message_type"] == "w1.private.w2.deletion-command.v1"
    assert body["visibility_scope"] == "PRIVATE"
    assert body["producer"] == "w1"
    assert body["message_id"] == str(TARGET_ID)
    assert body["deletion_request_id"] == str(REQUEST_ID)
    assert body["scope"] == {"type": "ACCOUNT", "ref": str(OWNER_ID)}
    assert body["payload"]["deletion_id"] == str(TARGET_ID)
    assert body["payload"]["owner_user_id"] == str(OWNER_ID)
    assert body["payload"]["deletion_epoch"] == 7
    assert _serialize() == first


def test_project_envelope_requires_owned_project() -> None:
    owner, request, target, project = _context(project=True)
    owner.account_status = "ACTIVE"
    body = json.loads(_serialize(owner=owner, request=request, target=target, project=project))
    assert body["scope"] == {"type": "PROJECT", "ref": str(PROJECT_ID)}

    assert project is not None
    project.owner_user_id = UUID("77777777-7777-4777-8777-777777777777")
    with pytest.raises(boundary.W2PrivateDeletionBoundaryError):
        _serialize(owner=owner, request=request, target=target, project=project)


@pytest.mark.parametrize("invalid", ["owner", "epoch", "target", "status", "scope"])
def test_envelope_rejects_foreign_or_stale_w1_context(invalid: str) -> None:
    owner, request, target, _ = _context()
    if invalid == "owner":
        request.owner_user_id = UUID("77777777-7777-4777-8777-777777777777")
    elif invalid == "epoch":
        owner.deletion_epoch = 8
    elif invalid == "target":
        target.deletion_request_id = UUID("77777777-7777-4777-8777-777777777777")
    elif invalid == "status":
        target.status = "ACKNOWLEDGED"
    else:
        target.resource_id = UUID("77777777-7777-4777-8777-777777777777")

    with pytest.raises(boundary.W2PrivateDeletionBoundaryError):
        _serialize(owner=owner, request=request, target=target)


def test_ack_completion_accepts_only_current_bound_applied_or_duplicate() -> None:
    owner, request, target, _ = _context()
    target.status = "DISPATCHED"
    ack = {
        "schema_version": "w2.private-deletion-ack.v1",
        "deletion_id": str(TARGET_ID),
        "owner_user_id": str(OWNER_ID),
        "deletion_epoch": 7,
        "outcome": "APPLIED",
    }
    assert (
        boundary.require_authorized_w2_deletion_ack(
            body=json.dumps(ack), owner=owner, request=request, target=target
        )
        == TARGET_ID
    )
    ack["outcome"] = "DUPLICATE"
    assert (
        boundary.require_authorized_w2_deletion_ack(
            body=json.dumps(ack), owner=owner, request=request, target=target
        )
        == TARGET_ID
    )
    ack["outcome"] = "STALE"
    with pytest.raises(boundary.W2PrivateDeletionBoundaryError):
        boundary.require_authorized_w2_deletion_ack(
            body=json.dumps(ack), owner=owner, request=request, target=target
        )


def test_ack_rejects_epoch_advanced_after_dispatch() -> None:
    owner, request, target, _ = _context()
    target.status = "DISPATCHED"
    owner.deletion_epoch = 8
    body = json.dumps(
        {
            "schema_version": "w2.private-deletion-ack.v1",
            "deletion_id": str(TARGET_ID),
            "owner_user_id": str(OWNER_ID),
            "deletion_epoch": 7,
            "outcome": "APPLIED",
        }
    )
    with pytest.raises(boundary.W2PrivateDeletionBoundaryError):
        boundary.require_authorized_w2_deletion_ack(
            body=body, owner=owner, request=request, target=target
        )


def test_duplicate_ack_after_account_completion_remains_idempotent() -> None:
    owner, request, target, _ = _context()
    owner.account_status = "DELETED"
    request.status = "COMPLETED"
    target.status = "ACKNOWLEDGED"
    body = json.dumps(
        {
            "schema_version": "w2.private-deletion-ack.v1",
            "deletion_id": str(TARGET_ID),
            "owner_user_id": str(OWNER_ID),
            "deletion_epoch": 7,
            "outcome": "DUPLICATE",
        }
    )

    assert (
        boundary.require_authorized_w2_deletion_ack(
            body=body, owner=owner, request=request, target=target
        )
        == TARGET_ID
    )


def test_ack_rejects_target_not_yet_dispatched() -> None:
    owner, request, target, _ = _context()
    body = json.dumps(
        {
            "schema_version": "w2.private-deletion-ack.v1",
            "deletion_id": str(TARGET_ID),
            "owner_user_id": str(OWNER_ID),
            "deletion_epoch": 7,
            "outcome": "APPLIED",
        }
    )

    with pytest.raises(boundary.W2PrivateDeletionBoundaryError):
        boundary.require_authorized_w2_deletion_ack(
            body=body, owner=owner, request=request, target=target
        )
