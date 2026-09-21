"""Keep historical W2 command links immutable while permitting one new retry link.

Revision ID: 040_w2_resume_source_link
Revises: 039_w2_checkpoint_revision
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "040_w2_resume_source_link"
down_revision = "039_w2_checkpoint_revision"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_job_source_links_job_id", "job_source_links", type_="unique")
    op.create_index(
        "uq_job_source_links_unbound",
        "job_source_links",
        ["job_id", "source_id", "purpose_ref"],
        unique=True,
        postgresql_where=sa.text("command_id IS NULL"),
    )


def downgrade() -> None:
    # PostgreSQL rejects this constraint when retry history contains duplicate
    # Job/Source/purpose rows. Never discard history to make a downgrade pass.
    op.drop_index("uq_job_source_links_unbound", table_name="job_source_links")
    op.create_unique_constraint(
        "uq_job_source_links_job_id",
        "job_source_links",
        ["job_id", "source_id", "purpose_ref"],
    )
