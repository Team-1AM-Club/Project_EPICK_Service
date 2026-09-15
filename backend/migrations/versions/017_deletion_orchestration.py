"""Add epoch-fenced private deletion orchestration and store acknowledgements.

Revision ID: 017_deletion_orchestration
Revises: 016_sensitivity_privacy
Create Date: 2026-09-15

This revision coordinates private-data deletion only.  It intentionally never registers
public company/source/claim history as a deletion target and it does not define W3 ACK
data, whose contract remains on hold.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "017_deletion_orchestration"
down_revision = "016_sensitivity_privacy"
branch_labels = None
depends_on = None

REQUEST_STATUS_VALUES = (
    "'REQUESTED', 'CONFIRMED', 'RUNNING', 'COMPLETED', "
    "'PARTIALLY_COMPLETED', 'FAILED_RETRYABLE', 'EXPIRED'"
)
TARGET_STATUS_VALUES = "'QUEUED', 'DISPATCHED', 'ACKNOWLEDGED', 'FAILED_RETRYABLE'"
STORE_TYPE_VALUES = "'POSTGRESQL', 'NEO4J', 'VECTOR', 'CACHE', 'CHECKPOINT'"
PUBLIC_PAYLOAD_FORBIDDEN_KEYS = (
    "ARRAY['owner_id', 'owner_user_id', 'job_id', 'command_id', "
    "'authenticated_owner_ref', 'project_id', 'auth_subject', 'email', "
    "'checkpoint', 'prompt', 'response', 'secret', 'token']"
)


def upgrade() -> None:
    op.create_table(
        "deletion_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subject_tombstone_hash", sa.String(length=128), nullable=True),
        sa.Column("owner_deletion_epoch", sa.BigInteger(), nullable=True),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("preview_token_hash", sa.String(length=128), nullable=False),
        sa.Column("preview_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default=sa.text("'REQUESTED'"), nullable=False
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
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
        sa.CheckConstraint(
            "owner_user_id IS NOT NULL OR subject_tombstone_hash IS NOT NULL",
            name="ck_deletion_requests_subject_reference_present",
        ),
        sa.CheckConstraint(
            "length(btrim(target_type)) > 0", name="ck_deletion_requests_target_type_present"
        ),
        sa.CheckConstraint("length(btrim(scope)) > 0", name="ck_deletion_requests_scope_present"),
        sa.CheckConstraint(
            f"status IN ({REQUEST_STATUS_VALUES})", name="ck_deletion_requests_status_allowed"
        ),
        sa.CheckConstraint(
            "owner_deletion_epoch IS NULL OR owner_deletion_epoch >= 1",
            name="ck_deletion_requests_owner_deletion_epoch_positive",
        ),
        sa.CheckConstraint(
            "(status IN ('RUNNING', 'COMPLETED', 'PARTIALLY_COMPLETED', 'FAILED_RETRYABLE') "
            "AND owner_deletion_epoch IS NOT NULL) OR "
            "(status IN ('REQUESTED', 'CONFIRMED', 'EXPIRED'))",
            name="ck_deletion_requests_epoch_required_after_start",
        ),
        sa.CheckConstraint(
            "(status IN ('CONFIRMED', 'RUNNING', 'COMPLETED', 'PARTIALLY_COMPLETED', "
            "'FAILED_RETRYABLE') AND confirmed_at IS NOT NULL AND token_consumed_at IS NOT NULL) "
            "OR status IN ('REQUESTED', 'EXPIRED')",
            name="ck_deletion_requests_confirmation_required_after_confirm",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_deletion_requests_owner_user_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_deletion_requests"),
        sa.UniqueConstraint("preview_token_hash", name="uq_deletion_requests_preview_token_hash"),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_deletion_requests_id_owner_user_id"),
    )
    op.create_index(
        "ix_deletion_requests_owner_status_requested",
        "deletion_requests",
        ["owner_user_id", "status", sa.text("requested_at DESC")],
    )
    op.create_index(
        "uq_deletion_requests_active_owner",
        "deletion_requests",
        ["owner_user_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('REQUESTED', 'CONFIRMED', 'RUNNING', "
            "'PARTIALLY_COMPLETED', 'FAILED_RETRYABLE')"
        ),
    )

    op.create_table(
        "deletion_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deletion_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("store_type", sa.String(length=32), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ack_epoch", sa.BigInteger(), nullable=True),
        sa.Column(
            "status", sa.String(length=32), server_default=sa.text("'QUEUED'"), nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("ack_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            f"store_type IN ({STORE_TYPE_VALUES})", name="ck_deletion_targets_store_type_allowed"
        ),
        sa.CheckConstraint(
            f"status IN ({TARGET_STATUS_VALUES})", name="ck_deletion_targets_status_allowed"
        ),
        sa.CheckConstraint(
            "length(btrim(resource_type)) > 0", name="ck_deletion_targets_resource_type_present"
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_deletion_targets_attempts_not_negative"),
        sa.CheckConstraint(
            "(status = 'ACKNOWLEDGED' AND ack_epoch IS NOT NULL AND ack_event_id IS NOT NULL "
            "AND completed_at IS NOT NULL) OR status <> 'ACKNOWLEDGED'",
            name="ck_deletion_targets_ack_fields_required",
        ),
        sa.ForeignKeyConstraint(
            ["deletion_request_id"],
            ["deletion_requests.id"],
            name="fk_deletion_targets_request_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_deletion_targets"),
        sa.UniqueConstraint(
            "deletion_request_id",
            "store_type",
            "resource_type",
            "resource_id",
            name="uq_deletion_targets_request_store_resource",
        ),
        sa.UniqueConstraint("ack_event_id", name="uq_deletion_targets_ack_event_id"),
        sa.UniqueConstraint("id", "deletion_request_id", name="uq_deletion_targets_id_request_id"),
    )
    op.create_index(
        "ix_deletion_targets_request_status", "deletion_targets", ["deletion_request_id", "status"]
    )

    op.add_column(
        "outbox_messages",
        sa.Column("deletion_request_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "outbox_messages",
        sa.Column("deletion_target_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_outbox_messages_deletion_request_id",
        "outbox_messages",
        "deletion_requests",
        ["deletion_request_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_outbox_messages_deletion_target_id",
        "outbox_messages",
        "deletion_targets",
        ["deletion_target_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_outbox_messages_deletion_target_id",
        "outbox_messages",
        ["deletion_target_id"],
        postgresql_where=sa.text("deletion_target_id IS NOT NULL"),
    )
    op.drop_constraint(
        "ck_outbox_messages_visibility_reference_scope", "outbox_messages", type_="check"
    )
    op.create_check_constraint(
        "ck_outbox_messages_visibility_reference_scope",
        "outbox_messages",
        "(visibility_scope = 'PUBLIC' AND owner_user_id IS NULL AND job_id IS NULL "
        "AND command_id IS NULL AND execution_fence IS NULL AND owner_deletion_epoch IS NULL "
        "AND deletion_request_id IS NULL AND deletion_target_id IS NULL "
        "AND jsonb_typeof(payload) = 'object' "
        f"AND NOT (payload ?| {PUBLIC_PAYLOAD_FORBIDDEN_KEYS})) "
        "OR (visibility_scope = 'PRIVATE' AND owner_user_id IS NOT NULL AND job_id IS NOT NULL "
        "AND command_id IS NOT NULL AND execution_fence IS NOT NULL "
        "AND owner_deletion_epoch IS NOT NULL AND deletion_request_id IS NULL "
        "AND deletion_target_id IS NULL AND jsonb_typeof(payload) = 'object') "
        "OR (visibility_scope = 'PRIVATE' AND owner_user_id IS NOT NULL AND job_id IS NULL "
        "AND command_id IS NULL AND execution_fence IS NULL AND owner_deletion_epoch IS NOT NULL "
        "AND deletion_request_id IS NOT NULL AND deletion_target_id IS NOT NULL "
        "AND jsonb_typeof(payload) = 'object')",
    )

    op.execute(
        """
        CREATE FUNCTION validate_deletion_target_ack()
        RETURNS trigger AS $$
        DECLARE
            request_epoch bigint;
            request_status text;
        BEGIN
            IF NEW.status <> 'ACKNOWLEDGED' THEN
                IF OLD.status = 'ACKNOWLEDGED' AND NEW.status <> 'QUEUED' THEN
                    RAISE EXCEPTION 'acknowledged deletion target may only be replayed as queued';
                END IF;
                RETURN NEW;
            END IF;
            SELECT owner_deletion_epoch, status
            INTO request_epoch, request_status
            FROM deletion_requests
            WHERE id = NEW.deletion_request_id
            FOR KEY SHARE;
            IF request_epoch IS NULL OR NEW.ack_epoch IS DISTINCT FROM request_epoch THEN
                RAISE EXCEPTION 'deletion target acknowledgement epoch is stale';
            END IF;
            IF request_status NOT IN ('RUNNING', 'PARTIALLY_COMPLETED', 'FAILED_RETRYABLE') THEN
                RAISE EXCEPTION 'deletion target acknowledgement requires an active request';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_deletion_targets_ack_epoch
        BEFORE UPDATE ON deletion_targets
        FOR EACH ROW EXECUTE FUNCTION validate_deletion_target_ack();
        """
    )
    op.execute(
        """
        CREATE FUNCTION validate_deletion_request_completion()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.status <> 'COMPLETED' THEN
                RETURN NEW;
            END IF;
            IF NEW.owner_deletion_epoch IS NULL THEN
                RAISE EXCEPTION 'completed deletion request requires an owner deletion epoch';
            END IF;
            IF (SELECT count(*) FROM deletion_targets WHERE deletion_request_id = NEW.id) <> 5
               OR (
                   SELECT count(DISTINCT store_type)
                   FROM deletion_targets
                   WHERE deletion_request_id = NEW.id
               ) <> 5 THEN
                RAISE EXCEPTION 'completed deletion request is missing mandatory store targets';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM deletion_targets
                WHERE deletion_request_id = NEW.id
                  AND (
                      status <> 'ACKNOWLEDGED'
                      OR ack_epoch IS DISTINCT FROM NEW.owner_deletion_epoch
                  )
            ) THEN
                RAISE EXCEPTION 'completed deletion request has unacknowledged store targets';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_deletion_requests_completion
        BEFORE UPDATE OF status, owner_deletion_epoch ON deletion_requests
        FOR EACH ROW EXECUTE FUNCTION validate_deletion_request_completion();
        """
    )

    _enable_owner_rls("deletion_requests", "owner_user_id")
    op.execute("ALTER TABLE deletion_targets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE deletion_targets FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY deletion_targets_owner_policy ON deletion_targets
        USING (
            EXISTS (
                SELECT 1 FROM deletion_requests request
                WHERE request.id = deletion_targets.deletion_request_id
                  AND request.owner_user_id = NULLIF(
                      current_setting('app.current_user_id', true), ''
                  )::uuid
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1 FROM deletion_requests request
                WHERE request.id = deletion_targets.deletion_request_id
                  AND request.owner_user_id = NULLIF(
                      current_setting('app.current_user_id', true), ''
                  )::uuid
            )
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS deletion_targets_owner_policy ON deletion_targets")
    op.execute("ALTER TABLE deletion_targets DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS deletion_requests_owner_policy ON deletion_requests")
    op.execute("ALTER TABLE deletion_requests DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TRIGGER IF EXISTS trg_deletion_requests_completion ON deletion_requests")
    op.execute("DROP FUNCTION IF EXISTS validate_deletion_request_completion()")
    op.execute("DROP TRIGGER IF EXISTS trg_deletion_targets_ack_epoch ON deletion_targets")
    op.execute("DROP FUNCTION IF EXISTS validate_deletion_target_ack()")

    op.drop_constraint(
        "ck_outbox_messages_visibility_reference_scope", "outbox_messages", type_="check"
    )
    op.create_check_constraint(
        "ck_outbox_messages_visibility_reference_scope",
        "outbox_messages",
        "(visibility_scope = 'PUBLIC' AND owner_user_id IS NULL AND job_id IS NULL "
        "AND command_id IS NULL AND execution_fence IS NULL AND owner_deletion_epoch IS NULL "
        "AND jsonb_typeof(payload) = 'object' "
        f"AND NOT (payload ?| {PUBLIC_PAYLOAD_FORBIDDEN_KEYS})) "
        "OR (visibility_scope = 'PRIVATE' AND owner_user_id IS NOT NULL AND job_id IS NOT NULL "
        "AND command_id IS NOT NULL AND execution_fence IS NOT NULL "
        "AND owner_deletion_epoch IS NOT NULL AND jsonb_typeof(payload) = 'object')",
    )
    op.drop_index("ix_outbox_messages_deletion_target_id", table_name="outbox_messages")
    op.drop_constraint(
        "fk_outbox_messages_deletion_target_id", "outbox_messages", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_outbox_messages_deletion_request_id", "outbox_messages", type_="foreignkey"
    )
    op.drop_column("outbox_messages", "deletion_target_id")
    op.drop_column("outbox_messages", "deletion_request_id")

    op.drop_index("ix_deletion_targets_request_status", table_name="deletion_targets")
    op.drop_table("deletion_targets")
    op.drop_index("uq_deletion_requests_active_owner", table_name="deletion_requests")
    op.drop_index("ix_deletion_requests_owner_status_requested", table_name="deletion_requests")
    op.drop_table("deletion_requests")


def _enable_owner_rls(table_name: str, owner_column: str) -> None:
    expression = f"{owner_column} = NULLIF(current_setting('app.current_user_id', true), '')::uuid"
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table_name}_owner_policy ON {table_name} "
        f"USING ({expression}) WITH CHECK ({expression})"
    )
