"""Add durable collection runtime reservation and candidate staging metadata.

Revision ID: 0008_collection_runtime
Revises: 0007_restriction_receipt
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_collection_runtime"
down_revision: str | None = "0007_restriction_receipt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column(
            "pointer_update_mode",
            sa.String(length=32),
            server_default=sa.text("'LEGACY_SAME_DB'"),
            nullable=False,
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "next_observation_order",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "last_promoted_observation_order",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_sources_valid_pointer_update_mode"),
        "sources",
        "pointer_update_mode IN ('LEGACY_SAME_DB', 'FINALIZE_GATE')",
    )
    op.create_check_constraint(
        op.f("ck_sources_valid_observation_order_counters"),
        "sources",
        "0 <= last_promoted_observation_order "
        "AND last_promoted_observation_order <= next_observation_order",
    )

    op.add_column(
        "private_commit_stages",
        sa.Column(
            "stage_kind",
            sa.String(length=16),
            server_default=sa.text("'PRIVATE_ONLY'"),
            nullable=True,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE private_commit_stages SET stage_kind = 'PRIVATE_ONLY' WHERE stage_kind IS NULL"
        )
    )
    op.alter_column(
        "private_commit_stages",
        "stage_kind",
        existing_type=sa.String(length=16),
        existing_server_default=sa.text("'PRIVATE_ONLY'"),
        nullable=False,
    )
    op.create_check_constraint(
        op.f("ck_private_commit_stages_valid_stage_kind"),
        "private_commit_stages",
        "stage_kind IN ('PRIVATE_ONLY', 'COLLECTION')",
    )

    for table_name in ("private_staged_outbox", "private_commit_gate_acks"):
        op.add_column(
            table_name,
            sa.Column("relay_claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.add_column(
            table_name,
            sa.Column("relay_claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_check_constraint(
            op.f(f"ck_{table_name}_relay_claim_fields_together"),
            table_name,
            "(relay_claim_token IS NULL) = (relay_claim_expires_at IS NULL)",
        )

    op.create_table(
        "collection_runtime_attempts",
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dispatch_digest", sa.String(length=64), nullable=False),
        sa.Column("owner_ref", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("observation_order", sa.BigInteger(), nullable=False),
        sa.Column("effective_policy_revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "observation_order > 0",
            name=op.f("ck_collection_runtime_attempts_positive_observation_order"),
        ),
        sa.CheckConstraint(
            "effective_policy_revision > 0",
            name=op.f("ck_collection_runtime_attempts_positive_policy_revision"),
        ),
        sa.CheckConstraint(
            "dispatch_digest ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_collection_runtime_attempts_valid_dispatch_digest"),
        ),
        sa.CheckConstraint(
            "state IN ('RESERVED', 'PERSISTED', 'FINALIZED', 'INVALIDATED')",
            name=op.f("ck_collection_runtime_attempts_valid_state"),
        ),
        sa.CheckConstraint(
            "(claim_token IS NULL) = (claim_expires_at IS NULL)",
            name=op.f("ck_collection_runtime_attempts_claim_fields_together"),
        ),
        sa.CheckConstraint(
            "state = 'RESERVED' OR (claim_token IS NULL AND claim_expires_at IS NULL)",
            name=op.f("ck_collection_runtime_attempts_claim_fields_reserved_only"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "company_id"],
            ["sources.source_id", "sources.company_id"],
            name="fk_collection_runtime_attempts_source_company",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id", "source_id"],
            ["source_observations.observation_id", "source_observations.source_id"],
            name="fk_collection_runtime_attempts_observation_same_source",
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id", "source_id"],
            ["source_versions.source_version_id", "source_versions.source_id"],
            name="fk_collection_runtime_attempts_version_same_source",
        ),
        sa.PrimaryKeyConstraint(
            "command_id",
            name=op.f("pk_collection_runtime_attempts"),
        ),
        sa.UniqueConstraint(
            "attempt_id",
            name="uq_collection_runtime_attempts_attempt_id",
        ),
        sa.UniqueConstraint(
            "source_id",
            "observation_order",
            name="uq_collection_runtime_attempts_source_observation_order",
        ),
    )
    op.create_index(
        "ix_collection_runtime_attempts_owner_ref",
        "collection_runtime_attempts",
        ["owner_ref"],
        unique=False,
    )
    op.create_index(
        "ix_collection_runtime_attempts_job_id",
        "collection_runtime_attempts",
        ["job_id"],
        unique=False,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Destructive downgrade is intentionally unsupported; use a forward corrective migration."
    )
