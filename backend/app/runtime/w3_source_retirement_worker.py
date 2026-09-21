from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.jobs import OutboxMessage
from app.models.sources import Source


class SourceRetirementReceipt(Protocol):
    outcome: str
    target_ref: UUID
    effective_at: datetime | None


def apply_source_retirement_receipt(
    *,
    session: Session,
    message: OutboxMessage,
    receipt: SourceRetirementReceipt,
) -> None:
    """Reconcile a terminal W3 Source-retirement result without changing registry facts."""

    payload = message.payload
    if not isinstance(payload, dict):
        raise ValueError("W3_SOURCE_RETIREMENT_COMMAND_INVALID")
    try:
        company_id = UUID(str(payload.get("company_id")))
    except ValueError as error:
        raise ValueError("W3_SOURCE_RETIREMENT_COMMAND_INVALID") from error
    source = session.scalar(
        select(Source)
        .where(
            Source.id == message.aggregate_id,
            Source.company_id == company_id,
        )
        .with_for_update()
    )
    if (
        source is None
        or payload.get("source_id") != str(source.id)
        or payload.get("target_ref") != str(source.id)
        or receipt.target_ref != source.id
    ):
        raise ValueError("W3_SOURCE_RETIREMENT_BINDING_MISMATCH")
    retired_at_value = payload.get("retired_at")
    if not isinstance(retired_at_value, str):
        raise ValueError("W3_SOURCE_RETIREMENT_COMMAND_INVALID")
    try:
        retired_at = datetime.fromisoformat(retired_at_value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("W3_SOURCE_RETIREMENT_COMMAND_INVALID") from error
    if receipt.outcome in {"APPLIED", "DUPLICATE"}:
        if receipt.effective_at != retired_at:
            raise ValueError("W3_SOURCE_RETIREMENT_EFFECTIVE_AT_MISMATCH")
        message.last_error_code = None
        message.last_error_at = None
        return
    message.last_error_code = f"W3_SOURCE_RETIREMENT_{receipt.outcome}"[:64]
    message.last_error_at = datetime.now(UTC)
