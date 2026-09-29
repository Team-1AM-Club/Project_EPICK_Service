"""Test-only inspection; never a public endpoint or production logging surface."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from epick_engine.source_collection.commit_gate_store import (
    PrivateCommitGateAck,
    PrivateCommitStage,
    PrivateStagedOutbox,
)


def inspect_private_gate(
    session: Session, *, owner_ref: UUID, command_id: UUID
) -> dict[str, Any] | None:
    row = (
        session.execute(
            select(PrivateCommitStage.__table__).where(
                PrivateCommitStage.owner_ref == owner_ref,
                PrivateCommitStage.command_id == command_id,
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    result = dict(row)
    result["operation_revision"] = int(result["operation_revision"])
    result["max_purge_epoch"] = int(result["max_purge_epoch"])
    result["stage_payloads"] = list(
        session.scalars(
            select(PrivateStagedOutbox.payload).where(
                PrivateStagedOutbox.command_id == command_id,
            )
        )
    )
    result["stage_count"] = len(result["stage_payloads"])
    result["ack_count"] = session.scalar(
        select(func.count())
        .select_from(PrivateCommitGateAck)
        .where(
            PrivateCommitGateAck.command_id == command_id,
        )
    )
    return result
