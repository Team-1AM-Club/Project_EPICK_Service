"""Add W3 Core Decision inbox metadata and immutable Job binding.

Revision ID: 026_w3_core_decision_inbound
Revises: 025_w1_worker_owner_lock
Create Date: 2026-09-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "026_w3_core_decision_inbound"
down_revision = "025_w1_worker_owner_lock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _require_worker_role()

    op.add_column(
        "inbox_receipts",
        sa.Column("payload_digest", sa.String(length=71), nullable=True),
    )
    op.add_column(
        "inbox_receipts",
        sa.Column("producer_name", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "inbox_receipts",
        sa.Column("schema_version", sa.String(length=128), nullable=True),
    )
    op.create_check_constraint(
        "ck_inbox_receipts_payload_digest_format",
        "inbox_receipts",
        "payload_digest IS NULL OR payload_digest ~ '^sha256:[0-9a-f]{64}$'",
    )

    op.create_table(
        "job_core_decision_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "analysis_source_decision_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("origin_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload_digest", sa.String(length=71), nullable=False),
        sa.Column("analysis_input_version", sa.String(length=64), nullable=False),
        sa.Column("decision_version", sa.Integer(), nullable=False),
        sa.Column("decision_code", sa.String(length=64), nullable=False),
        sa.Column("owner_deletion_epoch", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "payload_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_job_core_decision_bindings_payload_digest_format",
        ),
        sa.CheckConstraint(
            "decision_version >= 1",
            name="ck_job_core_decision_bindings_decision_version_positive",
        ),
        sa.CheckConstraint(
            "owner_deletion_epoch >= 0",
            name="ck_job_core_decision_bindings_owner_deletion_epoch_not_negative",
        ),
        sa.CheckConstraint(
            "decision_code IN ('CORE_REQUIRED', 'NON_CORE_OPTIONAL')",
            name="ck_job_core_decision_bindings_decision_code_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["jobs.id", "jobs.owner_user_id"],
            name="fk_job_core_decision_bindings_job_owner_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_job_core_decision_bindings_source_id_sources",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_source_decision_id"],
            ["analysis_source_decisions.id"],
            name="fk_jcdb_analysis_source_decision",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_job_core_decision_bindings"),
        sa.UniqueConstraint(
            "origin_message_id",
            name="uq_job_core_decision_bindings_origin_message_id",
        ),
        sa.UniqueConstraint(
            "job_id",
            "source_id",
            "decision_version",
            name="uq_job_core_decision_bindings_job_source_decision_version",
        ),
        sa.UniqueConstraint(
            "job_id",
            "analysis_source_decision_id",
            name="uq_job_core_decision_bindings_job_decision",
        ),
    )
    op.create_index(
        "ix_job_core_decision_bindings_current",
        "job_core_decision_bindings",
        ["job_id", "source_id", "analysis_input_version", "decision_version"],
    )

    owner_expression = (
        "owner_user_id = "
        "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
    )
    op.execute("ALTER TABLE job_core_decision_bindings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE job_core_decision_bindings FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY job_core_decision_bindings_owner_policy "
        "ON job_core_decision_bindings "
        f"USING ({owner_expression}) WITH CHECK ({owner_expression})"
    )
    op.execute(
        "CREATE POLICY job_core_decision_bindings_worker_read_policy "
        "ON job_core_decision_bindings FOR SELECT TO epick_worker USING (true)"
    )
    op.execute(
        "CREATE POLICY job_core_decision_bindings_worker_insert_policy "
        "ON job_core_decision_bindings FOR INSERT TO epick_worker WITH CHECK (true)"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_job_core_decision_bindings_current",
        table_name="job_core_decision_bindings",
    )
    op.drop_table("job_core_decision_bindings")
    op.drop_constraint(
        "ck_inbox_receipts_payload_digest_format",
        "inbox_receipts",
        type_="check",
    )
    op.drop_column("inbox_receipts", "schema_version")
    op.drop_column("inbox_receipts", "producer_name")
    op.drop_column("inbox_receipts", "payload_digest")


def _require_worker_role() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_worker') THEN
                RAISE EXCEPTION 'epick_worker role must be provisioned before revision 026';
            END IF;
        END
        $$;
        """
    )
