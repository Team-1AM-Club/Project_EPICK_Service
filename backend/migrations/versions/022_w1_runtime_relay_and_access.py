"""Add W1 relay recovery state and scoped runtime RLS policies.

Revision ID: 022_w1_runtime_relay_access
Revises: 021_idempotency_snapshot
Create Date: 2026-09-16

The runtime group roles are installed by an RDS administrator before Alembic,
as required by infra/postgres/RDS_MIGRATION_RUNBOOK.md.  This migration never
creates LOGIN roles and never grants BYPASSRLS.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "022_w1_runtime_relay_access"
down_revision = "021_idempotency_snapshot"
branch_labels = None
depends_on = None

_WORKER_ALL_TABLES = (
    "jobs",
    "job_commands",
    "outbox_messages",
    "owner_execution_slots",
    "job_execution_leases",
    "job_checkpoints",
    "job_required_actions",
    "job_source_links",
)
_WORKER_READ_TABLES = ("users", "job_input_refs")
_LOOKUP_READ_TABLES = ("users", "jobs", "job_commands")


def upgrade() -> None:
    _require_runtime_roles()

    op.add_column(
        "outbox_messages",
        sa.Column("relay_claim_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "outbox_messages",
        sa.Column("relay_claimed_by", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "outbox_messages",
        sa.Column("relay_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "outbox_messages",
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "outbox_messages",
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_outbox_messages_relay_claim_complete",
        "outbox_messages",
        "(relay_claim_token IS NULL AND relay_claimed_by IS NULL "
        "AND relay_lease_expires_at IS NULL) OR "
        "(relay_claim_token IS NOT NULL AND relay_claimed_by IS NOT NULL "
        "AND relay_lease_expires_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_outbox_messages_last_error_complete",
        "outbox_messages",
        "(last_error_code IS NULL AND last_error_at IS NULL) OR "
        "(last_error_code IS NOT NULL AND last_error_at IS NOT NULL)",
    )
    op.create_index(
        "ix_outbox_messages_relay_due",
        "outbox_messages",
        ["visibility_scope", "status", "available_at", "relay_lease_expires_at", "created_at"],
    )

    for table_name in _WORKER_ALL_TABLES:
        _create_operational_policy(
            table_name=table_name,
            policy_name=f"{table_name}_worker_operational_policy",
            role_name="epick_worker",
            command="ALL",
        )
    for table_name in _WORKER_READ_TABLES:
        _create_operational_policy(
            table_name=table_name,
            policy_name=f"{table_name}_worker_operational_read_policy",
            role_name="epick_worker",
            command="SELECT",
        )
    for table_name in _LOOKUP_READ_TABLES:
        _create_operational_policy(
            table_name=table_name,
            policy_name=f"{table_name}_lookup_read_policy",
            role_name="epick_lookup",
            command="SELECT",
        )


def downgrade() -> None:
    for table_name in _LOOKUP_READ_TABLES:
        op.execute(
            f"DROP POLICY IF EXISTS {table_name}_lookup_read_policy ON {table_name}"
        )
    for table_name in _WORKER_READ_TABLES:
        op.execute(
            f"DROP POLICY IF EXISTS {table_name}_worker_operational_read_policy ON {table_name}"
        )
    for table_name in _WORKER_ALL_TABLES:
        op.execute(
            f"DROP POLICY IF EXISTS {table_name}_worker_operational_policy ON {table_name}"
        )

    op.drop_index("ix_outbox_messages_relay_due", table_name="outbox_messages")
    op.drop_constraint(
        "ck_outbox_messages_last_error_complete", "outbox_messages", type_="check"
    )
    op.drop_constraint(
        "ck_outbox_messages_relay_claim_complete", "outbox_messages", type_="check"
    )
    op.drop_column("outbox_messages", "last_error_at")
    op.drop_column("outbox_messages", "last_error_code")
    op.drop_column("outbox_messages", "relay_lease_expires_at")
    op.drop_column("outbox_messages", "relay_claimed_by")
    op.drop_column("outbox_messages", "relay_claim_token")


def _require_runtime_roles() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_worker')
               OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_lookup') THEN
                RAISE EXCEPTION
                    'epick_worker and epick_lookup roles must be provisioned before revision 022';
            END IF;
        END
        $$;
        """
    )


def _create_operational_policy(
    *, table_name: str, policy_name: str, role_name: str, command: str
) -> None:
    if command == "ALL":
        op.execute(
            f"CREATE POLICY {policy_name} ON {table_name} FOR ALL TO {role_name} "
            "USING (true) WITH CHECK (true)"
        )
        return
    op.execute(
        f"CREATE POLICY {policy_name} ON {table_name} FOR SELECT TO {role_name} USING (true)"
    )
