"""Add disconnected owner-private staging and durable gate ACK proposals.

Revision ID: 0004_private_commit_gate
Revises: 0003_source_retention_origin
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_private_commit_gate"
down_revision: str | None = "0003_source_retention_origin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "private_commit_stages",
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_fence", sa.Text(), nullable=False),
        sa.Column("owner_deletion_epoch", sa.Text(), nullable=False),
        sa.Column("result_digest", sa.String(length=71), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("operation_revision", sa.Text(), nullable=False),
        sa.Column("max_purge_epoch", sa.Text(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("result_payload", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('STAGED', 'PREPARED', 'FINALIZED', 'ABORTED', 'PURGED')",
            name="valid_state",
        ),
        sa.CheckConstraint(
            "(state IN ('ABORTED', 'PURGED') AND result_payload IS NULL) OR "
            "(state IN ('STAGED', 'PREPARED', 'FINALIZED') AND result_payload IS NOT NULL)",
            name="payload_matches_state",
        ),
        sa.PrimaryKeyConstraint("command_id", name="pk_private_commit_stages"),
        sa.UniqueConstraint("operation_id", name="uq_private_commit_stages_operation"),
    )
    op.create_table(
        "private_staged_outbox",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("wire_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.ForeignKeyConstraint(["command_id"], ["private_commit_stages.command_id"]),
        sa.PrimaryKeyConstraint("message_id", name="pk_private_staged_outbox"),
        sa.UniqueConstraint("command_id", name="uq_private_staged_outbox_command"),
    )
    op.create_table(
        "private_commit_gate_acks",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["command_id"], ["private_commit_stages.command_id"]),
        sa.PrimaryKeyConstraint("message_id", name="pk_private_commit_gate_acks"),
    )
    op.create_table(
        "private_commit_gate_receipts",
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_revision", sa.Text(), nullable=False),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("ack_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["command_id"], ["private_commit_stages.command_id"]),
        sa.ForeignKeyConstraint(["ack_message_id"], ["private_commit_gate_acks.message_id"]),
        sa.PrimaryKeyConstraint(
            "operation_id", "operation_revision", name="pk_private_commit_gate_receipts"
        ),
    )
    op.create_table(
        "private_commit_gate_inbox",
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("wire_hash", sa.String(length=64), nullable=False),
        sa.Column("ack_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["command_id"], ["private_commit_stages.command_id"]),
        sa.ForeignKeyConstraint(["ack_message_id"], ["private_commit_gate_acks.message_id"]),
        sa.PrimaryKeyConstraint("message_id", name="pk_private_commit_gate_inbox"),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Destructive downgrade is intentionally unsupported; use a forward corrective migration."
    )
