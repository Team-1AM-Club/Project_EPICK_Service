"""Add location-only sensitivity findings and external-processing decisions.

Revision ID: 016_sensitivity_privacy
Revises: 015_user_settings_feedback
Create Date: 2026-09-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "016_sensitivity_privacy"
down_revision = "015_user_settings_feedback"
branch_labels = None
depends_on = None

ASSESSMENT_STATUS_VALUES = "'PENDING', 'REVIEW_REQUIRED', 'DECIDED', 'FAILED'"
SENSITIVITY_DECISION_VALUES = "'KEEP', 'REDACT_BEFORE_EXTERNAL', 'EXCLUDE_FROM_EXTERNAL', 'DELETE'"


def upgrade() -> None:
    op.create_table(
        "sensitivity_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("activity_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("episode_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("detector_version", sa.String(length=64), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default=sa.text("'PENDING'"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(activity_version_id IS NOT NULL)::integer + "
            "(episode_version_id IS NOT NULL)::integer = 1",
            name="ck_sensitivity_assessments_exactly_one_version",
        ),
        sa.CheckConstraint(
            "length(btrim(detector_version)) > 0",
            name="ck_sensitivity_assessments_detector_version_present",
        ),
        sa.CheckConstraint(
            f"status IN ({ASSESSMENT_STATUS_VALUES})",
            name="ck_sensitivity_assessments_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["activity_version_id", "owner_user_id"],
            ["activity_versions.id", "activity_versions.owner_user_id"],
            name="fk_sensitivity_assessments_activity_version_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["episode_version_id", "owner_user_id"],
            ["episode_versions.id", "episode_versions.owner_user_id"],
            name="fk_sensitivity_assessments_episode_version_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sensitivity_assessments"),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_sensitivity_assessments_id_owner"),
    )
    op.create_index(
        "ix_sensitivity_assessments_owner_activity_created",
        "sensitivity_assessments",
        ["owner_user_id", "activity_version_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("activity_version_id IS NOT NULL"),
    )
    op.create_index(
        "ix_sensitivity_assessments_owner_episode_created",
        "sensitivity_assessments",
        ["owner_user_id", "episode_version_id", sa.text("created_at DESC")],
        postgresql_where=sa.text("episode_version_id IS NOT NULL"),
    )
    op.create_table(
        "sensitivity_findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("field_name", sa.String(length=128), nullable=False),
        sa.Column("source_span_start", sa.BigInteger(), nullable=True),
        sa.Column("source_span_end", sa.BigInteger(), nullable=True),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(category)) > 0", name="ck_sensitivity_findings_category_present"
        ),
        sa.CheckConstraint(
            "length(btrim(field_name)) > 0", name="ck_sensitivity_findings_field_name_present"
        ),
        sa.CheckConstraint(
            "length(btrim(severity)) > 0", name="ck_sensitivity_findings_severity_present"
        ),
        sa.CheckConstraint(
            "source_span_start IS NULL OR source_span_start >= 0",
            name="ck_sensitivity_findings_source_span_start_not_negative",
        ),
        sa.CheckConstraint(
            "source_span_end IS NULL OR source_span_end >= 0",
            name="ck_sensitivity_findings_source_span_end_not_negative",
        ),
        sa.CheckConstraint(
            "source_span_start IS NULL OR source_span_end IS NULL "
            "OR source_span_end >= source_span_start",
            name="ck_sensitivity_findings_source_span_ordered",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id", "owner_user_id"],
            ["sensitivity_assessments.id", "sensitivity_assessments.owner_user_id"],
            name="fk_sensitivity_findings_assessment_owner_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sensitivity_findings"),
    )
    op.create_index("ix_sensitivity_findings_assessment", "sensitivity_findings", ["assessment_id"])
    op.create_table(
        "sensitivity_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_no", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision_no >= 1", name="ck_sensitivity_decisions_decision_no_positive"
        ),
        sa.CheckConstraint(
            f"decision IN ({SENSITIVITY_DECISION_VALUES})",
            name="ck_sensitivity_decisions_decision_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["assessment_id", "owner_user_id"],
            ["sensitivity_assessments.id", "sensitivity_assessments.owner_user_id"],
            name="fk_sensitivity_decisions_assessment_owner_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sensitivity_decisions"),
        sa.UniqueConstraint(
            "assessment_id", "decision_no", name="uq_sensitivity_decisions_assessment_decision_no"
        ),
    )
    op.execute(
        """
        CREATE FUNCTION reject_sensitivity_finding_value_copy()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF to_jsonb(NEW) ?| ARRAY[
                'excerpt', 'matched_value', 'original_text', 'prompt', 'response'
            ] THEN
                RAISE EXCEPTION 'sensitivity findings must not carry copied source text'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_sensitivity_findings_no_value_copy
        BEFORE INSERT OR UPDATE ON sensitivity_findings
        FOR EACH ROW EXECUTE FUNCTION reject_sensitivity_finding_value_copy();
        """
    )
    for table_name in (
        "sensitivity_assessments",
        "sensitivity_findings",
        "sensitivity_decisions",
    ):
        _enable_owner_rls(table_name, "owner_user_id")
    for table_name in ("sensitivity_findings", "sensitivity_decisions"):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_no_update BEFORE UPDATE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()"
        )


def downgrade() -> None:
    for table_name in ("sensitivity_decisions", "sensitivity_findings"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_no_update ON {table_name}")
    for table_name in (
        "sensitivity_decisions",
        "sensitivity_findings",
        "sensitivity_assessments",
    ):
        op.execute(f"DROP POLICY IF EXISTS {table_name}_owner_policy ON {table_name}")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_sensitivity_findings_no_value_copy ON sensitivity_findings"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_sensitivity_finding_value_copy()")
    op.drop_table("sensitivity_decisions")
    op.drop_index("ix_sensitivity_findings_assessment", table_name="sensitivity_findings")
    op.drop_table("sensitivity_findings")
    op.drop_index(
        "ix_sensitivity_assessments_owner_episode_created", table_name="sensitivity_assessments"
    )
    op.drop_index(
        "ix_sensitivity_assessments_owner_activity_created", table_name="sensitivity_assessments"
    )
    op.drop_table("sensitivity_assessments")


def _enable_owner_rls(table_name: str, owner_column: str) -> None:
    expression = f"{owner_column} = NULLIF(current_setting('app.current_user_id', true), '')::uuid"
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table_name}_owner_policy ON {table_name} "
        f"USING ({expression}) WITH CHECK ({expression})"
    )
