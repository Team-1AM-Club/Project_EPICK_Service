"""Merge the W2 retry and W3 retention runtime migration branches.

Revision ID: 041_merge_w2_w3_runtime_heads
Revises: 038_w3_retention_deleter_rls, 040_w2_resume_source_link
Create Date: 2026-09-23
"""

from __future__ import annotations

revision = "041_merge_w2_w3_runtime_heads"
down_revision = (
    "038_w3_retention_deleter_rls",
    "040_w2_resume_source_link",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
