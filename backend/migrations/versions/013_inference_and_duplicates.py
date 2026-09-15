"""Add approval-gated inference and duplicate-merge audit records.

Revision ID: 013_inference_duplicates
Revises: 012_projection_readiness
Create Date: 2026-09-15

Suggestions and decisions are immutable audit rows.  They never overwrite an Experience Version;
an explicit MERGE decision is the only path that may record a derived Episode Version.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "013_inference_duplicates"
down_revision = "012_projection_readiness"
branch_labels = None
depends_on = None

INFERENCE_SUGGESTION_STATUS_VALUES = "'PENDING_DECISION', 'DECIDED', 'SUPERSEDED'"
INFERENCE_DECISION_VALUES = "'APPROVED', 'MODIFIED', 'REJECTED'"
DUPLICATE_SUGGESTION_STATUS_VALUES = "'PENDING_DECISION', 'DECIDED', 'SUPERSEDED'"
DUPLICATE_DECISION_VALUES = "'MERGE', 'KEEP_SEPARATE', 'DISMISSED'"


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_episode_versions_id_episode_owner",
        "episode_versions",
        ["id", "episode_id", "owner_user_id"],
    )
    op.create_table(
        "inference_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("episode_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("episode_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suggestion_type", sa.String(length=64), nullable=False),
        sa.Column("proposed_value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_policy_version", sa.String(length=64), nullable=False),
        sa.Column("model_execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'PENDING_DECISION'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"status IN ({INFERENCE_SUGGESTION_STATUS_VALUES})",
            name="ck_inference_suggestions_status_allowed",
        ),
        sa.CheckConstraint(
            "length(btrim(suggestion_type)) > 0",
            name="ck_inference_suggestions_suggestion_type_present",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(proposed_value) = 'object'",
            name="ck_inference_suggestions_proposed_value_object",
        ),
        sa.CheckConstraint(
            "octet_length(proposed_value::text) <= 16384",
            name="ck_inference_suggestions_proposed_value_max_16kib",
        ),
        sa.ForeignKeyConstraint(
            ["episode_version_id", "episode_id", "owner_user_id"],
            [
                "episode_versions.id",
                "episode_versions.episode_id",
                "episode_versions.owner_user_id",
            ],
            name="fk_inference_suggestions_episode_version_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_execution_id"],
            ["model_executions.id"],
            name="fk_inference_suggestions_model_execution",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_inference_suggestions"),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_inference_suggestions_id_owner"),
    )
    op.create_index(
        "ix_inference_suggestions_owner_status",
        "inference_suggestions",
        ["owner_user_id", "status", sa.text("created_at DESC")],
    )
    op.create_table(
        "inference_suggestion_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suggestion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("episode_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("evidence_span_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("field_name", sa.String(length=128), nullable=True),
        sa.Column("source_span_start", sa.BigInteger(), nullable=True),
        sa.Column("source_span_end", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(episode_version_id IS NOT NULL)::integer + (source_version_id IS NOT NULL)::integer "
            "+ (evidence_span_id IS NOT NULL)::integer = 1",
            name="ck_inference_suggestion_sources_exactly_one_evidence",
        ),
        sa.CheckConstraint(
            "source_span_start IS NULL OR source_span_start >= 0",
            name="ck_inference_suggestion_sources_source_span_start_not_negative",
        ),
        sa.CheckConstraint(
            "source_span_end IS NULL OR source_span_end >= 0",
            name="ck_inference_suggestion_sources_source_span_end_not_negative",
        ),
        sa.CheckConstraint(
            "source_span_start IS NULL OR source_span_end IS NULL "
            "OR source_span_end >= source_span_start",
            name="ck_inference_suggestion_sources_source_span_ordered",
        ),
        sa.ForeignKeyConstraint(
            ["suggestion_id", "owner_user_id"],
            ["inference_suggestions.id", "inference_suggestions.owner_user_id"],
            name="fk_inference_suggestion_sources_suggestion_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["episode_version_id", "owner_user_id"],
            ["episode_versions.id", "episode_versions.owner_user_id"],
            name="fk_inference_suggestion_sources_episode_version_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["source_versions.id"],
            name="fk_inference_suggestion_sources_source_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_span_id"],
            ["evidence_spans.id"],
            name="fk_inference_suggestion_sources_evidence_span",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_inference_suggestion_sources"),
    )
    op.create_table(
        "inference_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suggestion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_no", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("modified_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("decision_no >= 1", name="ck_inference_decisions_decision_no_positive"),
        sa.CheckConstraint(
            f"decision IN ({INFERENCE_DECISION_VALUES})",
            name="ck_inference_decisions_decision_allowed",
        ),
        sa.CheckConstraint(
            "modified_value IS NULL OR jsonb_typeof(modified_value) = 'object'",
            name="ck_inference_decisions_modified_value_object",
        ),
        sa.CheckConstraint(
            "modified_value IS NULL OR octet_length(modified_value::text) <= 16384",
            name="ck_inference_decisions_modified_value_max_16kib",
        ),
        sa.CheckConstraint(
            "(decision = 'MODIFIED' AND modified_value IS NOT NULL) OR "
            "(decision <> 'MODIFIED' AND modified_value IS NULL)",
            name="ck_inference_decisions_modified_value_matches_decision",
        ),
        sa.ForeignKeyConstraint(
            ["suggestion_id", "owner_user_id"],
            ["inference_suggestions.id", "inference_suggestions.owner_user_id"],
            name="fk_inference_decisions_suggestion_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_inference_decisions"),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_inference_decisions_id_owner"),
        sa.UniqueConstraint(
            "suggestion_id", "decision_no", name="uq_inference_decisions_suggestion_decision_no"
        ),
    )
    op.add_column(
        "experience_field_provenance",
        sa.Column("inference_decision_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.drop_constraint(
        "ck_experience_field_provenance_origin_source_scope",
        "experience_field_provenance",
        type_="check",
    )
    op.create_check_constraint(
        "ck_experience_field_provenance_origin_scope",
        "experience_field_provenance",
        "(origin = 'USER_INPUT' AND source_version_id IS NULL AND inference_decision_id IS NULL) "
        "OR (origin = 'EXTERNAL_SOURCE' AND source_version_id IS NOT NULL "
        "AND inference_decision_id IS NULL) "
        "OR (origin = 'INFERENCE_DECISION' AND source_version_id IS NULL "
        "AND inference_decision_id IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_experience_provenance_inference_decision_owner",
        "experience_field_provenance",
        "inference_decisions",
        ["inference_decision_id", "owner_user_id"],
        ["id", "owner_user_id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "experience_duplicate_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("left_episode_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("left_episode_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("right_episode_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("right_episode_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("model_execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'PENDING_DECISION'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "left_episode_id < right_episode_id",
            name="ck_experience_duplicate_suggestions_episode_pair_canonical_order",
        ),
        sa.CheckConstraint(
            "left_episode_version_id <> right_episode_version_id",
            name="ck_experience_duplicate_suggestions_version_pair_distinct",
        ),
        sa.CheckConstraint(
            "left_episode_id <> right_episode_id",
            name="ck_experience_duplicate_suggestions_episode_pair_distinct",
        ),
        sa.CheckConstraint(
            f"status IN ({DUPLICATE_SUGGESTION_STATUS_VALUES})",
            name="ck_experience_duplicate_suggestions_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["left_episode_version_id", "left_episode_id", "owner_user_id"],
            [
                "episode_versions.id",
                "episode_versions.episode_id",
                "episode_versions.owner_user_id",
            ],
            name="fk_duplicate_suggestions_left_episode_version_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["right_episode_version_id", "right_episode_id", "owner_user_id"],
            [
                "episode_versions.id",
                "episode_versions.episode_id",
                "episode_versions.owner_user_id",
            ],
            name="fk_duplicate_suggestions_right_episode_version_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_execution_id"],
            ["model_executions.id"],
            name="fk_duplicate_suggestions_model_execution",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_experience_duplicate_suggestions"),
        sa.UniqueConstraint(
            "id", "owner_user_id", name="uq_experience_duplicate_suggestions_id_owner"
        ),
        sa.UniqueConstraint(
            "owner_user_id",
            "left_episode_id",
            "left_episode_version_id",
            "right_episode_id",
            "right_episode_version_id",
            name="uq_experience_duplicate_suggestions_owner_episode_version_pair",
        ),
    )
    op.execute(
        """
        CREATE FUNCTION validate_duplicate_suggestion_activity_scope()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            left_activity_id uuid;
            right_activity_id uuid;
        BEGIN
            SELECT activity_id INTO left_activity_id
            FROM episode_versions
            WHERE id = NEW.left_episode_version_id
              AND episode_id = NEW.left_episode_id
              AND owner_user_id = NEW.owner_user_id;
            SELECT activity_id INTO right_activity_id
            FROM episode_versions
            WHERE id = NEW.right_episode_version_id
              AND episode_id = NEW.right_episode_id
              AND owner_user_id = NEW.owner_user_id;
            IF left_activity_id IS NULL OR right_activity_id IS NULL
               OR left_activity_id <> right_activity_id THEN
                RAISE EXCEPTION 'duplicate suggestion episodes must belong to one activity'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_duplicate_suggestions_activity_scope
        BEFORE INSERT OR UPDATE ON experience_duplicate_suggestions
        FOR EACH ROW EXECUTE FUNCTION validate_duplicate_suggestion_activity_scope();
        """
    )
    op.create_table(
        "experience_duplicate_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suggestion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_no", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision_no >= 1", name="ck_experience_duplicate_decisions_decision_no_positive"
        ),
        sa.CheckConstraint(
            f"decision IN ({DUPLICATE_DECISION_VALUES})",
            name="ck_experience_duplicate_decisions_decision_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["suggestion_id", "owner_user_id"],
            [
                "experience_duplicate_suggestions.id",
                "experience_duplicate_suggestions.owner_user_id",
            ],
            name="fk_duplicate_decisions_suggestion_owner",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_experience_duplicate_decisions"),
        sa.UniqueConstraint(
            "id", "owner_user_id", name="uq_experience_duplicate_decisions_id_owner"
        ),
        sa.UniqueConstraint(
            "suggestion_id",
            "decision_no",
            name="uq_experience_duplicate_decisions_suggestion_decision_no",
        ),
    )
    op.create_table(
        "experience_merge_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_episode_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_episode_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_episode_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_episode_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("result_episode_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_episode_id <> target_episode_id",
            name="ck_experience_merge_records_episode_distinct",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id", "owner_user_id"],
            ["experience_duplicate_decisions.id", "experience_duplicate_decisions.owner_user_id"],
            name="fk_merge_records_decision_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_episode_version_id", "source_episode_id", "owner_user_id"],
            [
                "episode_versions.id",
                "episode_versions.episode_id",
                "episode_versions.owner_user_id",
            ],
            name="fk_merge_records_source_episode_version_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_episode_version_id", "target_episode_id", "owner_user_id"],
            [
                "episode_versions.id",
                "episode_versions.episode_id",
                "episode_versions.owner_user_id",
            ],
            name="fk_merge_records_target_episode_version_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["result_episode_version_id", "target_episode_id", "owner_user_id"],
            [
                "episode_versions.id",
                "episode_versions.episode_id",
                "episode_versions.owner_user_id",
            ],
            name="fk_merge_records_result_episode_version_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_experience_merge_records"),
        sa.UniqueConstraint("decision_id", name="uq_experience_merge_records_decision_id"),
    )
    op.execute(
        """
        CREATE FUNCTION validate_merge_record_decision_scope()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM experience_duplicate_decisions AS decision
                JOIN experience_duplicate_suggestions AS suggestion
                  ON suggestion.id = decision.suggestion_id
                 AND suggestion.owner_user_id = decision.owner_user_id
                WHERE decision.id = NEW.decision_id
                  AND decision.owner_user_id = NEW.owner_user_id
                  AND decision.decision = 'MERGE'
                  AND decision.decision_no = (
                    SELECT max(latest.decision_no)
                    FROM experience_duplicate_decisions AS latest
                    WHERE latest.suggestion_id = decision.suggestion_id
                  )
                  AND (
                    (suggestion.left_episode_id = NEW.source_episode_id
                     AND suggestion.left_episode_version_id = NEW.source_episode_version_id
                     AND suggestion.right_episode_id = NEW.target_episode_id
                     AND suggestion.right_episode_version_id = NEW.target_episode_version_id)
                    OR
                    (suggestion.right_episode_id = NEW.source_episode_id
                     AND suggestion.right_episode_version_id = NEW.source_episode_version_id
                     AND suggestion.left_episode_id = NEW.target_episode_id
                     AND suggestion.left_episode_version_id = NEW.target_episode_version_id)
                  )
            ) THEN
                RAISE EXCEPTION 'merge record must match an approved duplicate merge decision'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_merge_records_decision_scope
        BEFORE INSERT OR UPDATE ON experience_merge_records
        FOR EACH ROW EXECUTE FUNCTION validate_merge_record_decision_scope();
        """
    )
    for table_name in (
        "inference_suggestions",
        "inference_suggestion_sources",
        "inference_decisions",
        "experience_duplicate_suggestions",
        "experience_duplicate_decisions",
        "experience_merge_records",
    ):
        _enable_owner_rls(table_name, "owner_user_id")
    for table_name in (
        "inference_suggestion_sources",
        "inference_decisions",
        "experience_duplicate_decisions",
        "experience_merge_records",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_append_only BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()"
        )


def downgrade() -> None:
    for table_name in (
        "experience_merge_records",
        "experience_duplicate_decisions",
        "inference_decisions",
        "inference_suggestion_sources",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only ON {table_name}")
    for table_name in (
        "experience_merge_records",
        "experience_duplicate_decisions",
        "experience_duplicate_suggestions",
        "inference_decisions",
        "inference_suggestion_sources",
        "inference_suggestions",
    ):
        op.execute(f"DROP POLICY IF EXISTS {table_name}_owner_policy ON {table_name}")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_merge_records_decision_scope ON experience_merge_records"
    )
    op.execute("DROP FUNCTION IF EXISTS validate_merge_record_decision_scope()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_duplicate_suggestions_activity_scope "
        "ON experience_duplicate_suggestions"
    )
    op.execute("DROP FUNCTION IF EXISTS validate_duplicate_suggestion_activity_scope()")
    op.drop_table("experience_merge_records")
    op.drop_table("experience_duplicate_decisions")
    op.drop_table("experience_duplicate_suggestions")
    op.drop_constraint(
        "fk_experience_provenance_inference_decision_owner",
        "experience_field_provenance",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_experience_field_provenance_origin_scope",
        "experience_field_provenance",
        type_="check",
    )
    op.create_check_constraint(
        "ck_experience_field_provenance_origin_source_scope",
        "experience_field_provenance",
        "(origin = 'USER_INPUT' AND source_version_id IS NULL) OR "
        "(origin = 'EXTERNAL_SOURCE' AND source_version_id IS NOT NULL)",
    )
    op.drop_column("experience_field_provenance", "inference_decision_id")
    op.drop_table("inference_decisions")
    op.drop_table("inference_suggestion_sources")
    op.drop_index("ix_inference_suggestions_owner_status", table_name="inference_suggestions")
    op.drop_table("inference_suggestions")
    op.drop_constraint("uq_episode_versions_id_episode_owner", "episode_versions", type_="unique")


def _enable_owner_rls(table_name: str, owner_column: str) -> None:
    expression = f"{owner_column} = NULLIF(current_setting('app.current_user_id', true), '')::uuid"
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table_name}_owner_policy ON {table_name} "
        f"USING ({expression}) WITH CHECK ({expression})"
    )
