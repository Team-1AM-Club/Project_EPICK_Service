"""Add question-analysis provenance and fixed snapshot evidence.

Revision ID: 010_question_analysis_snapshot_evidence
Revises: 009_claims_interpretations
Create Date: 2026-09-15
"""

# ruff: noqa: E501

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "010_question_analysis"
down_revision = "009_claims_interpretations"
branch_labels = None
depends_on = None

ANALYSIS_STATUS_VALUES = "'SUCCEEDED', 'PARTIAL', 'FAILED', 'BLOCKED'"
MODEL_EXECUTION_STATUS_VALUES = "'SUCCEEDED', 'PARTIAL', 'FAILED', 'BLOCKED'"


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_question_versions_id_question_project_owner",
        "question_versions",
        ["id", "question_id", "project_id", "owner_user_id"],
    )
    op.create_table(
        "question_intent_types",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_question_intent_types_code"),
    )
    op.create_table(
        "question_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_posting_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("analysis_input_version", sa.String(length=64), nullable=False),
        sa.Column("analysis_revision", sa.Integer(), nullable=False),
        sa.Column("result_status", sa.String(length=16), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("safe_failure_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("analysis_revision >= 1", name="analysis_revision_positive"),
        sa.CheckConstraint(
            f"result_status IN ({ANALYSIS_STATUS_VALUES})", name="result_status_allowed"
        ),
        sa.CheckConstraint(
            "safe_failure_message IS NULL OR octet_length(safe_failure_message) <= 1024",
            name="safe_failure_message_bounded",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "owner_user_id"],
            ["application_projects.id", "application_projects.owner_user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_version_id", "project_id", "owner_user_id", "company_id"],
            [
                "application_project_versions.id",
                "application_project_versions.project_id",
                "application_project_versions.owner_user_id",
                "application_project_versions.company_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_id", "project_id", "owner_user_id"],
            [
                "project_questions.id",
                "project_questions.project_id",
                "project_questions.owner_user_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_version_id", "question_id", "project_id", "owner_user_id"],
            [
                "question_versions.id",
                "question_versions.question_id",
                "question_versions.project_id",
                "question_versions.owner_user_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_posting_version_id", "company_id"],
            ["job_posting_versions.id", "job_posting_versions.company_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "question_version_id",
            "analysis_input_version",
            "analysis_revision",
            name="uq_question_analyses_question_version_input_revision",
        ),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_question_analyses_id_owner_user_id"),
        sa.UniqueConstraint(
            "id",
            "question_id",
            "question_version_id",
            "owner_user_id",
            name="uq_question_analyses_id_question_version_owner",
        ),
        sa.UniqueConstraint(
            "id",
            "project_id",
            "project_version_id",
            "owner_user_id",
            name="uq_question_analyses_id_project_version_owner",
        ),
    )
    op.create_table(
        "question_analysis_intents",
        sa.Column("question_analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_intent_type_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.CheckConstraint("rank >= 1", name="rank_positive"),
        sa.ForeignKeyConstraint(
            ["question_analysis_id", "owner_user_id"],
            ["question_analyses.id", "question_analyses.owner_user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_intent_type_id"], ["question_intent_types.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("question_analysis_id", "question_intent_type_id"),
    )
    op.create_index(
        "uq_question_analysis_intents_primary",
        "question_analysis_intents",
        ["question_analysis_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )
    op.create_table(
        "question_analysis_requirement_groups",
        sa.Column("question_analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requirement_group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["question_analysis_id", "owner_user_id"],
            ["question_analyses.id", "question_analyses.owner_user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["requirement_group_id"], ["requirement_groups.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("question_analysis_id", "requirement_group_id"),
    )
    op.create_table(
        "question_analysis_requirements",
        sa.Column("question_analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requirement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["question_analysis_id", "owner_user_id"],
            ["question_analyses.id", "question_analyses.owner_user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["requirement_id"], ["requirements.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("question_analysis_id", "requirement_id"),
    )

    op.add_column(
        "project_snapshots",
        sa.Column("job_posting_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_project_snapshots_job_posting_version",
        "project_snapshots",
        "job_posting_versions",
        ["job_posting_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_project_snapshots_id_project_version_owner",
        "project_snapshots",
        ["id", "project_id", "project_version_id", "owner_user_id"],
    )
    op.create_table(
        "snapshot_activity_versions",
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("activity_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "owner_user_id"],
            ["project_snapshots.id", "project_snapshots.owner_user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["activity_version_id", "owner_user_id"],
            ["activity_versions.id", "activity_versions.owner_user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "activity_version_id"),
    )
    op.create_table(
        "snapshot_source_versions",
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "owner_user_id"],
            ["project_snapshots.id", "project_snapshots.owner_user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id", "source_id", "company_id"],
            ["source_versions.id", "source_versions.source_id", "source_versions.company_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "source_version_id"),
    )
    op.create_table(
        "snapshot_question_analyses",
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "project_id", "project_version_id", "owner_user_id"],
            [
                "project_snapshots.id",
                "project_snapshots.project_id",
                "project_snapshots.project_version_id",
                "project_snapshots.owner_user_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_analysis_id", "project_id", "project_version_id", "owner_user_id"],
            [
                "question_analyses.id",
                "question_analyses.project_id",
                "question_analyses.project_version_id",
                "question_analyses.owner_user_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "question_analysis_id"),
    )
    op.add_column(
        "recommendation_runs",
        sa.Column("question_analysis_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_recommendation_runs_question_analysis_scope",
        "recommendation_runs",
        "question_analyses",
        ["question_analysis_id", "question_id", "question_version_id", "owner_user_id"],
        ["id", "question_id", "question_version_id", "owner_user_id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_recommendation_runs_id_owner_user_id",
        "recommendation_runs",
        ["id", "owner_user_id"],
    )
    op.create_table(
        "model_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_analysis_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("recommendation_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("execution_kind", sa.String(length=32), nullable=False),
        sa.Column("model_provider", sa.String(length=64), nullable=False),
        sa.Column("model_identifier", sa.String(length=128), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("result_status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("safe_failure_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(question_analysis_id IS NOT NULL)::integer + (recommendation_run_id IS NOT NULL)::integer = 1",
            name="exactly_one_parent",
        ),
        sa.CheckConstraint(
            "(execution_kind = 'QUESTION_ANALYSIS' AND question_analysis_id IS NOT NULL) OR (execution_kind = 'RECOMMENDATION' AND recommendation_run_id IS NOT NULL)",
            name="execution_kind_parent_matches",
        ),
        sa.CheckConstraint(
            f"result_status IN ({MODEL_EXECUTION_STATUS_VALUES})", name="result_status_allowed"
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at", name="completed_after_started"
        ),
        sa.CheckConstraint(
            "safe_failure_message IS NULL OR octet_length(safe_failure_message) <= 1024",
            name="safe_failure_message_bounded",
        ),
        sa.ForeignKeyConstraint(
            ["question_analysis_id", "owner_user_id"],
            ["question_analyses.id", "question_analyses.owner_user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recommendation_run_id", "owner_user_id"],
            ["recommendation_runs.id", "recommendation_runs.owner_user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_model_executions_owner_started",
        "model_executions",
        ["owner_user_id", sa.text("started_at DESC")],
    )

    op.execute(
        """
        CREATE FUNCTION validate_question_analysis_posting_scope()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.job_posting_version_id IS NULL THEN
                RETURN NEW;
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM application_project_versions AS project_version
                JOIN job_posting_versions AS posting_version
                  ON posting_version.id = NEW.job_posting_version_id
                WHERE project_version.id = NEW.project_version_id
                  AND project_version.project_id = NEW.project_id
                  AND project_version.owner_user_id = NEW.owner_user_id
                  AND project_version.company_id = NEW.company_id
                  AND posting_version.company_id = NEW.company_id
                  AND project_version.job_posting_id = posting_version.posting_id
            ) THEN
                RAISE EXCEPTION 'question analysis posting version is outside its project-version scope' USING ERRCODE = '23503';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION validate_question_analysis_requirement_group_scope()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM question_analyses AS analysis
                JOIN requirement_groups AS requirement_group ON requirement_group.id = NEW.requirement_group_id
                JOIN job_posting_versions AS posting_version ON posting_version.id = requirement_group.job_posting_version_id
                WHERE analysis.id = NEW.question_analysis_id
                  AND analysis.owner_user_id = NEW.owner_user_id
                  AND analysis.company_id = posting_version.company_id
                  AND (analysis.job_posting_version_id IS NULL OR analysis.job_posting_version_id = posting_version.id)
            ) THEN
                RAISE EXCEPTION 'question analysis requirement group is outside its posting scope' USING ERRCODE = '23503';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION validate_question_analysis_requirement_scope()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM question_analyses AS analysis
                JOIN requirements AS requirement ON requirement.id = NEW.requirement_id
                JOIN requirement_groups AS requirement_group ON requirement_group.id = requirement.group_id
                JOIN job_posting_versions AS posting_version ON posting_version.id = requirement_group.job_posting_version_id
                WHERE analysis.id = NEW.question_analysis_id
                  AND analysis.owner_user_id = NEW.owner_user_id
                  AND analysis.company_id = posting_version.company_id
                  AND (analysis.job_posting_version_id IS NULL OR analysis.job_posting_version_id = posting_version.id)
            ) THEN
                RAISE EXCEPTION 'question analysis requirement is outside its posting scope' USING ERRCODE = '23503';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION validate_snapshot_posting_scope()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.job_posting_version_id IS NULL THEN
                RETURN NEW;
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM application_project_versions AS project_version
                JOIN job_posting_versions AS posting_version
                  ON posting_version.id = NEW.job_posting_version_id
                WHERE project_version.id = NEW.project_version_id
                  AND project_version.project_id = NEW.project_id
                  AND project_version.owner_user_id = NEW.owner_user_id
                  AND project_version.company_id = posting_version.company_id
                  AND project_version.job_posting_id = posting_version.posting_id
            ) THEN
                RAISE EXCEPTION 'snapshot posting version is outside its project-version scope' USING ERRCODE = '23503';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE FUNCTION validate_snapshot_source_company_scope()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM project_snapshots AS snapshot
                JOIN application_project_versions AS project_version ON project_version.id = snapshot.project_version_id
                JOIN source_versions AS source_version ON source_version.id = NEW.source_version_id
                WHERE snapshot.id = NEW.snapshot_id
                  AND snapshot.owner_user_id = NEW.owner_user_id
                  AND project_version.company_id = NEW.company_id
                  AND source_version.source_id = NEW.source_id
                  AND source_version.company_id = NEW.company_id
            ) THEN
                RAISE EXCEPTION 'snapshot source version is outside the snapshot company scope' USING ERRCODE = '23503';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE CONSTRAINT TRIGGER trg_question_analyses_posting_scope
        AFTER INSERT OR UPDATE OF project_version_id, job_posting_version_id ON question_analyses
        DEFERRABLE INITIALLY IMMEDIATE FOR EACH ROW EXECUTE FUNCTION validate_question_analysis_posting_scope();
        CREATE CONSTRAINT TRIGGER trg_question_analysis_requirement_groups_scope
        AFTER INSERT ON question_analysis_requirement_groups
        DEFERRABLE INITIALLY IMMEDIATE FOR EACH ROW EXECUTE FUNCTION validate_question_analysis_requirement_group_scope();
        CREATE CONSTRAINT TRIGGER trg_question_analysis_requirements_scope
        AFTER INSERT ON question_analysis_requirements
        DEFERRABLE INITIALLY IMMEDIATE FOR EACH ROW EXECUTE FUNCTION validate_question_analysis_requirement_scope();
        CREATE CONSTRAINT TRIGGER trg_project_snapshots_posting_scope
        AFTER INSERT OR UPDATE OF project_version_id, job_posting_version_id ON project_snapshots
        DEFERRABLE INITIALLY IMMEDIATE FOR EACH ROW EXECUTE FUNCTION validate_snapshot_posting_scope();
        CREATE CONSTRAINT TRIGGER trg_snapshot_source_versions_company_scope
        AFTER INSERT ON snapshot_source_versions
        DEFERRABLE INITIALLY IMMEDIATE FOR EACH ROW EXECUTE FUNCTION validate_snapshot_source_company_scope();
        """
    )
    for table_name in (
        "question_analyses",
        "question_analysis_intents",
        "question_analysis_requirement_groups",
        "question_analysis_requirements",
        "snapshot_activity_versions",
        "snapshot_source_versions",
        "snapshot_question_analyses",
        "model_executions",
    ):
        _enable_owner_rls(table_name, "owner_user_id")


def downgrade() -> None:
    for table_name in (
        "question_analyses",
        "question_analysis_intents",
        "question_analysis_requirement_groups",
        "question_analysis_requirements",
        "snapshot_activity_versions",
        "snapshot_source_versions",
        "snapshot_question_analyses",
        "model_executions",
    ):
        op.execute(f"DROP POLICY IF EXISTS {table_name}_owner_policy ON {table_name}")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_snapshot_source_versions_company_scope ON snapshot_source_versions"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_project_snapshots_posting_scope ON project_snapshots")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_question_analysis_requirements_scope ON question_analysis_requirements"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_question_analysis_requirement_groups_scope ON question_analysis_requirement_groups"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_question_analyses_posting_scope ON question_analyses")
    op.execute("DROP FUNCTION IF EXISTS validate_snapshot_source_company_scope()")
    op.execute("DROP FUNCTION IF EXISTS validate_snapshot_posting_scope()")
    op.execute("DROP FUNCTION IF EXISTS validate_question_analysis_requirement_scope()")
    op.execute("DROP FUNCTION IF EXISTS validate_question_analysis_requirement_group_scope()")
    op.execute("DROP FUNCTION IF EXISTS validate_question_analysis_posting_scope()")
    op.drop_index("ix_model_executions_owner_started", table_name="model_executions")
    op.drop_table("model_executions")
    op.drop_constraint(
        "uq_recommendation_runs_id_owner_user_id",
        "recommendation_runs",
        type_="unique",
    )
    op.drop_constraint(
        "fk_recommendation_runs_question_analysis_scope", "recommendation_runs", type_="foreignkey"
    )
    op.drop_column("recommendation_runs", "question_analysis_id")
    op.drop_table("snapshot_question_analyses")
    op.drop_table("snapshot_source_versions")
    op.drop_table("snapshot_activity_versions")
    op.drop_constraint(
        "uq_project_snapshots_id_project_version_owner", "project_snapshots", type_="unique"
    )
    op.drop_constraint(
        "fk_project_snapshots_job_posting_version", "project_snapshots", type_="foreignkey"
    )
    op.drop_column("project_snapshots", "job_posting_version_id")
    op.drop_index("uq_question_analysis_intents_primary", table_name="question_analysis_intents")
    op.drop_table("question_analysis_requirements")
    op.drop_table("question_analysis_requirement_groups")
    op.drop_table("question_analysis_intents")
    op.drop_table("question_analyses")
    op.drop_constraint(
        "uq_question_versions_id_question_project_owner",
        "question_versions",
        type_="unique",
    )
    op.drop_table("question_intent_types")


def _enable_owner_rls(table_name: str, owner_column: str) -> None:
    expression = f"{owner_column} = NULLIF(current_setting('app.current_user_id', true), '')::uuid"
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table_name}_owner_policy ON {table_name} "
        f"USING ({expression}) WITH CHECK ({expression})"
    )
