"""Record W1-confirmed private deletion ACKs without assuming historical success.

Revision ID: 0013_deletion_ack_confirmed
Revises: 0012_private_ack_wire_digest
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_deletion_ack_confirmed"
down_revision: str | None = "0012_private_ack_wire_digest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "private_deletion_receipts",
        sa.Column("ack_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Destructive downgrade is intentionally unsupported; use a forward corrective migration."
    )
