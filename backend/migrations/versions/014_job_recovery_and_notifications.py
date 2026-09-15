"""Add safe Job checkpoints and owner-scoped notifications.

Revision ID: 014_job_recovery_notifications
Revises: 013_inference_duplicates
Create Date: 2026-09-15

Checkpoint rows are immutable recovery evidence.  A checkpoint is useful only when its
input version, execution fence, and deletion epoch still match the current private Job.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "014_job_recovery_notifications"
down_revision = "013_inference_duplicates"
branch_labels = None
depends_on = None

NOTIFICATION_SEVERITY_VALUES = "'INFO', 'WARNING', 'ERROR'"
CHECKPOINT_PAYLOAD_ALLOWED_KEYS = "ARRAY['cursor', 'next_page', 'snapshot_id', 'source_version_id']"


def upgrade() -> None:
    op.create_table(
        "job_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("checkpoint_revision", sa.Integer(), nullable=False),
        sa.Column(
            "checkpoint_schema_version",
            sa.String(length=32),
            server_default=sa.text("'1.0'"),
            nullable=False,
        ),
        sa.Column("analysis_input_version", sa.String(length=64), nullable=True),
        sa.Column("execution_fence", sa.BigInteger(), nullable=False),
        sa.Column("owner_deletion_epoch", sa.BigInteger(), nullable=False),
        sa.Column("resume_stage", sa.String(length=64), nullable=False),
        sa.Column("state_ref", sa.String(length=512), nullable=True),
        sa.Column(
            "resume_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("resumable", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "checkpoint_revision >= 1", name="ck_job_checkpoints_checkpoint_revision_positive"
        ),
        sa.CheckConstraint(
            "execution_fence >= 1", name="ck_job_checkpoints_execution_fence_positive"
        ),
        sa.CheckConstraint(
            "owner_deletion_epoch >= 0",
            name="ck_job_checkpoints_owner_deletion_epoch_not_negative",
        ),
        sa.CheckConstraint(
            "length(btrim(checkpoint_schema_version)) > 0",
            name="ck_job_checkpoints_schema_version_present",
        ),
        sa.CheckConstraint(
            "length(btrim(resume_stage)) > 0", name="ck_job_checkpoints_resume_stage_present"
        ),
        sa.CheckConstraint(
            "state_ref IS NULL OR length(btrim(state_ref)) > 0",
            name="ck_job_checkpoints_state_ref_present",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(resume_payload) = 'object'",
            name="ck_job_checkpoints_resume_payload_object",
        ),
        sa.CheckConstraint(
            "octet_length(resume_payload::text) <= 16384",
            name="ck_job_checkpoints_resume_payload_max_16kib",
        ),
        sa.CheckConstraint(
            f"resume_payload - {CHECKPOINT_PAYLOAD_ALLOWED_KEYS} = '{{}}'::jsonb",
            name="ck_job_checkpoints_resume_payload_allowlist",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["jobs.id", "jobs.owner_user_id"],
            name="fk_job_checkpoints_job_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_job_checkpoints"),
        sa.UniqueConstraint(
            "job_id", "checkpoint_revision", name="uq_job_checkpoints_job_checkpoint_revision"
        ),
    )
    op.create_index(
        "ix_job_checkpoints_owner_job_created",
        "job_checkpoints",
        ["owner_user_id", "job_id", sa.text("created_at DESC")],
    )
    op.execute(
        """
        CREATE FUNCTION validate_job_checkpoint_current_execution()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            current_fence bigint;
            current_epoch bigint;
            current_input_version text;
        BEGIN
            SELECT execution_fence, owner_deletion_epoch, analysis_input_version
            INTO current_fence, current_epoch, current_input_version
            FROM jobs
            WHERE id = NEW.job_id AND owner_user_id = NEW.owner_user_id;
            IF current_fence IS NULL
               OR NEW.execution_fence <> current_fence
               OR NEW.owner_deletion_epoch <> current_epoch
               OR NEW.analysis_input_version IS DISTINCT FROM current_input_version THEN
                RAISE EXCEPTION 'checkpoint is not bound to the current Job execution'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_job_checkpoints_current_execution
        BEFORE INSERT OR UPDATE ON job_checkpoints
        FOR EACH ROW EXECUTE FUNCTION validate_job_checkpoint_current_execution();
        """
    )
    _enable_owner_rls("job_checkpoints", "owner_user_id")
    # A later deletion orchestration revision may physically remove a private
    # checkpoint, but ordinary runtime code must never rewrite recovery evidence.
    op.execute(
        "CREATE TRIGGER trg_job_checkpoints_no_update BEFORE UPDATE ON job_checkpoints "
        "FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()"
    )

    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notification_type", sa.String(length=64), nullable=False),
        sa.Column(
            "severity", sa.String(length=16), server_default=sa.text("'INFO'"), nullable=False
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("safe_message", sa.Text(), nullable=False),
        sa.Column("action_url", sa.Text(), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(notification_type)) > 0",
            name="ck_notifications_notification_type_present",
        ),
        sa.CheckConstraint(
            f"severity IN ({NOTIFICATION_SEVERITY_VALUES})",
            name="ck_notifications_severity_allowed",
        ),
        sa.CheckConstraint(
            "octet_length(title) <= 512", name="ck_notifications_title_max_512bytes"
        ),
        sa.CheckConstraint(
            "octet_length(safe_message) <= 4096",
            name="ck_notifications_safe_message_max_4kib",
        ),
        sa.CheckConstraint(
            "action_url IS NULL OR (octet_length(action_url) <= 2048 "
            "AND action_url LIKE '/%' AND position('?' IN action_url) = 0 "
            "AND position('#' IN action_url) = 0)",
            name="ck_notifications_action_url_safe_relative_path",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name="fk_notifications_owner", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "owner_user_id"],
            ["application_projects.id", "application_projects.owner_user_id"],
            name="fk_notifications_project_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
    )
    op.create_index(
        "ix_notifications_owner_unread_created",
        "notifications",
        ["owner_user_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("read_at IS NULL AND archived_at IS NULL"),
    )
    _enable_owner_rls("notifications", "owner_user_id")


def downgrade() -> None:
    for table_name in ("notifications", "job_checkpoints"):
        op.execute(f"DROP POLICY IF EXISTS {table_name}_owner_policy ON {table_name}")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_notifications_owner_unread_created", table_name="notifications")
    op.drop_table("notifications")
    op.execute("DROP TRIGGER IF EXISTS trg_job_checkpoints_no_update ON job_checkpoints")
    op.execute("DROP TRIGGER IF EXISTS trg_job_checkpoints_current_execution ON job_checkpoints")
    op.execute("DROP FUNCTION IF EXISTS validate_job_checkpoint_current_execution()")
    op.drop_index("ix_job_checkpoints_owner_job_created", table_name="job_checkpoints")
    op.drop_table("job_checkpoints")


def _enable_owner_rls(table_name: str, owner_column: str) -> None:
    expression = f"{owner_column} = NULLIF(current_setting('app.current_user_id', true), '')::uuid"
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table_name}_owner_policy ON {table_name} "
        f"USING ({expression}) WITH CHECK ({expression})"
    )
