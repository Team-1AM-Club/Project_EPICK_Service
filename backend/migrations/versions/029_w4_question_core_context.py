"""Add an opaque W1-issued context boundary for W4 Question Core CT-12.

Revision ID: 029_w4_question_core_context
Revises: 028_w4_question_core_inbound
Create Date: 2026-09-19

The W4 context login is read-only and receives only selected columns through a
private W1 adapter.  It never gets a public router, W1 worker credentials, or
direct W1 database access.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "029_w4_question_core_context"
down_revision = "028_w4_question_core_inbound"
branch_labels = None
depends_on = None

_CONTEXT_TABLE = "w4_question_core_contexts"
_CONTEXT_READ_TABLES = (
    "users",
    "jobs",
    "application_projects",
    "application_project_versions",
    "project_questions",
    "question_versions",
    "job_source_links",
    "sources",
    "job_required_actions",
    "job_core_decision_bindings",
)


def upgrade() -> None:
    _require_runtime_roles()
    op.create_table(
        _CONTEXT_TABLE,
        sa.Column("context_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("analysis_input_version", sa.String(length=64), nullable=False),
        sa.Column("execution_fence", sa.BigInteger(), nullable=False),
        sa.Column("owner_deletion_epoch", sa.BigInteger(), nullable=False),
        sa.Column("data_kind", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "data_kind IN ('SYNTHETIC', 'REAL')",
            name="data_kind_allowed",
        ),
        sa.CheckConstraint("execution_fence >= 1", name="execution_fence_positive"),
        sa.CheckConstraint(
            "owner_deletion_epoch >= 0",
            name="owner_deletion_epoch_not_negative",
        ),
        sa.CheckConstraint("expires_at > created_at", name="expires_after_created"),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["jobs.id", "jobs.owner_user_id"],
            name="job_owner_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("context_key"),
    )
    op.create_index(
        "ix_w4_question_core_contexts_job",
        _CONTEXT_TABLE,
        ["job_id", "expires_at"],
    )
    op.execute(f"ALTER TABLE {_CONTEXT_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {_CONTEXT_TABLE}_worker_operational_policy ON {_CONTEXT_TABLE} "
        "FOR ALL TO epick_worker USING (true) WITH CHECK (true)"
    )
    op.execute(
        f"CREATE POLICY {_CONTEXT_TABLE}_w4_context_read_policy ON {_CONTEXT_TABLE} "
        "FOR SELECT TO epick_w4_context USING (true)"
    )
    for table_name in _CONTEXT_READ_TABLES:
        op.execute(
            f"CREATE POLICY {table_name}_w4_context_read_policy ON {table_name} "
            "FOR SELECT TO epick_w4_context USING (true)"
        )


def downgrade() -> None:
    for table_name in _CONTEXT_READ_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table_name}_w4_context_read_policy ON {table_name}")
    op.execute(
        f"DROP POLICY IF EXISTS {_CONTEXT_TABLE}_w4_context_read_policy ON {_CONTEXT_TABLE}"
    )
    op.execute(
        f"DROP POLICY IF EXISTS {_CONTEXT_TABLE}_worker_operational_policy ON {_CONTEXT_TABLE}"
    )
    op.drop_index("ix_w4_question_core_contexts_job", table_name=_CONTEXT_TABLE)
    op.drop_table(_CONTEXT_TABLE)


def _require_runtime_roles() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_worker')
               OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_w4_context') THEN
                RAISE EXCEPTION
                    'epick_worker and epick_w4_context roles must be provisioned '
                    'before revision 029';
            END IF;
        END
        $$;
        """
    )
