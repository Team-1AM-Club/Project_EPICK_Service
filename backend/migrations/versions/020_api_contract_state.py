"""Add API contract snapshots without coupling the W3/W4 integration boundary.

Revision ID: 020_api_contract_state
Revises: 019_question_analysis_provenance
Create Date: 2026-09-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "020_api_contract_state"
down_revision = "019_question_analysis_provenance"
branch_labels = None
depends_on = None

RESULT_ORIGIN_VALUES = "'SYNTHETIC', 'ENGINE'"


def upgrade() -> None:
    op.add_column(
        "recommendation_runs",
        sa.Column("result_origin", sa.String(length=16), nullable=True),
    )
    # Runs created before the API distinguished synthetic data had no origin
    # marker. They were persisted by Engine-facing flows, so ENGINE is the only
    # non-misleading backward-compatible value.
    op.execute(
        "UPDATE recommendation_runs SET result_origin = 'ENGINE' WHERE result_origin IS NULL"
    )
    op.alter_column(
        "recommendation_runs",
        "result_origin",
        nullable=False,
        server_default=sa.text("'ENGINE'"),
    )
    op.create_check_constraint(
        "result_origin_allowed",
        "recommendation_runs",
        f"result_origin IN ({RESULT_ORIGIN_VALUES})",
    )
    op.add_column(
        "job_required_actions",
        sa.Column("expected_input_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "job_required_actions",
        sa.Column("expected_result_version", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_required_actions", "expected_result_version")
    op.drop_column("job_required_actions", "expected_input_version")
    op.drop_constraint(
        "result_origin_allowed",
        "recommendation_runs",
        type_="check",
    )
    op.drop_column("recommendation_runs", "result_origin")
