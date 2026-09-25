"""Permit the deletion role to detach private Job references from public attempts.

Revision ID: 043_w2_attempt_detachment
Revises: 042_w2_private_deletion_target
"""

from __future__ import annotations

from alembic import op

revision = "043_w2_attempt_detachment"
down_revision = "042_w2_private_deletion_target"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION reject_source_attempt_mutation_except_private_detachment()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND pg_has_role(current_user, 'epick_deleter', 'MEMBER')
               AND OLD.job_source_link_id IS NOT NULL
               AND NEW.job_source_link_id IS NULL
               AND NEW.command_id IS NULL
               AND (to_jsonb(NEW) - 'job_source_link_id' - 'command_id')
                   = (to_jsonb(OLD) - 'job_source_link_id' - 'command_id')
            THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = '55000';
        END;
        $$;
        """
    )
    op.execute(
        "DROP TRIGGER trg_source_collection_attempts_append_only ON source_collection_attempts"
    )
    op.execute(
        "CREATE TRIGGER trg_source_collection_attempts_append_only "
        "BEFORE UPDATE OR DELETE ON source_collection_attempts "
        "FOR EACH ROW EXECUTE FUNCTION "
        "reject_source_attempt_mutation_except_private_detachment()"
    )


def downgrade() -> None:
    raise RuntimeError("forward-only: W2 private attempt references may already be detached")
