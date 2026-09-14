"""Complete the identity deletion-epoch contract with a forward-only migration.

Revision ID: 003_identity_contract_completion
Revises: 002_create_experience_repository
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "003_identity_contract_completion"
down_revision = "002_create_experience_repository"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "deletion_epoch",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_check_constraint(
        "ck_users_deletion_epoch_not_negative",
        "users",
        "deletion_epoch >= 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_deletion_epoch_not_negative", "users", type_="check")
    op.drop_column("users", "deletion_epoch")
