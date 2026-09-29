"""Add durable delivery markers to private commit-gate outboxes.

Revision ID: 0005_private_gate_delivery
Revises: 0004_private_commit_gate
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_private_gate_delivery"
down_revision: str | None = "0004_private_commit_gate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "private_staged_outbox",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "private_commit_gate_acks",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Destructive downgrade is intentionally unsupported; use a forward corrective migration."
    )
