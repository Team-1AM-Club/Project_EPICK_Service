"""Persist immutable API replay payloads in the idempotency ledger.

Revision ID: 021_idempotency_snapshot
Revises: 020_api_contract_state
Create Date: 2026-09-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "021_idempotency_snapshot"
down_revision = "020_api_contract_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "idempotency_records",
        sa.Column("response_body", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("idempotency_records", "response_body")
