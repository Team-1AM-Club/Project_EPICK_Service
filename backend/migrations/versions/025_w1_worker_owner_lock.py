"""Allow W1 worker owner-row locking without identity mutation.

Revision ID: 025_w1_worker_owner_lock
Revises: 024_w2_commit_gate
Create Date: 2026-09-17
"""

from __future__ import annotations

from alembic import op

revision = "025_w1_worker_owner_lock"
down_revision = "024_w2_commit_gate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL evaluates UPDATE RLS policies for SELECT ... FOR UPDATE.  W1
    # needs that row lock to serialize account-status/deletion-epoch checks,
    # but must not be able to modify identity data.  USING permits the lock;
    # the always-false WITH CHECK rejects every attempted row mutation.
    op.execute(
        """
        CREATE POLICY users_worker_operational_lock_policy
        ON users
        FOR UPDATE
        TO epick_worker
        USING (true)
        WITH CHECK (false)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS users_worker_operational_lock_policy ON users")
