"""Add read-only currentness visibility for the W3 Authority role.

Revision ID: 034_w3_authority_currentness_rls
Revises: 033_w4_candidate_result_version
Create Date: 2026-09-20
"""

from __future__ import annotations

from alembic import op

revision = "034_w3_authority_currentness_rls"
down_revision = "033_w4_candidate_result_version"
branch_labels = None
depends_on = None

_READ_TABLES = ("users", "jobs", "job_source_links", "sources")


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = 'epick_w3_authority'
            ) THEN
                RAISE EXCEPTION
                    'epick_w3_authority role must be provisioned before revision 034';
            END IF;
        END
        $$;
        """
    )
    for table_name in _READ_TABLES:
        op.execute(
            f"CREATE POLICY {table_name}_w3_authority_read_policy ON {table_name} "
            "FOR SELECT TO epick_w3_authority USING (true)"
        )


def downgrade() -> None:
    for table_name in _READ_TABLES:
        op.execute(
            f"DROP POLICY IF EXISTS {table_name}_w3_authority_read_policy ON {table_name}"
        )
