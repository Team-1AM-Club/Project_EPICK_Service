"""Add the fenced W4 recommendation execution boundary.

Revision ID: 032_w4_recommendation_execution
Revises: 031_browser_auth_rls
Create Date: 2026-09-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "032_w4_recommendation_execution"
down_revision = "031_browser_auth_rls"
branch_labels = None
depends_on = None

_EXECUTION_TABLES = (
    "recommendation_execution_bindings",
    "recommendation_execution_episodes",
    "recommendation_publications",
    "recommendation_source_dependencies",
)


def upgrade() -> None:
    _require_runtime_roles()
    op.create_table(
        "recommendation_execution_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "execution_status", sa.String(length=16), server_default="PENDING", nullable=False
        ),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_no", sa.Integer(), server_default="0", nullable=False),
        sa.Column("owner_deletion_epoch", sa.BigInteger(), nullable=False),
        sa.Column("context_sha256", sa.String(length=71), nullable=False),
        sa.Column("contract_version", sa.String(length=64), nullable=False),
        sa.Column("engine_source_revision", sa.String(length=64), nullable=False),
        sa.Column("request_body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("context_body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "execution_status IN ('PENDING','RUNNING','PUBLISHED','FAILED','CANCELLED')",
            name="reb_execution_status_allowed",
        ),
        sa.CheckConstraint("attempt_no >= 0", name="reb_attempt_no_not_negative"),
        sa.CheckConstraint(
            "owner_deletion_epoch >= 0", name="reb_owner_deletion_epoch_not_negative"
        ),
        sa.CheckConstraint(
            "context_sha256 ~ '^sha256:[0-9a-f]{64}$'", name="reb_context_sha256_format"
        ),
        sa.CheckConstraint(
            "(lease_token IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="reb_lease_complete",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "owner_user_id"],
            ["recommendation_runs.id", "recommendation_runs.owner_user_id"],
            name="reb_run_owner_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="reb_run_id"),
        sa.UniqueConstraint("id", "owner_user_id", name="reb_id_owner_user_id"),
    )
    op.create_index(
        "ix_recommendation_execution_bindings_claim",
        "recommendation_execution_bindings",
        ["execution_status", "lease_expires_at", "created_at"],
    )
    op.create_table(
        "recommendation_execution_episodes",
        sa.Column("binding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("episode_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("episode_id", sa.String(length=200), nullable=False),
        sa.Column("episode_version", sa.Integer(), nullable=False),
        sa.CheckConstraint("episode_version >= 1", name="ree_episode_version_positive"),
        sa.ForeignKeyConstraint(
            ["binding_id", "owner_user_id"],
            [
                "recommendation_execution_bindings.id",
                "recommendation_execution_bindings.owner_user_id",
            ],
            name="ree_binding_owner_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["episode_version_id", "owner_user_id"],
            ["episode_versions.id", "episode_versions.owner_user_id"],
            name="ree_episode_version_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("binding_id", "episode_version_id"),
        sa.UniqueConstraint(
            "binding_id", "episode_id", "episode_version", name="ree_binding_episode_version"
        ),
        sa.UniqueConstraint(
            "binding_id", "episode_version_id", name="ree_binding_episode_version_id"
        ),
    )
    op.create_table(
        "recommendation_publications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("binding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("result_version", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("engine_source_revision", sa.String(length=64), nullable=False),
        sa.Column("input_data_kind", sa.String(length=16), nullable=False),
        sa.Column("processing_status", sa.String(length=64), nullable=False),
        sa.Column("limited_analysis", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "limitations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("full_result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_sha256", sa.String(length=71), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("input_data_kind = 'SYNTHETIC'", name="rp_synthetic_only"),
        sa.CheckConstraint(
            "content_sha256 ~ '^sha256:[0-9a-f]{64}$'", name="rp_content_sha256_format"
        ),
        sa.ForeignKeyConstraint(
            ["binding_id", "owner_user_id"],
            [
                "recommendation_execution_bindings.id",
                "recommendation_execution_bindings.owner_user_id",
            ],
            name="rp_binding_owner_scope",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "owner_user_id"],
            ["recommendation_runs.id", "recommendation_runs.owner_user_id"],
            name="rp_run_owner_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("binding_id", name="rp_binding_id"),
        sa.UniqueConstraint("run_id", name="rp_run_id"),
    )
    op.create_table(
        "recommendation_source_dependencies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("publication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.String(length=200), nullable=False),
        sa.Column("source_version_id", sa.String(length=200), nullable=False),
        sa.Column("extraction_revision_id", sa.String(length=200), nullable=True),
        sa.Column("representation", sa.String(length=200), nullable=True),
        sa.Column("normalization_version", sa.String(length=200), nullable=True),
        sa.Column("knowledge_generation", sa.BigInteger(), nullable=True),
        sa.Column("restriction_revision", sa.String(length=200), nullable=True),
        sa.Column("dependency_digest", sa.String(length=71), nullable=False),
        sa.CheckConstraint(
            "dependency_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="rsd_dependency_digest_format",
        ),
        sa.ForeignKeyConstraint(
            ["publication_id"], ["recommendation_publications.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "publication_id", "dependency_digest", name="rsd_publication_dependency"
        ),
    )

    op.add_column(
        "outbox_messages",
        sa.Column("recommendation_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "recommendation_run_owner_scope",
        "outbox_messages",
        "recommendation_runs",
        ["recommendation_run_id", "owner_user_id"],
        ["id", "owner_user_id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "ck_outbox_messages_visibility_reference_scope",
        "outbox_messages",
        type_="check",
    )
    op.create_check_constraint(
        "ck_outbox_messages_visibility_reference_scope",
        "outbox_messages",
        "(visibility_scope = 'PUBLIC' AND owner_user_id IS NULL AND job_id IS NULL "
        "AND command_id IS NULL AND execution_fence IS NULL AND owner_deletion_epoch IS NULL "
        "AND deletion_request_id IS NULL AND deletion_target_id IS NULL "
        "AND recommendation_run_id IS NULL) OR "
        "(visibility_scope = 'PRIVATE' AND owner_user_id IS NOT NULL AND job_id IS NOT NULL "
        "AND command_id IS NOT NULL AND execution_fence IS NOT NULL "
        "AND owner_deletion_epoch IS NOT NULL AND deletion_request_id IS NULL "
        "AND deletion_target_id IS NULL AND recommendation_run_id IS NULL) OR "
        "(visibility_scope = 'PRIVATE' AND owner_user_id IS NOT NULL AND job_id IS NULL "
        "AND command_id IS NULL AND execution_fence IS NULL AND owner_deletion_epoch IS NOT NULL "
        "AND deletion_request_id IS NOT NULL AND deletion_target_id IS NOT NULL "
        "AND recommendation_run_id IS NULL) OR "
        "(visibility_scope = 'PRIVATE' AND owner_user_id IS NOT NULL "
        "AND recommendation_run_id IS NOT NULL AND job_id IS NULL AND command_id IS NULL "
        "AND execution_fence IS NULL AND owner_deletion_epoch IS NOT NULL "
        "AND deletion_request_id IS NULL AND deletion_target_id IS NULL)",
    )

    for table_name in _EXECUTION_TABLES:
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table_name}_worker_policy ON {table_name} "
            "FOR ALL TO epick_worker USING (true) WITH CHECK (true)"
        )
    for table_name in (
        "recommendation_execution_bindings",
        "recommendation_execution_episodes",
        "recommendation_publications",
    ):
        op.execute(
            f"CREATE POLICY {table_name}_runtime_owner_policy ON {table_name} "
            "FOR ALL TO epick_runtime "
            "USING (owner_user_id = "
            "NULLIF(current_setting('app.current_user_id', true), '')::uuid) "
            "WITH CHECK (owner_user_id = "
            "NULLIF(current_setting('app.current_user_id', true), '')::uuid)"
        )


def downgrade() -> None:
    op.drop_constraint(
        "ck_outbox_messages_visibility_reference_scope",
        "outbox_messages",
        type_="check",
    )
    op.create_check_constraint(
        "ck_outbox_messages_visibility_reference_scope",
        "outbox_messages",
        "(visibility_scope = 'PUBLIC' AND owner_user_id IS NULL AND job_id IS NULL "
        "AND command_id IS NULL AND execution_fence IS NULL AND owner_deletion_epoch IS NULL "
        "AND deletion_request_id IS NULL AND deletion_target_id IS NULL) OR "
        "(visibility_scope = 'PRIVATE' AND owner_user_id IS NOT NULL AND job_id IS NOT NULL "
        "AND command_id IS NOT NULL AND execution_fence IS NOT NULL "
        "AND owner_deletion_epoch IS NOT NULL AND deletion_request_id IS NULL "
        "AND deletion_target_id IS NULL) OR "
        "(visibility_scope = 'PRIVATE' AND owner_user_id IS NOT NULL AND job_id IS NULL "
        "AND command_id IS NULL AND execution_fence IS NULL AND owner_deletion_epoch IS NOT NULL "
        "AND deletion_request_id IS NOT NULL AND deletion_target_id IS NOT NULL)",
    )
    op.drop_constraint("recommendation_run_owner_scope", "outbox_messages", type_="foreignkey")
    op.drop_column("outbox_messages", "recommendation_run_id")
    for table_name in reversed(_EXECUTION_TABLES):
        op.drop_table(table_name)


def _require_runtime_roles() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_runtime')
               OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_worker') THEN
                RAISE EXCEPTION 'epick_runtime and epick_worker roles are required';
            END IF;
        END
        $$;
        """
    )
