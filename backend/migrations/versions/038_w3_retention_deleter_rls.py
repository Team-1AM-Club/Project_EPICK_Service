"""Allow the deletion worker to operate W3 retention rows through RLS.

Revision ID: 038_w3_retention_deleter_rls
Revises: 037_w3_source_private_outbox
Create Date: 2026-09-20
"""

from __future__ import annotations

from alembic import op

revision = "038_w3_retention_deleter_rls"
down_revision = "037_w3_source_private_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_deleter') THEN
                RAISE EXCEPTION
                    'epick_deleter role must be provisioned before revision 038';
            END IF;
        END
        $$;
        """
    )

    # Receipt reconciliation locks the current owner but must never mutate the
    # identity row.  PostgreSQL requires both SELECT and UPDATE RLS policies for
    # SELECT ... FOR UPDATE; WITH CHECK (false) keeps mutation denied.
    op.execute(
        """
        CREATE POLICY users_deleter_operational_read_policy
        ON users
        FOR SELECT TO epick_deleter
        USING (true)
        """
    )
    op.execute(
        """
        CREATE POLICY users_deleter_operational_lock_policy
        ON users
        FOR UPDATE TO epick_deleter
        USING (true)
        WITH CHECK (false)
        """
    )

    for table_name in ("deletion_requests", "deletion_targets"):
        op.execute(
            f"CREATE POLICY {table_name}_deleter_operational_policy "
            f"ON {table_name} FOR ALL TO epick_deleter "
            "USING (true) WITH CHECK (true)"
        )

    # The deletion principal owns only the two adopted W3 retention routes on
    # the shared outbox.  Other worker routes remain hidden by RLS.
    op.execute(
        """
        CREATE POLICY outbox_messages_w3_retention_deleter_policy
        ON outbox_messages
        FOR ALL TO epick_deleter
        USING (
            message_type IN (
                'w1.private.w3.owner-deletion.v1',
                'w1.private.w3.source-retirement.v1'
            )
        )
        WITH CHECK (
            message_type IN (
                'w1.private.w3.owner-deletion.v1',
                'w1.private.w3.source-retirement.v1'
            )
        )
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 038_w3_retention_deleter_rls is forward-only: removing the "
        "policies would stop durable W3 retention delivery and reconciliation"
    )
