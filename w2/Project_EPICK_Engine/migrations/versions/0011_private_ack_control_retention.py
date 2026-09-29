"""Retain payload-free control rows for original private gate ACK replay.

Revision ID: 0011_private_ack_control_retention
Revises: 0010_private_deletion_scope_v2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_private_ack_control_retention"
down_revision: str | None = "0010_private_deletion_scope_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PAYLOAD_MATCHES_STATE = (
    "(state IN ('ABORTED', 'PURGED') AND result_payload IS NULL) OR "
    "(state IN ('STAGED', 'PREPARED', 'FINALIZED') AND ("
    "(payload_purged AND result_payload IS NULL) OR "
    "(NOT payload_purged AND result_payload IS NOT NULL)))"
)


def upgrade() -> None:
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    op.add_column(
        "private_commit_stages",
        sa.Column(
            "payload_purged",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.drop_constraint(
        op.f("ck_private_commit_stages_payload_matches_state"),
        "private_commit_stages",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_private_commit_stages_payload_matches_state"),
        "private_commit_stages",
        _PAYLOAD_MATCHES_STATE,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Destructive downgrade is intentionally unsupported; use a forward corrective migration."
    )
