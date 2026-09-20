"""Allow narrowly-scoped pre-principal browser authentication lookups.

Revision ID: 031_browser_auth_rls
Revises: 030_w4_worker_currentness_rls
Create Date: 2026-09-20
"""

from __future__ import annotations

from alembic import op

revision = "031_browser_auth_rls"
down_revision = "030_w4_worker_currentness_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE POLICY auth_identities_provider_subject_lookup_policy
        ON auth_identities FOR SELECT TO epick_runtime
        USING (
            provider || ':' || provider_subject =
            NULLIF(current_setting('app.auth_provider_subject', true), '')
        )
        """
    )
    op.execute(
        """
        CREATE POLICY auth_sessions_refresh_hash_lookup_policy
        ON auth_sessions FOR SELECT TO epick_runtime
        USING (
            refresh_token_hash =
            NULLIF(current_setting('app.auth_refresh_hash', true), '')
        )
        """
    )
    # PostgreSQL evaluates UPDATE policies for SELECT ... FOR UPDATE. These
    # policies permit only the pre-principal row lock; the false WITH CHECK
    # prevents mutation until the normal owner context is established.
    op.execute(
        """
        CREATE POLICY auth_identities_provider_subject_lock_policy
        ON auth_identities FOR UPDATE TO epick_runtime
        USING (
            provider || ':' || provider_subject =
            NULLIF(current_setting('app.auth_provider_subject', true), '')
        )
        WITH CHECK (false)
        """
    )
    op.execute(
        """
        CREATE POLICY auth_sessions_refresh_hash_lock_policy
        ON auth_sessions FOR UPDATE TO epick_runtime
        USING (
            refresh_token_hash =
            NULLIF(current_setting('app.auth_refresh_hash', true), '')
        )
        WITH CHECK (false)
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS auth_sessions_refresh_hash_lock_policy ON auth_sessions"
    )
    op.execute(
        "DROP POLICY IF EXISTS auth_identities_provider_subject_lock_policy ON auth_identities"
    )
    op.execute(
        "DROP POLICY IF EXISTS auth_sessions_refresh_hash_lookup_policy ON auth_sessions"
    )
    op.execute(
        "DROP POLICY IF EXISTS auth_identities_provider_subject_lookup_policy ON auth_identities"
    )
