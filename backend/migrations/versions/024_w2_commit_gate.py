"""Add W1's durable private W2 commit-operation gate.

Revision ID: 024_w2_commit_gate
Revises: 023_direct_source_registration
Create Date: 2026-09-17

The table records only W1's visibility decision for a W2 child command.  It
does not model or expose W2 staging data.  The immutable original command epoch
is distinct from a later owner-deletion PURGE epoch.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "024_w2_commit_gate"
down_revision = "023_direct_source_registration"
branch_labels = None
depends_on = None

_STATE_VALUES = (
    "'PREPARE_PENDING', 'PREPARED', 'W1_COMMITTED', 'FINALIZE_PENDING', 'FINALIZED', "
    "'ABORT_PENDING', 'ABORTED', 'PURGE_PENDING', 'PURGED', 'FAILED_FINAL'"
)
_OPERATIONAL_ROLES = ("epick_runtime", "epick_worker", "epick_deleter")


def upgrade() -> None:
    _require_runtime_roles()
    op.create_table(
        "w2_commit_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_fence", sa.BigInteger(), nullable=False),
        sa.Column("owner_deletion_epoch", sa.BigInteger(), nullable=False),
        sa.Column("purge_owner_deletion_epoch", sa.BigInteger(), nullable=True),
        sa.Column("result_digest", sa.String(length=71), nullable=False),
        sa.Column("operation_revision", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("state", sa.String(length=32), server_default="PREPARE_PENDING", nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("w1_committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("aborted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["jobs.id", "jobs.owner_user_id"],
            name="fk_w2_commit_operations_job_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["command_id", "job_id", "owner_user_id"],
            ["job_commands.id", "job_commands.job_id", "job_commands.owner_user_id"],
            name="fk_w2_commit_operations_command_job_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("command_id", name="uq_w2_commit_operations_command_id_once"),
        sa.CheckConstraint(
            "execution_fence >= 1",
            name="ck_w2_commit_operations_execution_fence_positive",
        ),
        sa.CheckConstraint(
            "owner_deletion_epoch >= 0",
            name="ck_w2_commit_operations_owner_deletion_epoch_not_negative",
        ),
        sa.CheckConstraint(
            "operation_revision >= 1",
            name="ck_w2_commit_operations_operation_revision_positive",
        ),
        sa.CheckConstraint(
            f"state IN ({_STATE_VALUES})", name="ck_w2_commit_operations_state_allowed"
        ),
        sa.CheckConstraint(
            "result_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_w2_commit_operations_result_digest_sha256",
        ),
        sa.CheckConstraint(
            "purge_owner_deletion_epoch IS NULL "
            "OR purge_owner_deletion_epoch > owner_deletion_epoch",
            name="ck_w2_commit_operations_purge_epoch_advances_original",
        ),
    )
    op.create_index(
        "ix_w2_commit_operations_recovery_due",
        "w2_commit_operations",
        ["state", "updated_at"],
    )
    op.create_index(
        "ix_w2_commit_operations_owner_state",
        "w2_commit_operations",
        ["owner_user_id", "state", "updated_at"],
    )

    _enable_owner_rls("w2_commit_operations", "owner_user_id")
    for role_name in _OPERATIONAL_ROLES:
        op.execute(
            "CREATE POLICY w2_commit_operations_"
            f"{role_name.removeprefix('epick_')}_operational_policy "
            "ON w2_commit_operations FOR ALL "
            f"TO {role_name} USING (true) WITH CHECK (true)"
        )


def downgrade() -> None:
    for role_name in _OPERATIONAL_ROLES:
        policy_name = (
            "w2_commit_operations_"
            f"{role_name.removeprefix('epick_')}_operational_policy"
        )
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON w2_commit_operations")
    op.execute("DROP POLICY IF EXISTS w2_commit_operations_owner_policy ON w2_commit_operations")
    op.execute("ALTER TABLE w2_commit_operations DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_w2_commit_operations_owner_state", table_name="w2_commit_operations")
    op.drop_index("ix_w2_commit_operations_recovery_due", table_name="w2_commit_operations")
    op.drop_table("w2_commit_operations")


def _require_runtime_roles() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_runtime')
               OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_worker')
               OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_deleter') THEN
                RAISE EXCEPTION
                    'runtime, worker, and deleter roles must be provisioned before revision 024';
            END IF;
        END
        $$;
        """
    )


def _enable_owner_rls(table_name: str, owner_column: str) -> None:
    expression = f"{owner_column} = NULLIF(current_setting('app.current_user_id', true), '')::uuid"
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table_name}_owner_policy ON {table_name} "
        f"USING ({expression}) WITH CHECK ({expression})"
    )
