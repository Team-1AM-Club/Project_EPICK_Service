"""Allow the W2-only lookup login to inspect bound gate state without payload access.

Revision ID: 044_w2_gate_lookup_grant
Revises: 043_w2_attempt_detachment
"""

from __future__ import annotations

from alembic import op

revision = "044_w2_gate_lookup_grant"
down_revision = "043_w2_attempt_detachment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "GRANT SELECT (id, command_id, job_id, owner_user_id, execution_fence, "
        "owner_deletion_epoch, purge_owner_deletion_epoch, result_digest, "
        "operation_revision, state) ON TABLE w2_commit_operations TO epick_lookup"
    )
    op.execute(
        """
        CREATE FUNCTION public.w2_gate_outbox_was_issued(
            p_operation_id uuid,
            p_revision bigint,
            p_action text,
            p_command_id uuid,
            p_job_id uuid,
            p_owner_user_id uuid,
            p_execution_fence bigint,
            p_owner_deletion_epoch bigint,
            p_result_digest text,
            p_purge_owner_deletion_epoch bigint
        ) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT EXISTS (
                SELECT 1 FROM public.outbox_messages AS issued
                WHERE issued.message_type = 'w1.private.w2.commit-gate.v1'
                  AND issued.aggregate_type = 'W2_COMMIT_OPERATION'
                  AND issued.aggregate_id = p_operation_id
                  AND issued.aggregate_revision = p_revision
                  AND issued.command_id = p_command_id
                  AND issued.job_id = p_job_id
                  AND issued.owner_user_id = p_owner_user_id
                  AND issued.execution_fence = p_execution_fence
                  AND issued.owner_deletion_epoch = p_owner_deletion_epoch
                  AND issued.payload ->> 'operation_id' = p_operation_id::text
                  AND issued.payload ->> 'operation_revision' = p_revision::text
                  AND issued.payload ->> 'action' = p_action
                  AND issued.payload ->> 'result_digest' = p_result_digest
                  AND (issued.payload ->> 'purge_owner_deletion_epoch')
                      IS NOT DISTINCT FROM p_purge_owner_deletion_epoch::text
            )
        $$
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.w2_gate_outbox_was_issued("
        "uuid, bigint, text, uuid, uuid, uuid, bigint, bigint, text, bigint) FROM PUBLIC"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION public.w2_gate_outbox_was_issued("
        "uuid, bigint, text, uuid, uuid, uuid, bigint, bigint, text, bigint) TO epick_lookup"
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION public.w2_gate_outbox_was_issued("
        "uuid, bigint, text, uuid, uuid, uuid, bigint, bigint, text, bigint)"
    )
    op.execute(
        "REVOKE SELECT (id, command_id, job_id, owner_user_id, execution_fence, "
        "owner_deletion_epoch, purge_owner_deletion_epoch, result_digest, "
        "operation_revision, state) ON TABLE w2_commit_operations FROM epick_lookup"
    )
