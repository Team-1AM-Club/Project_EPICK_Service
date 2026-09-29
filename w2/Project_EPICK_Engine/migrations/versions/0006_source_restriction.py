"""Add storage-only SourceRestriction identity and immutable revision history.

Revision ID: 0006_source_restriction
Revises: 0005_private_gate_delivery
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_source_restriction"
down_revision: str | None = "0005_private_gate_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_restriction_identities",
        sa.Column("restriction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.source_id"],
            name="fk_source_restriction_identities_source_id",
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id", "source_id"],
            ["source_versions.source_version_id", "source_versions.source_id"],
            name="fk_source_restriction_identities_version_same_source",
        ),
        sa.PrimaryKeyConstraint(
            "restriction_id",
            name=op.f("pk_source_restriction_identities"),
        ),
        sa.UniqueConstraint(
            "restriction_id",
            "source_id",
            name="uq_source_restriction_identities_id_source",
        ),
    )
    op.create_index(
        "ix_source_restriction_identities_source_id_restriction_id",
        "source_restriction_identities",
        ["source_id", "restriction_id"],
        unique=False,
    )

    op.create_table(
        "source_restrictions",
        sa.Column("restriction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("restriction_revision", sa.Integer(), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("restriction_status", sa.String(length=32), nullable=False),
        sa.Column("accuracy_status", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.Text(), nullable=False),
        sa.Column("evidence_refs", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replacement_ref", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint(
            "restriction_revision > 0",
            name=op.f("ck_source_restrictions_positive_restriction_revision"),
        ),
        sa.CheckConstraint(
            "restriction_status IN ('active', 'cleared')",
            name=op.f("ck_source_restrictions_valid_restriction_status"),
        ),
        sa.CheckConstraint(
            "accuracy_status IN "
            "('unverified', 'verified_in_scope', 'error_confirmed', 'superseded')",
            name=op.f("ck_source_restrictions_valid_accuracy_status"),
        ),
        sa.CheckConstraint(
            "length(btrim(reason_code)) > 0",
            name=op.f("ck_source_restrictions_nonempty_reason_code"),
        ),
        sa.ForeignKeyConstraint(
            ["restriction_id", "source_id"],
            [
                "source_restriction_identities.restriction_id",
                "source_restriction_identities.source_id",
            ],
            name="fk_source_restrictions_identity_same_source",
        ),
        sa.ForeignKeyConstraint(
            ["replacement_ref"],
            ["sources.source_id"],
            name="fk_source_restrictions_replacement_ref",
        ),
        sa.PrimaryKeyConstraint(
            "restriction_id",
            "restriction_revision",
            name=op.f("pk_source_restrictions"),
        ),
        sa.UniqueConstraint(
            "source_id",
            "restriction_revision",
            name="uq_source_restrictions_source_revision",
        ),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Destructive downgrade is intentionally unsupported; use a forward corrective migration."
    )
