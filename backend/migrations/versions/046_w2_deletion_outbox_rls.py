"""Expose only W2 private deletion dispatch rows to the deletion principal.

Revision ID: 046_w2_deletion_outbox_rls
Revises: 045_w2_command_binding_retention
"""

from __future__ import annotations

from alembic import op

revision = "046_w2_deletion_outbox_rls"
down_revision = "045_w2_command_binding_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The dedicated deletion relay and its manual reconciliation use
    # epick_deleter. Table grants alone do not bypass outbox RLS.
    predicate = """
        message_type = 'w1.private.w2.deletion-command.v2'
        AND schema_version = 'w1.private.w2-deletion-dispatch.v2'
        AND visibility_scope = 'PRIVATE'
        AND aggregate_type = 'DELETION_TARGET'
        AND deletion_target_id IS NOT NULL
        AND deletion_request_id IS NOT NULL
        AND owner_user_id IS NOT NULL
    """
    op.execute(
        f"""
        CREATE POLICY outbox_messages_w2_deletion_deleter_policy
        ON public.outbox_messages
        FOR ALL TO epick_deleter
        USING ({predicate})
        WITH CHECK ({predicate})
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY outbox_messages_w2_deletion_deleter_policy ON public.outbox_messages")
