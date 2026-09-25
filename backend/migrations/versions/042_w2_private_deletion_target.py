"""Require a distinct W2 private-deletion target for new owner deletions.

Revision ID: 042_w2_private_deletion_target
Revises: 041_merge_w2_w3_runtime_heads
"""

from __future__ import annotations

from alembic import op

revision = "042_w2_private_deletion_target"
down_revision = "041_merge_w2_w3_runtime_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE outbox_messages ALTER COLUMN schema_version TYPE VARCHAR(64)")
    # In-flight six-target requests need an operator-led replay/migration; never
    # silently mark them complete under the new seven-target contract.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM deletion_requests
                WHERE status IN ('CONFIRMED', 'RUNNING', 'PARTIALLY_COMPLETED', 'FAILED_RETRYABLE')
            ) THEN
                RAISE EXCEPTION 'active deletion requests require reconciliation before W2 v2';
            END IF;
        END
        $$;
        """
    )
    # `op.drop_constraint` applies the project's naming convention a second
    # time; use the physical PostgreSQL name observed after revision 035.
    op.execute(
        'ALTER TABLE deletion_targets DROP CONSTRAINT "ck_deletion_targets_store_type_allowed"'
    )
    op.create_check_constraint(
        "store_type_allowed",
        "deletion_targets",
        "store_type IN ('POSTGRESQL', 'NEO4J', 'VECTOR', 'CACHE', 'CHECKPOINT', "
        "'W3_CORE_RUNTIME', 'W2_SOURCE_RUNTIME')",
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION validate_deletion_request_completion()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.status <> 'COMPLETED' THEN
                RETURN NEW;
            END IF;
            IF NEW.owner_deletion_epoch IS NULL THEN
                RAISE EXCEPTION 'completed deletion request requires an owner deletion epoch';
            END IF;
            IF (SELECT count(*) FROM deletion_targets WHERE deletion_request_id = NEW.id) <> 7
               OR (
                   SELECT count(DISTINCT store_type)
                   FROM deletion_targets
                   WHERE deletion_request_id = NEW.id
               ) <> 7 THEN
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
        """
    )


def downgrade() -> None:
    raise RuntimeError("forward-only: W2 private-deletion targets may already exist")
