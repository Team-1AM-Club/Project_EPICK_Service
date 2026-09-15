"""Finish PG-3 public-history immutability.

Revision ID: 011_pg3_immutability
Revises: 010_question_analysis
Create Date: 2026-09-15

The mutable resource roots (for example ``sources`` and ``job_postings``)
continue to hold current pointers and operational policy state.  This revision
only protects the evidence-backed, time-anchored history records beneath them.
"""

from __future__ import annotations

from alembic import op

revision = "011_pg3_immutability"
down_revision = "010_question_analysis"
branch_labels = None
depends_on = None


PUBLIC_HISTORY_TABLES = (
    "source_relations",
    "company_relations",
    "org_unit_versions",
    "role_versions",
    "job_posting_versions",
    "requirement_groups",
    "requirements",
    "requirement_skills",
)


def upgrade() -> None:
    for table_name in PUBLIC_HISTORY_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_append_only BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()"
        )


def downgrade() -> None:
    for table_name in PUBLIC_HISTORY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only ON {table_name}")
