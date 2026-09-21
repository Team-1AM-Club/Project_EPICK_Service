"""Require the additive W3 target before deletion completion.

Revision ID: 036_w3_deletion_completion
Revises: 035_w3_deletion_target
Create Date: 2026-09-20
"""

from __future__ import annotations

from alembic import op

revision = "036_w3_deletion_completion"
down_revision = "035_w3_deletion_target"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
            IF (SELECT count(*) FROM deletion_targets WHERE deletion_request_id = NEW.id) <> 6
               OR (
                   SELECT count(DISTINCT store_type)
                   FROM deletion_targets
                   WHERE deletion_request_id = NEW.id
               ) <> 6 THEN
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
    raise RuntimeError(
        "forward-only: completed deletions may depend on the mandatory W3 target"
    )
