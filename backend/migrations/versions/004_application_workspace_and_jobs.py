"""Create owner-scoped application workspace and Job execution ledger.

Revision ID: 004_app_workspace_jobs
Revises: 003_identity_contract_completion
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "004_app_workspace_jobs"
down_revision = "003_identity_contract_completion"
branch_labels = None
depends_on = None

COMPANY_IDENTIFICATION_VALUES = "'PENDING', 'VERIFIED', 'AMBIGUOUS', 'REJECTED'"
PROJECT_STATUS_VALUES = (
    "'DRAFT', 'COLLECTING', 'READY', 'RECOMMENDING', 'MATERIALS_SELECTED', 'STALE', 'ARCHIVED'"
)
QUESTION_STATUS_VALUES = "'ACTIVE', 'ARCHIVED'"
JOB_STATUS_VALUES = (
    "'QUEUED', 'RUNNING', 'WAITING_USER', 'PAUSED_RATE_LIMIT', 'SUCCEEDED', "
    "'FAILED_RETRYABLE', 'FAILED_FINAL', 'CANCEL_REQUESTED', 'CANCELLED'"
)
JOB_COMPLETENESS_VALUES = "'none', 'partial', 'complete'"
DISPATCH_STATUS_VALUES = "'OUTBOX_PENDING', 'ENQUEUED', 'CLAIMED', 'BLOCKED', 'INVALIDATED'"
COMMAND_STATUS_VALUES = "'PENDING', 'ENQUEUED', 'CLAIMED', 'CONSUMED', 'INVALIDATED', 'FAILED'"
REQUIRED_ACTION_STATUS_VALUES = "'OPEN', 'RESOLVED', 'DISMISSED'"
OUTBOX_STATUS_VALUES = "'PENDING', 'PUBLISHED', 'FAILED'"
VISIBILITY_SCOPE_VALUES = "'PUBLIC', 'PRIVATE'"


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_idempotency_records_id_owner_user_id",
        "idempotency_records",
        ["id", "owner_user_id"],
    )
    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("legal_name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("official_domain", sa.Text(), nullable=True),
        sa.Column(
            "identification_status",
            sa.String(length=32),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"identification_status IN ({COMPANY_IDENTIFICATION_VALUES})",
            name="ck_companies_identification_status_allowed",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_companies"),
    )
    op.create_table(
        "application_projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status", sa.String(length=32), server_default=sa.text("'DRAFT'"), nullable=False
        ),
        sa.Column("current_step", sa.String(length=64), nullable=True),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"status IN ({PROJECT_STATUS_VALUES})", name="ck_application_projects_status_allowed"
        ),
        sa.CheckConstraint(
            "lock_version >= 1", name="ck_application_projects_lock_version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_application_projects_owner_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_application_projects"),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_application_projects_id_owner_user_id"),
    )
    op.create_table(
        "application_project_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("season", sa.String(length=64), nullable=True),
        sa.Column("organization_name", sa.Text(), nullable=True),
        sa.Column("role_name", sa.Text(), nullable=False),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version_no >= 1", name="ck_application_project_versions_version_no_positive"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "owner_user_id"],
            ["application_projects.id", "application_projects.owner_user_id"],
            name="fk_application_project_versions_project_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_application_project_versions_company_id_companies",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_application_project_versions"),
        sa.UniqueConstraint(
            "project_id", "version_no", name="uq_application_project_versions_project_id_version_no"
        ),
        sa.UniqueConstraint(
            "id",
            "project_id",
            "owner_user_id",
            name="uq_application_project_versions_id_project_id_owner_user_id",
        ),
        sa.UniqueConstraint(
            "id",
            "project_id",
            "owner_user_id",
            "company_id",
            name="uq_application_project_versions_id_project_owner_company_id",
        ),
        sa.UniqueConstraint(
            "id", "owner_user_id", name="uq_application_project_versions_id_owner_user_id"
        ),
    )
    op.create_foreign_key(
        "fk_application_projects_current_version_scope",
        "application_projects",
        "application_project_versions",
        ["id", "owner_user_id", "current_version_id"],
        ["project_id", "owner_user_id", "id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "project_questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default=sa.text("'ACTIVE'"), nullable=False
        ),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"status IN ({QUESTION_STATUS_VALUES})", name="ck_project_questions_status_allowed"
        ),
        sa.CheckConstraint(
            "display_order >= 0", name="ck_project_questions_display_order_not_negative"
        ),
        sa.CheckConstraint("lock_version >= 1", name="ck_project_questions_lock_version_positive"),
        sa.ForeignKeyConstraint(
            ["project_id", "owner_user_id"],
            ["application_projects.id", "application_projects.owner_user_id"],
            name="fk_project_questions_project_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_project_questions"),
        sa.UniqueConstraint(
            "id",
            "project_id",
            "owner_user_id",
            name="uq_project_questions_id_project_id_owner_user_id",
        ),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_project_questions_id_owner_user_id"),
        sa.UniqueConstraint(
            "project_id", "display_order", name="uq_project_questions_project_id_display_order"
        ),
    )
    op.create_table(
        "question_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("character_limit", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version_no >= 1", name="ck_question_versions_version_no_positive"),
        sa.CheckConstraint(
            "character_limit IS NULL OR character_limit > 0",
            name="ck_question_versions_character_limit_positive",
        ),
        sa.ForeignKeyConstraint(
            ["question_id", "project_id", "owner_user_id"],
            [
                "project_questions.id",
                "project_questions.project_id",
                "project_questions.owner_user_id",
            ],
            name="fk_question_versions_question_project_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_question_versions"),
        sa.UniqueConstraint(
            "question_id", "version_no", name="uq_question_versions_question_id_version_no"
        ),
        sa.UniqueConstraint(
            "id",
            "question_id",
            "owner_user_id",
            name="uq_question_versions_id_question_id_owner_user_id",
        ),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_question_versions_id_owner_user_id"),
    )
    op.create_foreign_key(
        "fk_project_questions_current_version_scope",
        "project_questions",
        "question_versions",
        ["id", "owner_user_id", "current_version_id"],
        ["question_id", "owner_user_id", "id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default=sa.text("'QUEUED'"), nullable=False
        ),
        sa.Column(
            "completeness", sa.String(length=16), server_default=sa.text("'none'"), nullable=False
        ),
        sa.Column(
            "dispatch_status",
            sa.String(length=32),
            server_default=sa.text("'OUTBOX_PENDING'"),
            nullable=False,
        ),
        sa.Column("execution_fence", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("owner_deletion_epoch", sa.BigInteger(), nullable=False),
        sa.Column("analysis_input_version", sa.String(length=64), nullable=True),
        sa.Column("stage", sa.String(length=64), nullable=True),
        sa.Column("idempotency_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("completed_units", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("total_units", sa.Integer(), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("safe_failure_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("retry_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_lease_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(f"status IN ({JOB_STATUS_VALUES})", name="ck_jobs_status_allowed"),
        sa.CheckConstraint(
            f"completeness IN ({JOB_COMPLETENESS_VALUES})", name="ck_jobs_completeness_allowed"
        ),
        sa.CheckConstraint(
            f"dispatch_status IN ({DISPATCH_STATUS_VALUES})", name="ck_jobs_dispatch_status_allowed"
        ),
        sa.CheckConstraint("execution_fence >= 1", name="ck_jobs_execution_fence_positive"),
        sa.CheckConstraint(
            "owner_deletion_epoch >= 0", name="ck_jobs_owner_deletion_epoch_not_negative"
        ),
        sa.CheckConstraint("completed_units >= 0", name="ck_jobs_completed_units_not_negative"),
        sa.CheckConstraint(
            "total_units IS NULL OR total_units >= 0", name="ck_jobs_total_units_not_negative"
        ),
        sa.CheckConstraint(
            "total_units IS NULL OR completed_units <= total_units",
            name="ck_jobs_completed_units_at_most_total",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name="fk_jobs_owner_user_id_users", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "owner_user_id"],
            ["application_projects.id", "application_projects.owner_user_id"],
            name="fk_jobs_project_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["idempotency_record_id", "owner_user_id"],
            ["idempotency_records.id", "idempotency_records.owner_user_id"],
            name="fk_jobs_idempotency_record_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_jobs_id_owner_user_id"),
        sa.UniqueConstraint("idempotency_record_id", name="uq_jobs_idempotency_record_id"),
    )
    op.create_table(
        "job_input_refs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("episode_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_name", sa.String(length=128), nullable=True),
        sa.Column("policy_version", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(project_version_id IS NOT NULL)::integer + "
            "(question_version_id IS NOT NULL)::integer + "
            "(episode_version_id IS NOT NULL)::integer + "
            "((policy_name IS NOT NULL AND policy_version IS NOT NULL)::integer) = 1 "
            "AND (policy_name IS NULL) = (policy_version IS NULL)",
            name="ck_job_input_refs_exactly_one_input",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["jobs.id", "jobs.owner_user_id"],
            name="fk_job_input_refs_job_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_version_id", "owner_user_id"],
            ["application_project_versions.id", "application_project_versions.owner_user_id"],
            name="fk_job_input_refs_project_version_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_version_id", "owner_user_id"],
            ["question_versions.id", "question_versions.owner_user_id"],
            name="fk_job_input_refs_question_version_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["episode_version_id", "owner_user_id"],
            ["episode_versions.id", "episode_versions.owner_user_id"],
            name="fk_job_input_refs_episode_version_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_job_input_refs"),
    )
    op.create_index(
        "uq_job_input_refs_job_project_version",
        "job_input_refs",
        ["job_id", "project_version_id"],
        unique=True,
        postgresql_where=sa.text("project_version_id IS NOT NULL"),
    )
    op.create_index(
        "uq_job_input_refs_job_question_version",
        "job_input_refs",
        ["job_id", "question_version_id"],
        unique=True,
        postgresql_where=sa.text("question_version_id IS NOT NULL"),
    )
    op.create_index(
        "uq_job_input_refs_job_episode_version",
        "job_input_refs",
        ["job_id", "episode_version_id"],
        unique=True,
        postgresql_where=sa.text("episode_version_id IS NOT NULL"),
    )
    op.create_index(
        "uq_job_input_refs_job_policy",
        "job_input_refs",
        ["job_id", "policy_name", "policy_version"],
        unique=True,
        postgresql_where=sa.text("policy_name IS NOT NULL"),
    )
    op.create_table(
        "job_required_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action_code", sa.String(length=64), nullable=False),
        sa.Column(
            "action_status", sa.String(length=32), server_default=sa.text("'OPEN'"), nullable=False
        ),
        sa.Column("context_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"action_status IN ({REQUIRED_ACTION_STATUS_VALUES})",
            name="ck_job_required_actions_action_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["jobs.id", "jobs.owner_user_id"],
            name="fk_job_required_actions_job_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_job_required_actions"),
    )
    op.create_index(
        "uq_job_required_actions_open_action",
        "job_required_actions",
        ["job_id", "action_code"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.create_table(
        "job_commands",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_type", sa.String(length=64), nullable=False),
        sa.Column("command_schema_version", sa.String(length=32), nullable=False),
        sa.Column("command_sequence", sa.Integer(), nullable=False),
        sa.Column("execution_fence", sa.BigInteger(), nullable=False),
        sa.Column("owner_deletion_epoch", sa.BigInteger(), nullable=False),
        sa.Column("analysis_input_version", sa.String(length=64), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default=sa.text("'PENDING'"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "command_sequence >= 1", name="ck_job_commands_command_sequence_positive"
        ),
        sa.CheckConstraint("execution_fence >= 1", name="ck_job_commands_execution_fence_positive"),
        sa.CheckConstraint(
            "owner_deletion_epoch >= 0", name="ck_job_commands_owner_deletion_epoch_not_negative"
        ),
        sa.CheckConstraint(
            f"status IN ({COMMAND_STATUS_VALUES})", name="ck_job_commands_status_allowed"
        ),
        sa.CheckConstraint(
            "octet_length(payload::text) <= 16384", name="ck_job_commands_payload_max_16kib"
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["jobs.id", "jobs.owner_user_id"],
            name="fk_job_commands_job_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_job_commands"),
        sa.UniqueConstraint(
            "job_id", "command_sequence", name="uq_job_commands_job_id_command_sequence"
        ),
        sa.UniqueConstraint(
            "id", "job_id", "owner_user_id", name="uq_job_commands_id_job_id_owner_user_id"
        ),
    )
    op.create_table(
        "outbox_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_type", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("visibility_scope", sa.String(length=16), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_revision", sa.BigInteger(), nullable=False),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("execution_fence", sa.BigInteger(), nullable=True),
        sa.Column("owner_deletion_epoch", sa.BigInteger(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default=sa.text("'PENDING'"), nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"visibility_scope IN ({VISIBILITY_SCOPE_VALUES})",
            name="ck_outbox_messages_visibility_scope_allowed",
        ),
        sa.CheckConstraint(
            f"status IN ({OUTBOX_STATUS_VALUES})", name="ck_outbox_messages_status_allowed"
        ),
        sa.CheckConstraint(
            "aggregate_revision >= 1", name="ck_outbox_messages_aggregate_revision_positive"
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_messages_attempts_not_negative"),
        sa.CheckConstraint(
            "(visibility_scope = 'PUBLIC' AND owner_user_id IS NULL AND job_id IS NULL "
            "AND command_id IS NULL AND execution_fence IS NULL AND owner_deletion_epoch IS NULL) "
            "OR (visibility_scope = 'PRIVATE' AND owner_user_id IS NOT NULL AND job_id IS NOT NULL "
            "AND command_id IS NOT NULL AND execution_fence IS NOT NULL "
            "AND owner_deletion_epoch IS NOT NULL)",
            name="ck_outbox_messages_visibility_reference_scope",
        ),
        sa.ForeignKeyConstraint(
            ["command_id", "job_id", "owner_user_id"],
            ["job_commands.id", "job_commands.job_id", "job_commands.owner_user_id"],
            name="fk_outbox_messages_command_job_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["jobs.id", "jobs.owner_user_id"],
            name="fk_outbox_messages_job_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_messages"),
    )
    op.create_index(
        "uq_outbox_messages_public_aggregate_revision_type",
        "outbox_messages",
        ["aggregate_type", "aggregate_id", "aggregate_revision", "message_type"],
        unique=True,
        postgresql_where=sa.text("visibility_scope = 'PUBLIC'"),
    )
    op.create_index(
        "ix_outbox_messages_command_id",
        "outbox_messages",
        ["command_id"],
        postgresql_where=sa.text("command_id IS NOT NULL"),
    )
    op.create_table(
        "inbox_receipts",
        sa.Column("consumer_name", sa.String(length=128), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("outcome_code", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("consumer_name", "event_id", name="pk_inbox_receipts"),
    )
    op.create_table(
        "owner_execution_slots",
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slot_no", sa.Integer(), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "slot_no BETWEEN 1 AND 3", name="ck_owner_execution_slots_slot_no_in_range"
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["jobs.id", "jobs.owner_user_id"],
            name="fk_owner_execution_slots_job_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("owner_user_id", "slot_no", name="pk_owner_execution_slots"),
    )
    op.create_table(
        "job_execution_leases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slot_no", sa.Integer(), nullable=False),
        sa.Column("execution_fence", sa.BigInteger(), nullable=False),
        sa.Column("owner_deletion_epoch", sa.BigInteger(), nullable=False),
        sa.Column("worker_ref", sa.String(length=128), nullable=True),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_reason", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "execution_fence >= 1", name="ck_job_execution_leases_execution_fence_positive"
        ),
        sa.CheckConstraint(
            "owner_deletion_epoch >= 0",
            name="ck_job_execution_leases_owner_deletion_epoch_not_negative",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["jobs.id", "jobs.owner_user_id"],
            name="fk_job_execution_leases_job_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "slot_no"],
            ["owner_execution_slots.owner_user_id", "owner_execution_slots.slot_no"],
            name="fk_job_execution_leases_slot_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_job_execution_leases"),
    )
    op.create_foreign_key(
        "fk_owner_execution_slots_lease_id_job_execution_leases",
        "owner_execution_slots",
        "job_execution_leases",
        ["lease_id"],
        ["id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_jobs_active_lease_id_job_execution_leases",
        "jobs",
        "job_execution_leases",
        ["active_lease_id"],
        ["id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_index(
        "uq_job_execution_leases_active_job",
        "job_execution_leases",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )
    op.create_index(
        "uq_job_execution_leases_active_slot",
        "job_execution_leases",
        ["owner_user_id", "slot_no"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )
    op.create_index(
        "ix_application_projects_owner_status_updated",
        "application_projects",
        ["owner_user_id", "status", sa.text("updated_at DESC")],
    )
    op.create_index(
        "ix_jobs_owner_status_updated",
        "jobs",
        ["owner_user_id", "status", sa.text("updated_at DESC")],
    )
    for table_name in (
        "application_projects",
        "application_project_versions",
        "project_questions",
        "question_versions",
        "jobs",
        "job_input_refs",
        "job_required_actions",
        "job_commands",
        "outbox_messages",
        "owner_execution_slots",
        "job_execution_leases",
    ):
        _enable_owner_rls(table_name, "owner_user_id")


def downgrade() -> None:
    op.drop_constraint(
        "fk_project_questions_current_version_scope",
        "project_questions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_application_projects_current_version_scope",
        "application_projects",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_owner_execution_slots_lease_id_job_execution_leases",
        "owner_execution_slots",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_jobs_active_lease_id_job_execution_leases",
        "jobs",
        type_="foreignkey",
    )
    for table_name in (
        "job_execution_leases",
        "owner_execution_slots",
        "inbox_receipts",
        "outbox_messages",
        "job_commands",
        "job_required_actions",
        "job_input_refs",
        "jobs",
        "question_versions",
        "project_questions",
        "application_project_versions",
        "application_projects",
        "companies",
    ):
        op.drop_table(table_name)
    op.drop_constraint(
        "uq_idempotency_records_id_owner_user_id", "idempotency_records", type_="unique"
    )


def _enable_owner_rls(table_name: str, owner_column: str) -> None:
    policy_name = f"{table_name}_owner_policy"
    expression = f"{owner_column} = NULLIF(current_setting('app.current_user_id', true), '')::uuid"
    policy_statement = (
        f"CREATE POLICY {policy_name} ON {table_name} "
        f"USING ({expression}) WITH CHECK ({expression})"
    )
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(policy_statement)
