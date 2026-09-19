"""Expose W4 currentness rows to the W1 worker without mutation authority.

Revision ID: 030_w4_worker_currentness_rls
Revises: 029_w4_question_core_context
Create Date: 2026-09-20
"""

from __future__ import annotations

from alembic import op

revision = "030_w4_worker_currentness_rls"
down_revision = "029_w4_question_core_context"
branch_labels = None
depends_on = None

_CURRENTNESS_TABLES = (
    "application_projects",
    "application_project_versions",
    "project_questions",
    "question_versions",
)


def upgrade() -> None:
    _require_worker_role()
    for table_name in _CURRENTNESS_TABLES:
        op.execute(
            f"CREATE POLICY {table_name}_worker_question_core_read_policy "
            f"ON {table_name} FOR SELECT TO epick_worker USING (true)"
        )
        # PostgreSQL applies UPDATE RLS policy checks to SELECT ... FOR UPDATE.
        # USING permits the row lock while WITH CHECK keeps all row mutation denied.
        op.execute(
            f"CREATE POLICY {table_name}_worker_question_core_lock_policy "
            f"ON {table_name} FOR UPDATE TO epick_worker "
            "USING (true) WITH CHECK (false)"
        )


def downgrade() -> None:
    for table_name in reversed(_CURRENTNESS_TABLES):
        op.execute(
            f"DROP POLICY IF EXISTS {table_name}_worker_question_core_lock_policy "
            f"ON {table_name}"
        )
        op.execute(
            f"DROP POLICY IF EXISTS {table_name}_worker_question_core_read_policy "
            f"ON {table_name}"
        )


def _require_worker_role() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_worker') THEN
                RAISE EXCEPTION 'epick_worker role must be provisioned before revision 030';
            END IF;
        END
        $$;
        """
    )
