"""Create Identity and idempotency tables with owner RLS.

Revision ID: 001_create_identity_tables
Revises: 000_database_test_baseline
Create Date: 2026-09-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "001_create_identity_tables"
down_revision = "000_database_test_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column(
            "account_status",
            sa.String(length=32),
            server_default=sa.text("'ACTIVE'"),
            nullable=False,
        ),
        sa.Column("locale", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "account_status IN ('ACTIVE', 'SUSPENDED', 'DELETION_PENDING', 'DELETED')",
            name="ck_users_account_status_allowed",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_table(
        "auth_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_subject", sa.Text(), nullable=False),
        sa.Column("provider_email", sa.Text(), nullable=True),
        sa.Column(
            "provider_email_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_auth_identities_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auth_identities"),
        sa.UniqueConstraint(
            "provider", "provider_subject", name="uq_auth_identities_provider_subject"
        ),
        sa.UniqueConstraint("id", "user_id", name="uq_auth_identities_id_user_id"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("auth_identity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=128), nullable=False),
        sa.Column("token_family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=32), nullable=True),
        sa.Column("replaced_by_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_ip_hash", sa.String(length=128), nullable=True),
        sa.Column("user_agent_summary", sa.Text(), nullable=True),
        sa.CheckConstraint("expires_at > issued_at", name="ck_auth_sessions_expires_after_issued"),
        sa.ForeignKeyConstraint(
            ["auth_identity_id", "user_id"],
            ["auth_identities.id", "auth_identities.user_id"],
            name="fk_auth_sessions_auth_identity_user_id_auth_identities",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_session_id"],
            ["auth_sessions.id"],
            name="fk_auth_sessions_replaced_by_session_id_auth_sessions",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_auth_sessions_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auth_sessions"),
        sa.UniqueConstraint("refresh_token_hash", name="uq_auth_sessions_refresh_token_hash"),
        sa.UniqueConstraint("id", "user_id", name="uq_auth_sessions_id_user_id"),
    )
    op.create_table(
        "idempotency_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("path_scope", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=128), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_ref", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_idempotency_records_owner_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_records"),
        sa.UniqueConstraint(
            "owner_user_id",
            "method",
            "path_scope",
            "idempotency_key",
            name="uq_idempotency_records_owner_method_path_key",
        ),
    )
    _enable_owner_rls("users", "id")
    _enable_owner_rls("auth_identities", "user_id")
    _enable_owner_rls("auth_sessions", "user_id")
    _enable_owner_rls("idempotency_records", "owner_user_id")


def downgrade() -> None:
    for table_name in ("idempotency_records", "auth_sessions", "auth_identities", "users"):
        op.drop_table(table_name)


def _enable_owner_rls(table_name: str, owner_column: str) -> None:
    policy_name = f"{table_name}_owner_policy"
    expression = f"{owner_column} = NULLIF(current_setting('app.current_user_id', true), '')::uuid"
    policy_statement = (
        f"CREATE POLICY {policy_name} ON {table_name} "
        f"USING ({expression}) WITH CHECK ({expression})"
    )
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(policy_statement)
