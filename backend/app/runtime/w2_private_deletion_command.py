from __future__ import annotations

import json
from collections.abc import Iterable
from uuid import UUID

_MAX_EPOCH = 2**63 - 1
_MAX_BODY_BYTES = 16 * 1024


class W2PrivateDeletionCommandError(ValueError):
    pass


def _uuid_values(values: Iterable[UUID], field: str) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise W2PrivateDeletionCommandError(f"{field} must be UUID values")
    items = tuple(values)
    if any(not isinstance(item, UUID) for item in items) or len(items) != len(set(items)):
        raise W2PrivateDeletionCommandError(f"{field} must contain unique UUID values")
    return sorted(str(item) for item in items)


def _reference_values(values: Iterable[str]) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise W2PrivateDeletionCommandError("private_reference_keys must be a sequence")
    items = tuple(values)
    if any(not isinstance(item, str) or not item for item in items) or len(items) != len(
        set(items)
    ):
        raise W2PrivateDeletionCommandError(
            "private_reference_keys must be unique nonempty strings"
        )
    return sorted(items)


def serialize_w2_private_deletion_command(
    *,
    deletion_id: UUID,
    owner_user_id: UUID,
    deletion_epoch: int,
    attempt_ids: Iterable[UUID],
    request_deduplication_ids: Iterable[UUID],
    private_reference_keys: Iterable[str],
) -> str:
    """Serialize an already-authorized W2 scope; never invent IDs or broaden it."""
    if not isinstance(deletion_id, UUID) or not isinstance(owner_user_id, UUID):
        raise W2PrivateDeletionCommandError("deletion and owner IDs must be UUIDs")
    if (
        isinstance(deletion_epoch, bool)
        or not isinstance(deletion_epoch, int)
        or not 1 <= deletion_epoch <= _MAX_EPOCH
    ):
        raise W2PrivateDeletionCommandError(
            "deletion_epoch must be a positive signed 64-bit integer"
        )
    attempts = _uuid_values(attempt_ids, "attempt_ids")
    deduplications = _uuid_values(request_deduplication_ids, "request_deduplication_ids")
    references = _reference_values(private_reference_keys)
    if not (attempts or deduplications or references):
        raise W2PrivateDeletionCommandError("W2 deletion scope cannot be unproven empty")
    payload = {
        "schema_version": "w2.private-deletion.v1",
        "deletion_id": str(deletion_id),
        "owner_user_id": str(owner_user_id),
        "deletion_epoch": deletion_epoch,
        "attempt_ids": attempts,
        "request_deduplication_ids": deduplications,
        "private_reference_keys": references,
    }
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(body.encode("utf-8")) > _MAX_BODY_BYTES:
        raise W2PrivateDeletionCommandError("W2 deletion command exceeds private transport limit")
    return body
