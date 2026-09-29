"""Add private restriction mutation idempotency receipts.

Revision ID: 0007_restriction_receipt
Revises: 0006_source_restriction
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_restriction_receipt"
down_revision: str | None = "0006_source_restriction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "restriction_mutation_receipts",
        sa.Column("authority_ref", sa.Text(), nullable=False),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("restriction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("restriction_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "btrim(authority_ref) <> ''",
            name=op.f("ck_restriction_mutation_receipts_nonempty_authority_ref"),
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_restriction_mutation_receipts_valid_request_hash"),
        ),
        sa.CheckConstraint(
            "restriction_revision > 0",
            name=op.f("ck_restriction_mutation_receipts_positive_restriction_revision"),
        ),
        sa.ForeignKeyConstraint(
            ["restriction_id", "restriction_revision"],
            ["source_restrictions.restriction_id", "source_restrictions.restriction_revision"],
            name="fk_restriction_mutation_receipts_restriction_history",
        ),
        sa.PrimaryKeyConstraint(
            "authority_ref",
            "request_id",
            name=op.f("pk_restriction_mutation_receipts"),
        ),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Destructive downgrade is intentionally unsupported; use a forward corrective migration."
    )
