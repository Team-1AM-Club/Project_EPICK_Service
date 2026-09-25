"""Retain W1 W2-command proof until an explicit cross-service drain contract exists.

Revision ID: 045_w2_command_binding_retention
Revises: 044_w2_gate_lookup_grant
"""

from __future__ import annotations

from alembic import op

revision = "045_w2_command_binding_retention"
down_revision = "044_w2_gate_lookup_grant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION public.retain_w2_command_binding()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog, public
        AS $$
        BEGIN
            IF OLD.command_type IN ('W2_SOURCE_COLLECTION', 'W2_DIRECT_SOURCE_REGISTRATION') THEN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'w2_command_binding_retained' USING ERRCODE = '23514';
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.job_id IS DISTINCT FROM OLD.job_id
                   OR NEW.owner_user_id IS DISTINCT FROM OLD.owner_user_id
                   OR NEW.command_type IS DISTINCT FROM OLD.command_type
                   OR NEW.execution_fence IS DISTINCT FROM OLD.execution_fence
                   OR NEW.owner_deletion_epoch IS DISTINCT FROM OLD.owner_deletion_epoch THEN
                    RAISE EXCEPTION 'w2_command_binding_retained' USING ERRCODE = '23514';
                END IF;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER retain_w2_command_binding
        BEFORE UPDATE OR DELETE ON public.job_commands
        FOR EACH ROW EXECUTE FUNCTION public.retain_w2_command_binding()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER retain_w2_command_binding ON public.job_commands")
    op.execute("DROP FUNCTION public.retain_w2_command_binding()")
