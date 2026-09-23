from __future__ import annotations

import json
from uuid import UUID

import pytest

from app.runtime.w2_private_deletion_ack import (
    W2PrivateDeletionAckError,
    parse_w2_private_deletion_ack,
    require_w2_ack_completion,
)

_DELETION_ID = "11111111-1111-4111-8111-111111111111"
_OWNER_ID = "22222222-2222-4222-8222-222222222222"


def _ack(**overrides: object) -> str:
    payload: dict[str, object] = {
        "schema_version": "w2.private-deletion-ack.v1",
        "deletion_id": _DELETION_ID,
        "owner_user_id": _OWNER_ID,
        "deletion_epoch": 7,
        "outcome": "APPLIED",
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_parse_w2_private_deletion_ack_preserves_exact_binding() -> None:
    ack = parse_w2_private_deletion_ack(_ack())

    assert ack.deletion_id == UUID(_DELETION_ID)
    assert ack.owner_user_id == UUID(_OWNER_ID)
    assert ack.deletion_epoch == 7
    assert ack.outcome == "APPLIED"


@pytest.mark.parametrize(
    "body",
    [
        _ack(deletion_epoch=True),
        _ack(deletion_epoch=0),
        _ack(deletion_epoch=2**63),
        _ack(outcome="REJECTED"),
        _ack(schema_version="w2.private-deletion-ack.v2"),
        _ack(unexpected="unsafe"),
        _ack(deletion_id="not-a-uuid"),
    ],
)
def test_parse_w2_private_deletion_ack_rejects_invalid_contract(body: str) -> None:
    with pytest.raises(W2PrivateDeletionAckError):
        parse_w2_private_deletion_ack(body)


def test_w2_ack_completion_requires_current_target_binding() -> None:
    ack = parse_w2_private_deletion_ack(_ack())

    assert require_w2_ack_completion(
        ack,
        deletion_id=UUID(_DELETION_ID),
        owner_user_id=UUID(_OWNER_ID),
        deletion_epoch=7,
    ) == UUID(_DELETION_ID)


@pytest.mark.parametrize(
    ("body", "deletion_id", "owner_id", "epoch"),
    [
        (_ack(), UUID("33333333-3333-4333-8333-333333333333"), UUID(_OWNER_ID), 7),
        (_ack(), UUID(_DELETION_ID), UUID("33333333-3333-4333-8333-333333333333"), 7),
        (_ack(), UUID(_DELETION_ID), UUID(_OWNER_ID), 8),
        (_ack(outcome="STALE"), UUID(_DELETION_ID), UUID(_OWNER_ID), 7),
    ],
)
def test_w2_ack_completion_rejects_stale_or_mismatched_target(
    body: str, deletion_id: UUID, owner_id: UUID, epoch: int
) -> None:
    with pytest.raises(W2PrivateDeletionAckError):
        require_w2_ack_completion(
            parse_w2_private_deletion_ack(body),
            deletion_id=deletion_id,
            owner_user_id=owner_id,
            deletion_epoch=epoch,
        )
