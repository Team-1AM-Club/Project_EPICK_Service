"""Add the W3 Core Runtime as a forward-only deletion target.

Revision ID: 035_w3_deletion_target
Revises: 034_w3_authority_currentness_rls
Create Date: 2026-09-20
"""

from __future__ import annotations

from alembic import op

revision = "035_w3_deletion_target"
down_revision = "034_w3_authority_currentness_rls"
branch_labels = None
depends_on = None

_STORE_TYPES = (
    "'POSTGRESQL', 'NEO4J', 'VECTOR', 'CACHE', 'CHECKPOINT', 'W3_CORE_RUNTIME'"
)


def upgrade() -> None:
    # Revision 017 supplied an already-prefixed name to a naming convention,
    # so its physical PostgreSQL name contains the ck/table prefix twice.
    op.execute(
        "ALTER TABLE deletion_targets DROP CONSTRAINT "
        '"ck_deletion_targets_ck_deletion_targets_store_type_allowed"'
    )
    op.create_check_constraint(
        "store_type_allowed",
        "deletion_targets",
        f"store_type IN ({_STORE_TYPES})",
    )


def downgrade() -> None:
    raise RuntimeError(
        "forward-only: W3 deletion targets may already exist and cannot be made invalid"
    )
