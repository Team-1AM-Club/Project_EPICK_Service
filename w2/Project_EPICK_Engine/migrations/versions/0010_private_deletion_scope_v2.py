"""Add explicit private scope attribution and deletion tombstones.

Revision ID: 0010_private_deletion_scope_v2
Revises: 0009_private_deletion_receipt
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_private_deletion_scope_v2"
down_revision: str | None = "0009_private_deletion_receipt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRIVATE_SCOPE_CHECK = (
    "(private_scope_kind = 'PROJECT' AND project_id IS NOT NULL) OR "
    "(private_scope_kind = 'ACCOUNT' AND project_id IS NULL) OR "
    "private_scope_kind = 'UNKNOWN'"
)


def _add_private_scope(
    table_name: str,
    *,
    owner_column: str,
    add_project_id: bool,
) -> None:
    op.add_column(
        table_name,
        sa.Column(
            "private_scope_kind",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'UNKNOWN'"),
        ),
    )
    if add_project_id:
        op.add_column(
            table_name,
            sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
    op.create_check_constraint(
        op.f(f"ck_{table_name}_valid_private_scope"),
        table_name,
        _PRIVATE_SCOPE_CHECK,
    )
    op.create_index(
        f"ix_{table_name}_owner_private_scope",
        table_name,
        [owner_column, "private_scope_kind", "project_id"],
        unique=False,
    )


def upgrade() -> None:
    op.alter_column(
        "collection_attempts",
        "owner_deletion_epoch",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )
    _add_private_scope(
        "collection_attempts",
        owner_column="owner_user_id",
        add_project_id=False,
    )
    _add_private_scope(
        "request_deduplications",
        owner_column="owner_user_id",
        add_project_id=True,
    )
    _add_private_scope(
        "collection_runtime_attempts",
        owner_column="owner_ref",
        add_project_id=True,
    )
    _add_private_scope(
        "private_commit_stages",
        owner_column="owner_ref",
        add_project_id=True,
    )

    op.add_column(
        "private_deletion_owner_states",
        sa.Column(
            "account_deleted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_table(
        "private_deletion_project_tombstones",
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("deletion_epoch", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "deletion_epoch > 0",
            name=op.f("ck_private_deletion_project_tombstones_positive_deletion_epoch"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["private_deletion_owner_states.owner_user_id"],
            name="fk_private_deletion_project_tombstones_owner_state",
        ),
        sa.PrimaryKeyConstraint(
            "owner_user_id",
            "project_id",
            name=op.f("pk_private_deletion_project_tombstones"),
        ),
    )
    op.add_column(
        "private_deletion_receipts",
        sa.Column(
            "contract_version",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'w2.private-deletion.v1'"),
        ),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Destructive downgrade is intentionally unsupported; use a forward corrective migration."
    )
