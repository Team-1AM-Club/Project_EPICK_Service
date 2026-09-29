"""Bind every persisted private gate ACK to its original canonical wire.

Revision ID: 0012_private_ack_wire_digest
Revises: 0011_private_ack_control_retention
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import RowMapping

revision: str = "0012_private_ack_wire_digest"
down_revision: str | None = "0011_private_ack_control_retention"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_SIZE = 500
_WIRE_DIGEST_CHECK = "wire_digest ~ '^sha256:[0-9a-f]{64}$'"


def _canonical_wire_digest(payload: object) -> str:
    """Migration-local copy: historical revisions must not import application code."""

    if not isinstance(payload, dict):
        raise RuntimeError("private commit-gate ACK payload must be a JSON object")
    try:
        body = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise RuntimeError("private commit-gate ACK payload is not canonicalizable") from None
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.add_column(
        "private_commit_gate_acks",
        sa.Column("wire_digest", sa.String(length=71), nullable=True),
    )
    acks = sa.table(
        "private_commit_gate_acks",
        sa.column("message_id", postgresql.UUID(as_uuid=True)),
        sa.column("payload", postgresql.JSONB()),
        sa.column("wire_digest", sa.String(length=71)),
    )
    connection = op.get_bind()
    last_message_id: UUID | None = None
    while True:
        statement = (
            sa.select(acks.c.message_id, acks.c.payload)
            .order_by(acks.c.message_id)
            .limit(_BATCH_SIZE)
        )
        if last_message_id is not None:
            statement = statement.where(acks.c.message_id > last_message_id)
        rows: list[RowMapping] = list(connection.execute(statement).mappings())
        if not rows:
            break
        for row in rows:
            connection.execute(
                acks.update()
                .where(acks.c.message_id == row["message_id"])
                .values(wire_digest=_canonical_wire_digest(row["payload"]))
            )
        last_message_id = rows[-1]["message_id"]

    op.alter_column(
        "private_commit_gate_acks",
        "wire_digest",
        existing_type=sa.String(length=71),
        nullable=False,
    )
    op.create_check_constraint(
        op.f("ck_private_commit_gate_acks_wire_digest_format"),
        "private_commit_gate_acks",
        _WIRE_DIGEST_CHECK,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Destructive downgrade is intentionally unsupported; use a forward corrective migration."
    )
