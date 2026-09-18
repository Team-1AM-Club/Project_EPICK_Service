"""Persist W2 staged results within W1's commit-gate boundary.

Revision ID: 027_w2_staged_result_adoption
Revises: 026_w3_core_decision_inbound
Create Date: 2026-09-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "027_w2_staged_result_adoption"
down_revision = "026_w3_core_decision_inbound"
branch_labels = None
depends_on = None

_PAYLOAD_STATES = "'ACTIVE', 'CONSUMED', 'CLEARED'"
_OPERATIONAL_ROLES = ("epick_worker", "epick_deleter")


def upgrade() -> None:
    _require_operational_roles()
    op.create_table(
        "w2_staged_results",
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("origin_message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_version", sa.String(length=128), nullable=False),
        sa.Column("producer_name", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_digest", sa.String(length=71), nullable=False),
        sa.Column("result_digest", sa.String(length=71), nullable=False),
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("payload_state", sa.String(length=16), server_default="ACTIVE", nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["operation_id"], ["w2_commit_operations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["command_id"], ["job_commands.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("operation_id"),
        sa.UniqueConstraint("command_id", name="uq_w2_staged_results_command_id_once"),
        sa.UniqueConstraint(
            "origin_message_id", name="uq_w2_staged_results_origin_message_id_once"
        ),
        sa.CheckConstraint(
            "payload_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_w2_staged_results_payload_digest_sha256",
        ),
        sa.CheckConstraint(
            "result_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_w2_staged_results_result_digest_sha256",
        ),
        sa.CheckConstraint(
            f"payload_state IN ({_PAYLOAD_STATES})",
            name="ck_w2_staged_results_payload_state_allowed",
        ),
        sa.CheckConstraint(
            "(payload_state = 'ACTIVE' AND result_payload IS NOT NULL "
            "AND consumed_at IS NULL AND cleared_at IS NULL) "
            "OR (payload_state = 'CONSUMED' AND result_payload IS NULL "
            "AND consumed_at IS NOT NULL AND cleared_at IS NOT NULL) "
            "OR (payload_state = 'CLEARED' AND result_payload IS NULL "
            "AND consumed_at IS NULL AND cleared_at IS NOT NULL)",
            name="ck_w2_staged_results_payload_lifecycle_consistent",
        ),
    )
    op.create_index(
        "ix_w2_staged_results_owner_state",
        "w2_staged_results",
        ["owner_user_id", "payload_state", "updated_at"],
    )

    owner_expression = (
        "owner_user_id = "
        "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
    )
    op.execute("ALTER TABLE w2_staged_results ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE w2_staged_results FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY w2_staged_results_owner_policy ON w2_staged_results "
        f"USING ({owner_expression}) WITH CHECK ({owner_expression})"
    )
    for role_name in _OPERATIONAL_ROLES:
        op.execute(
            "CREATE POLICY w2_staged_results_"
            f"{role_name.removeprefix('epick_')}_operational_policy "
            "ON w2_staged_results FOR ALL "
            f"TO {role_name} USING (true) WITH CHECK (true)"
        )


def downgrade() -> None:
    for role_name in _OPERATIONAL_ROLES:
        policy_name = "w2_staged_results_" f"{role_name.removeprefix('epick_')}_operational_policy"
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON w2_staged_results")
    op.execute("DROP POLICY IF EXISTS w2_staged_results_owner_policy ON w2_staged_results")
    op.execute("ALTER TABLE w2_staged_results DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_w2_staged_results_owner_state", table_name="w2_staged_results")
    op.drop_table("w2_staged_results")


def _require_operational_roles() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_worker')
               OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_deleter') THEN
                RAISE EXCEPTION
                    'worker and deleter roles must be provisioned before revision 027';
            END IF;
        END
        $$;
        """
    )
