"""Align recommendation candidate result versions with the W4 contract.

Revision ID: 033_w4_candidate_result_version
Revises: 032_w4_recommendation_execution
Create Date: 2026-09-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "033_w4_candidate_result_version"
down_revision = "032_w4_recommendation_execution"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "recommendation_candidates",
        "result_version",
        existing_type=sa.String(length=64),
        type_=sa.String(length=128),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "recommendation_candidates",
        "result_version",
        existing_type=sa.String(length=128),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
