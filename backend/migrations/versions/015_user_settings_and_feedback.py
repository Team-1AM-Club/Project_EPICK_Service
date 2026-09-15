"""Add owner settings, consent, feedback, and opt-in analytics controls.

Revision ID: 015_user_settings_feedback
Revises: 014_job_recovery_notifications
Create Date: 2026-09-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "015_user_settings_feedback"
down_revision = "014_job_recovery_notifications"
branch_labels = None
depends_on = None

DECISION_HELPFULNESS_VALUES = "'HELPFUL', 'NOT_HELPFUL', 'NOT_SURE'"
ANALYTICS_FORBIDDEN_PROPERTY_KEYS = (
    "ARRAY['auth_subject', 'email', 'original_narrative', 'prompt', 'response', "
    "'secret', 'text', 'token', 'url', 'url_query']"
)


def upgrade() -> None:
    op.create_table(
        "user_settings",
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("locale", sa.String(length=32), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column(
            "display_options",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("lock_version >= 1", name="ck_user_settings_lock_version_positive"),
        sa.CheckConstraint(
            "jsonb_typeof(display_options) = 'object'",
            name="ck_user_settings_display_options_object",
        ),
        sa.CheckConstraint(
            "octet_length(display_options::text) <= 4096",
            name="ck_user_settings_display_options_max_4kib",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name="fk_user_settings_owner", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("owner_user_id", name="pk_user_settings"),
    )
    op.create_table(
        "recommendation_preferences",
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("default_candidate_limit", sa.Integer(), nullable=False),
        sa.Column("question_display_mode", sa.String(length=64), nullable=False),
        sa.Column("evidence_display_mode", sa.String(length=64), nullable=False),
        sa.Column(
            "show_information_completeness",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "default_candidate_limit > 0",
            name="ck_recommendation_preferences_default_candidate_limit_positive",
        ),
        sa.CheckConstraint(
            "length(btrim(question_display_mode)) > 0",
            name="ck_recommendation_preferences_question_display_mode_present",
        ),
        sa.CheckConstraint(
            "length(btrim(evidence_display_mode)) > 0",
            name="ck_recommendation_preferences_evidence_display_mode_present",
        ),
        sa.CheckConstraint(
            "lock_version >= 1", name="ck_recommendation_preferences_lock_version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_recommendation_preferences_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("owner_user_id", name="pk_recommendation_preferences"),
    )
    op.create_table(
        "project_recommendation_preferences",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_limit", sa.Integer(), nullable=True),
        sa.Column("question_display_mode", sa.String(length=64), nullable=True),
        sa.Column("evidence_display_mode", sa.String(length=64), nullable=True),
        sa.Column("show_information_completeness", sa.Boolean(), nullable=True),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "candidate_limit IS NULL OR candidate_limit > 0",
            name="ck_project_recommendation_preferences_candidate_limit_positive",
        ),
        sa.CheckConstraint(
            "question_display_mode IS NULL OR length(btrim(question_display_mode)) > 0",
            name="ck_project_recommendation_preferences_question_display_mode_present",
        ),
        sa.CheckConstraint(
            "evidence_display_mode IS NULL OR length(btrim(evidence_display_mode)) > 0",
            name="ck_project_recommendation_preferences_evidence_display_mode_present",
        ),
        sa.CheckConstraint(
            "lock_version >= 1", name="ck_project_recommendation_preferences_lock_version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "owner_user_id"],
            ["application_projects.id", "application_projects.owner_user_id"],
            name="fk_project_recommendation_preferences_project_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("project_id", name="pk_project_recommendation_preferences"),
    )
    op.create_table(
        "snapshot_recommendation_preferences",
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_limit", sa.Integer(), nullable=False),
        sa.Column("question_display_mode", sa.String(length=64), nullable=False),
        sa.Column("evidence_display_mode", sa.String(length=64), nullable=False),
        sa.Column("show_information_completeness", sa.Boolean(), nullable=False),
        sa.Column("user_preference_lock_version", sa.Integer(), nullable=False),
        sa.Column("project_preference_lock_version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "candidate_limit > 0",
            name="ck_snapshot_recommendation_preferences_candidate_limit_positive",
        ),
        sa.CheckConstraint(
            "length(btrim(question_display_mode)) > 0",
            name="ck_snapshot_recommendation_preferences_question_display_mode_present",
        ),
        sa.CheckConstraint(
            "length(btrim(evidence_display_mode)) > 0",
            name="ck_snapshot_recommendation_preferences_evidence_display_mode_present",
        ),
        sa.CheckConstraint(
            "user_preference_lock_version >= 1",
            name="ck_snapshot_recommendation_preferences_user_lock_version_positive",
        ),
        sa.CheckConstraint(
            "project_preference_lock_version IS NULL OR project_preference_lock_version >= 1",
            name="ck_snapshot_recommendation_preferences_project_lock_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "owner_user_id"],
            ["project_snapshots.id", "project_snapshots.owner_user_id"],
            name="fk_snapshot_recommendation_preferences_snapshot_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("snapshot_id", name="pk_snapshot_recommendation_preferences"),
    )
    op.create_table(
        "consents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("consent_type", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(consent_type)) > 0", name="ck_consents_consent_type_present"
        ),
        sa.CheckConstraint(
            "length(btrim(policy_version)) > 0", name="ck_consents_policy_version_present"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name="fk_consents_owner", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_consents"),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_consents_id_owner"),
    )
    op.create_index(
        "ix_consents_owner_type_decided",
        "consents",
        ["owner_user_id", "consent_type", sa.text("decided_at DESC"), sa.text("id DESC")],
    )
    op.create_table(
        "retention_preferences",
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("option_id", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(option_id)) > 0", name="ck_retention_preferences_option_id_present"
        ),
        sa.CheckConstraint(
            "length(btrim(policy_version)) > 0",
            name="ck_retention_preferences_policy_version_present",
        ),
        sa.CheckConstraint(
            "lock_version >= 1", name="ck_retention_preferences_lock_version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_retention_preferences_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("owner_user_id", name="pk_retention_preferences"),
    )

    op.create_unique_constraint(
        "uq_recommendation_candidates_id_run_owner",
        "recommendation_candidates",
        ["id", "run_id", "owner_user_id"],
    )
    op.create_table(
        "feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_helpfulness", sa.String(length=16), nullable=True),
        sa.Column("category_l1", sa.String(length=64), nullable=False),
        sa.Column("category_l2", sa.String(length=64), nullable=True),
        sa.Column("other_text", sa.Text(), nullable=True),
        sa.Column("missing_activity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("missing_episode_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision_helpfulness IS NULL OR "
            f"decision_helpfulness IN ({DECISION_HELPFULNESS_VALUES})",
            name="ck_feedback_decision_helpfulness_allowed",
        ),
        sa.CheckConstraint(
            "length(btrim(category_l1)) > 0", name="ck_feedback_category_l1_present"
        ),
        sa.CheckConstraint(
            "(missing_activity_id IS NOT NULL)::integer + "
            "(missing_episode_id IS NOT NULL)::integer <= 1",
            name="ck_feedback_at_most_one_missing_resource",
        ),
        sa.CheckConstraint(
            "other_text IS NULL OR octet_length(other_text) <= 4096",
            name="ck_feedback_other_text_max_4kib",
        ),
        sa.CheckConstraint(
            "candidate_id IS NULL OR run_id IS NOT NULL", name="ck_feedback_candidate_requires_run"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name="fk_feedback_owner", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "owner_user_id"],
            ["recommendation_runs.id", "recommendation_runs.owner_user_id"],
            name="fk_feedback_run_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "owner_user_id"],
            ["project_snapshots.id", "project_snapshots.owner_user_id"],
            name="fk_feedback_snapshot_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "run_id", "owner_user_id"],
            [
                "recommendation_candidates.id",
                "recommendation_candidates.run_id",
                "recommendation_candidates.owner_user_id",
            ],
            name="fk_feedback_candidate_run_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["missing_activity_id", "owner_user_id"],
            ["activities.id", "activities.owner_user_id"],
            name="fk_feedback_missing_activity_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["missing_episode_id", "owner_user_id"],
            ["episodes.id", "episodes.owner_user_id"],
            name="fk_feedback_missing_episode_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_feedback"),
    )
    op.execute(
        """
        CREATE FUNCTION validate_feedback_recommendation_chain()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.run_id IS NOT NULL AND NEW.snapshot_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM recommendation_runs
                WHERE id = NEW.run_id
                  AND owner_user_id = NEW.owner_user_id
                  AND snapshot_id = NEW.snapshot_id
            ) THEN
                RAISE EXCEPTION 'feedback run and snapshot are outside one recommendation chain'
                    USING ERRCODE = '23503';
            END IF;
            IF NEW.candidate_id IS NOT NULL AND NOT EXISTS (
                SELECT 1
                FROM recommendation_candidates AS candidate
                JOIN recommendation_runs AS run ON run.id = candidate.run_id
                   AND run.owner_user_id = candidate.owner_user_id
                WHERE candidate.id = NEW.candidate_id
                  AND candidate.run_id = NEW.run_id
                  AND candidate.owner_user_id = NEW.owner_user_id
                  AND (NEW.snapshot_id IS NULL OR run.snapshot_id = NEW.snapshot_id)
            ) THEN
                RAISE EXCEPTION 'feedback candidate is outside its recommendation chain'
                    USING ERRCODE = '23503';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_feedback_recommendation_chain
        BEFORE INSERT OR UPDATE ON feedback
        FOR EACH ROW EXECUTE FUNCTION validate_feedback_recommendation_chain();
        """
    )
    op.create_index(
        "ix_feedback_owner_created",
        "feedback",
        ["owner_user_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "analytics_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_key", sa.String(length=128), nullable=False),
        sa.Column("pseudonymous_subject_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("consent_policy_version", sa.String(length=64), nullable=False),
        sa.Column("allowed_properties", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(pseudonymous_subject_id)) > 0",
            name="ck_analytics_events_pseudonymous_subject_present",
        ),
        sa.CheckConstraint(
            "length(btrim(event_key)) > 0", name="ck_analytics_events_event_key_present"
        ),
        sa.CheckConstraint(
            "length(btrim(event_type)) > 0", name="ck_analytics_events_event_type_present"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(allowed_properties) = 'object'",
            name="ck_analytics_events_allowed_properties_object",
        ),
        sa.CheckConstraint(
            "octet_length(allowed_properties::text) <= 4096",
            name="ck_analytics_events_allowed_properties_max_4kib",
        ),
        sa.CheckConstraint(
            f"NOT (allowed_properties ?| {ANALYTICS_FORBIDDEN_PROPERTY_KEYS})",
            name="ck_analytics_events_no_sensitive_property_keys",
        ),
        sa.CheckConstraint(
            "owner_user_id IS NOT NULL OR "
            "(project_id IS NULL AND question_id IS NULL AND run_id IS NULL)",
            name="ck_analytics_events_anonymous_has_no_private_context",
        ),
        sa.CheckConstraint(
            "question_id IS NULL OR project_id IS NOT NULL",
            name="ck_analytics_events_question_requires_project",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name="fk_analytics_events_owner", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "owner_user_id"],
            ["application_projects.id", "application_projects.owner_user_id"],
            name="fk_analytics_events_project_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_id", "project_id", "owner_user_id"],
            [
                "project_questions.id",
                "project_questions.project_id",
                "project_questions.owner_user_id",
            ],
            name="fk_analytics_events_question_project_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "owner_user_id"],
            ["recommendation_runs.id", "recommendation_runs.owner_user_id"],
            name="fk_analytics_events_run_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_analytics_events"),
        sa.UniqueConstraint(
            "pseudonymous_subject_id", "event_key", name="uq_analytics_events_subject_event_key"
        ),
    )
    op.execute(
        """
        CREATE FUNCTION validate_analytics_event_consent()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            current_granted boolean;
            current_policy_version text;
        BEGIN
            IF NEW.owner_user_id IS NULL THEN
                RAISE EXCEPTION 'unattributable analytics ingestion is not enabled'
                    USING ERRCODE = '23514';
            END IF;
            SELECT granted, policy_version
            INTO current_granted, current_policy_version
            FROM consents
            WHERE owner_user_id = NEW.owner_user_id AND consent_type = 'ANALYTICS'
            ORDER BY decided_at DESC, id DESC
            LIMIT 1;
            IF current_granted IS DISTINCT FROM true
               OR NEW.consent_policy_version IS DISTINCT FROM current_policy_version THEN
                RAISE EXCEPTION 'analytics event requires current analytics opt-in'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_analytics_events_consent
        BEFORE INSERT OR UPDATE ON analytics_events
        FOR EACH ROW EXECUTE FUNCTION validate_analytics_event_consent();
        """
    )
    op.create_index(
        "ix_analytics_events_owner_occurred",
        "analytics_events",
        ["owner_user_id", sa.text("occurred_at DESC")],
    )

    for table_name in (
        "user_settings",
        "recommendation_preferences",
        "project_recommendation_preferences",
        "snapshot_recommendation_preferences",
        "consents",
        "retention_preferences",
        "feedback",
        "analytics_events",
    ):
        _enable_owner_rls(table_name, "owner_user_id")
    for table_name in (
        "snapshot_recommendation_preferences",
        "consents",
        "feedback",
        "analytics_events",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_no_update BEFORE UPDATE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()"
        )


def downgrade() -> None:
    for table_name in (
        "analytics_events",
        "feedback",
        "consents",
        "snapshot_recommendation_preferences",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_no_update ON {table_name}")
    for table_name in (
        "analytics_events",
        "feedback",
        "retention_preferences",
        "consents",
        "snapshot_recommendation_preferences",
        "project_recommendation_preferences",
        "recommendation_preferences",
        "user_settings",
    ):
        op.execute(f"DROP POLICY IF EXISTS {table_name}_owner_policy ON {table_name}")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TRIGGER IF EXISTS trg_analytics_events_consent ON analytics_events")
    op.execute("DROP FUNCTION IF EXISTS validate_analytics_event_consent()")
    op.drop_index("ix_analytics_events_owner_occurred", table_name="analytics_events")
    op.drop_table("analytics_events")
    op.execute("DROP TRIGGER IF EXISTS trg_feedback_recommendation_chain ON feedback")
    op.execute("DROP FUNCTION IF EXISTS validate_feedback_recommendation_chain()")
    op.drop_index("ix_feedback_owner_created", table_name="feedback")
    op.drop_table("feedback")
    op.drop_constraint(
        "uq_recommendation_candidates_id_run_owner",
        "recommendation_candidates",
        type_="unique",
    )
    op.drop_table("retention_preferences")
    op.drop_index("ix_consents_owner_type_decided", table_name="consents")
    op.drop_table("consents")
    op.drop_table("snapshot_recommendation_preferences")
    op.drop_table("project_recommendation_preferences")
    op.drop_table("recommendation_preferences")
    op.drop_table("user_settings")


def _enable_owner_rls(table_name: str, owner_column: str) -> None:
    expression = f"{owner_column} = NULLIF(current_setting('app.current_user_id', true), '')::uuid"
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table_name}_owner_policy ON {table_name} "
        f"USING ({expression}) WITH CHECK ({expression})"
    )
