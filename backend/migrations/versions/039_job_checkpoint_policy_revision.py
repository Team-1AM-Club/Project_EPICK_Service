"""Preserve W2's effective policy revision on immutable Job checkpoints.

Revision ID: 039_w2_checkpoint_revision
Revises: 034_w3_authority_currentness_rls
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "039_w2_checkpoint_revision"
down_revision = "034_w3_authority_currentness_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("job_checkpoints", sa.Column("policy_revision", sa.Integer(), nullable=True))
    op.create_check_constraint(
        op.f("ck_job_checkpoints_policy_revision_positive"),
        "job_checkpoints",
        "policy_revision IS NULL OR policy_revision >= 1",
    )
    # Historical W2 non-policy checkpoints did not record a revision. Never infer one.
    # NOT VALID keeps those immutable rows intact while rejecting any new invalid row.
    op.execute(
        "ALTER TABLE job_checkpoints ADD CONSTRAINT "
        "ck_job_checkpoints_w2_non_policy_revision_required "
        "CHECK (checkpoint_schema_version <> 'w2.collection.v1' "
        "OR resume_stage = 'policy' "
        "OR (policy_revision IS NOT NULL AND policy_revision >= 1)) NOT VALID"
    )
def downgrade() -> None:
    op.execute(
        "ALTER TABLE job_checkpoints DROP CONSTRAINT "
        "ck_job_checkpoints_w2_non_policy_revision_required"
    )
    op.drop_constraint(
        op.f("ck_job_checkpoints_policy_revision_positive"),
        "job_checkpoints",
        type_="check",
    )
    op.drop_column("job_checkpoints", "policy_revision")
