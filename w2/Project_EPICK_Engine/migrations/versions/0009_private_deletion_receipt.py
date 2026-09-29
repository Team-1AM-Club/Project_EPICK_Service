"""Add durable owner epoch and private deletion receipts.

Revision ID: 0009_private_deletion_receipt
Revises: 0008_collection_runtime
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_private_deletion_receipt"
down_revision: str | None = "0008_collection_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "private_deletion_owner_states",
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("latest_epoch", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint(
            "owner_user_id",
            name=op.f("pk_private_deletion_owner_states"),
        ),
    )
    op.create_table(
        "private_deletion_receipts",
        sa.Column("deletion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deletion_epoch", sa.BigInteger(), nullable=False),
        sa.Column("command_digest", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "deletion_epoch > 0",
            name=op.f("ck_private_deletion_receipts_positive_deletion_epoch"),
        ),
        sa.PrimaryKeyConstraint(
            "deletion_id",
            name=op.f("pk_private_deletion_receipts"),
        ),
        sa.UniqueConstraint(
            "owner_user_id",
            "deletion_epoch",
            name="uq_private_deletion_receipts_owner_epoch",
        ),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Destructive downgrade is intentionally unsupported; use a forward corrective migration."
    )
