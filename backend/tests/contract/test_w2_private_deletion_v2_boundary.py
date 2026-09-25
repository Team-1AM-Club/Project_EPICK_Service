from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.models.application_workspace import ApplicationProject
from app.models.deletion import DeletionRequest, DeletionTarget
from app.models.identity import User
from app.runtime import w2_private_deletion_v2_boundary as boundary
from app.runtime.w2_private_deletion_v2_boundary import (
    W2PrivateDeletionV2BoundaryError,
    require_authorized_w2_deletion_ack_v2,
    serialize_authorized_w2_deletion_envelope_v2,
)

OWNER = UUID("22222222-2222-4222-8222-222222222222")
REQUEST = UUID("11111111-1111-4111-8111-111111111111")
TARGET = UUID("33333333-3333-4333-8333-333333333333")
PROJECT = UUID("44444444-4444-4444-8444-444444444444")
ISSUED_AT = datetime(2026, 9, 25, tzinfo=UTC)


def _context(
    project: bool = False,
) -> tuple[User, DeletionRequest, DeletionTarget, ApplicationProject | None]:
    owner = User(
        id=OWNER,
        account_status="ACTIVE" if project else "DELETION_PENDING",
        deletion_epoch=7,
    )
    request = DeletionRequest(
        id=REQUEST,
        owner_user_id=OWNER,
        owner_deletion_epoch=7,
        target_type="PROJECT" if project else "ACCOUNT",
        target_id=PROJECT if project else None,
        status="RUNNING",
    )
    target = DeletionTarget(
        id=TARGET,
        deletion_request_id=REQUEST,
        store_type="W2_SOURCE_RUNTIME",
        resource_type="PROJECT_PRIVATE_SCOPE" if project else "OWNER_PRIVATE_SCOPE",
        resource_id=PROJECT if project else OWNER,
        status="QUEUED",
    )
    project_row = ApplicationProject(id=PROJECT, owner_user_id=OWNER) if project else None
    return owner, request, target, project_row


@pytest.mark.parametrize("project", [False, True])
def test_v2_envelope_binds_current_w1_scope_to_inner_command(project: bool) -> None:
    owner, request, target, project_row = _context(project)
    body = serialize_authorized_w2_deletion_envelope_v2(
        owner=owner,
        request=request,
        target=target,
        project=project_row,
        issued_at=ISSUED_AT,
    )
    envelope = json.loads(body)
    command = envelope["payload"]
    scope = {"type": "PROJECT", "project_id": str(PROJECT)} if project else {"type": "ACCOUNT"}
    assert envelope["schema_version"] == "w1.private.w2-deletion-dispatch.v2"
    assert envelope["visibility_scope"] == "PRIVATE"
    assert envelope["scope"] == scope == command["scope"]
    assert envelope["owner_user_id"] == command["owner_user_id"] == str(OWNER)
    assert envelope["deletion_target_id"] == command["deletion_id"] == str(TARGET)
    assert envelope["deletion_epoch"] == command["deletion_epoch"] == 7
    assert body == serialize_authorized_w2_deletion_envelope_v2(
        owner=owner,
        request=request,
        target=target,
        project=project_row,
        issued_at=ISSUED_AT,
    )


@pytest.mark.parametrize("invalid", ["owner", "epoch", "project_owner", "target", "status"])
def test_v2_envelope_rejects_mismatched_or_stale_authority(invalid: str) -> None:
    owner, request, target, project = _context(project=True)
    if invalid == "owner":
        request.owner_user_id = UUID("77777777-7777-4777-8777-777777777777")
    elif invalid == "epoch":
        owner.deletion_epoch = 8
    elif invalid == "project_owner":
        assert project is not None
        project.owner_user_id = UUID("77777777-7777-4777-8777-777777777777")
    elif invalid == "target":
        target.resource_id = OWNER
    else:
        target.status = "ACKNOWLEDGED"
    with pytest.raises(W2PrivateDeletionV2BoundaryError):
        serialize_authorized_w2_deletion_envelope_v2(
            owner=owner,
            request=request,
            target=target,
            project=project,
            issued_at=ISSUED_AT,
        )


def test_v2_ack_rejects_scope_mismatch_and_legacy_version() -> None:
    owner, request, target, project = _context(project=True)
    target.status = "DISPATCHED"
    ack: dict[str, object] = {
        "schema_version": "w2.private-deletion-ack.v2",
        "deletion_id": str(TARGET),
        "owner_user_id": str(OWNER),
        "deletion_epoch": 7,
        "scope": {"type": "PROJECT", "project_id": str(PROJECT)},
        "outcome": "APPLIED",
    }
    assert (
        require_authorized_w2_deletion_ack_v2(
            body=json.dumps(ack), owner=owner, request=request, target=target, project=project
        )
        == TARGET
    )
    ack["scope"] = {"type": "ACCOUNT"}
    with pytest.raises(W2PrivateDeletionV2BoundaryError):
        require_authorized_w2_deletion_ack_v2(
            body=json.dumps(ack), owner=owner, request=request, target=target, project=project
        )
    ack["scope"] = {"type": "PROJECT", "project_id": str(PROJECT)}
    ack["schema_version"] = "w2.private-deletion-ack.v1"
    with pytest.raises(W2PrivateDeletionV2BoundaryError):
        require_authorized_w2_deletion_ack_v2(
            body=json.dumps(ack), owner=owner, request=request, target=target, project=project
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("owner_user_id", str(REQUEST)),
        ("deletion_target_id", str(REQUEST)),
        ("deletion_epoch", 8),
        ("scope", {"type": "ACCOUNT"}),
        ("payload_schema_version", "w2.private-deletion.v1"),
        ("message_type", "w1.private.w2.deletion-command.v1"),
    ],
)
def test_v2_dispatch_rejects_outer_inner_mismatch_or_v1(field: str, value: object) -> None:
    owner, request, target, project = _context(project=True)
    envelope = json.loads(
        serialize_authorized_w2_deletion_envelope_v2(
            owner=owner,
            request=request,
            target=target,
            project=project,
            issued_at=ISSUED_AT,
        )
    )
    envelope[field] = value
    with pytest.raises(W2PrivateDeletionV2BoundaryError):
        boundary.validate_authorized_w2_deletion_envelope_v2(json.dumps(envelope))


def test_v2_dispatch_accepts_exact_current_envelope() -> None:
    owner, request, target, project = _context(project=True)
    body = serialize_authorized_w2_deletion_envelope_v2(
        owner=owner, request=request, target=target, project=project, issued_at=ISSUED_AT
    )
    assert boundary.validate_authorized_w2_deletion_envelope_v2(body)["payload"][
        "deletion_id"
    ] == str(TARGET)
