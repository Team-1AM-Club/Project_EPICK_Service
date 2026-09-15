"""Complete question-analysis intent and requirement provenance.

Revision ID: 019_question_analysis_provenance
Revises: 018_claim_intent_completion
Create Date: 2026-09-15

Revision 010 modelled question analysis as links to public job-posting
requirements.  A question's own parsed requirements are distinct private
records, so this revision preserves the former links under explicit legacy
names and creates the document-defined private provenance chain.
"""

# ruff: noqa: E501

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "019_question_analysis_provenance"
down_revision = "018_claim_intent_completion"
branch_labels = None
depends_on = None

INTENT_PRIORITY_VALUES = "'PRIMARY', 'SECONDARY'"
GROUP_OPERATOR_VALUES = "'AND', 'OR'"


def upgrade() -> None:
    # Preserve the old public-job-posting links.  Their rows cannot be promoted
    # to question-derived requirements because they contain neither intent nor
    # source-span/rationale provenance.
    _rename_legacy_link_constraints_for_upgrade()
    op.rename_table(
        "question_analysis_requirement_groups",
        "question_analysis_job_requirement_group_links",
    )
    op.rename_table(
        "question_analysis_requirements",
        "question_analysis_job_requirement_links",
    )
    _reject_legacy_link_writes()

    # An Intent needs a stable row identity because a Requirement Group must
    # refer to one Intent belonging to the same analysis.
    op.add_column(
        "question_analysis_intents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
    )
    op.alter_column("question_analysis_intents", "id", server_default=None)
    op.add_column(
        "question_analysis_intents",
        sa.Column("priority", sa.String(length=16), nullable=True),
    )
    op.execute(
        "UPDATE question_analysis_intents "
        "SET priority = CASE WHEN is_primary THEN 'PRIMARY' ELSE 'SECONDARY' END"
    )
    op.alter_column("question_analysis_intents", "priority", nullable=False)
    op.add_column(
        "question_analysis_intents",
        sa.Column("source_span_start", sa.Integer(), nullable=True),
    )
    op.add_column(
        "question_analysis_intents",
        sa.Column("source_span_end", sa.Integer(), nullable=True),
    )
    op.add_column(
        "question_analysis_intents",
        sa.Column("rationale", sa.Text(), nullable=True),
    )
    op.add_column(
        "question_analysis_intents",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_check_constraint(
        "priority_allowed",
        "question_analysis_intents",
        f"priority IN ({INTENT_PRIORITY_VALUES})",
    )
    op.create_check_constraint(
        "source_span_ordered",
        "question_analysis_intents",
        "source_span_start IS NULL OR source_span_start >= 0",
    )
    op.create_check_constraint(
        "source_span_end_ordered",
        "question_analysis_intents",
        "source_span_end IS NULL OR "
        "(source_span_end >= 0 AND source_span_start IS NOT NULL "
        "AND source_span_end >= source_span_start)",
    )
    op.drop_index("uq_question_analysis_intents_primary", table_name="question_analysis_intents")
    op.drop_constraint("pk_question_analysis_intents", "question_analysis_intents", type_="primary")
    op.create_primary_key("pk_question_analysis_intents", "question_analysis_intents", ["id"])
    op.create_unique_constraint(
        "uq_question_analysis_intents_id_question_analysis",
        "question_analysis_intents",
        ["id", "question_analysis_id"],
    )
    op.create_unique_constraint(
        "uq_question_analysis_intents_analysis_taxonomy",
        "question_analysis_intents",
        ["question_analysis_id", "intent_code", "intent_taxonomy_version"],
    )
    op.create_index(
        "uq_question_analysis_intents_primary",
        "question_analysis_intents",
        ["question_analysis_id"],
        unique=True,
        postgresql_where=sa.text("priority = 'PRIMARY'"),
    )
    op.drop_column("question_analysis_intents", "is_primary")
    op.drop_column("question_analysis_intents", "rank")

    op.create_table(
        "question_analysis_requirement_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_analysis_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("intent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_order", sa.Integer(), nullable=False),
        sa.Column("operator", sa.String(length=8), nullable=False),
        sa.Column(
            "same_experience_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("group_order >= 0", name="group_order_not_negative"),
        sa.CheckConstraint(f"operator IN ({GROUP_OPERATOR_VALUES})", name="operator_allowed"),
        sa.ForeignKeyConstraint(
            ["question_analysis_id", "owner_user_id"],
            ["question_analyses.id", "question_analyses.owner_user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["intent_id", "question_analysis_id"],
            ["question_analysis_intents.id", "question_analysis_intents.question_analysis_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
        sa.UniqueConstraint("question_analysis_id", "group_order", name="analysis_group_order"),
    )
    op.create_table(
        "question_analysis_requirements",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_span_start", sa.Integer(), nullable=True),
        sa.Column("source_span_end", sa.Integer(), nullable=True),
        sa.Column("requirement_type", sa.String(length=64), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("importance", sa.String(length=32), nullable=False),
        sa.Column("uncertainty", sa.Text(), nullable=True),
        sa.Column("order_no", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("order_no >= 0", name="order_no_not_negative"),
        sa.CheckConstraint(
            "source_span_start IS NULL OR source_span_start >= 0",
            name="source_span_ordered",
        ),
        sa.CheckConstraint(
            "source_span_end IS NULL OR "
            "(source_span_end >= 0 AND source_span_start IS NOT NULL "
            "AND source_span_end >= source_span_start)",
            name="source_span_end_ordered",
        ),
        sa.ForeignKeyConstraint(
            ["group_id", "owner_user_id"],
            [
                "question_analysis_requirement_groups.id",
                "question_analysis_requirement_groups.owner_user_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "order_no", name="group_order_no"),
    )
    _enable_owner_rls("question_analysis_requirement_groups", "owner_user_id")
    _enable_owner_rls("question_analysis_requirements", "owner_user_id")


def downgrade() -> None:
    _disable_owner_rls("question_analysis_requirements")
    _disable_owner_rls("question_analysis_requirement_groups")
    op.drop_table("question_analysis_requirements")
    op.drop_table("question_analysis_requirement_groups")

    op.drop_index("uq_question_analysis_intents_primary", table_name="question_analysis_intents")
    op.drop_constraint(
        "uq_question_analysis_intents_analysis_taxonomy",
        "question_analysis_intents",
        type_="unique",
    )
    op.drop_constraint(
        "uq_question_analysis_intents_id_question_analysis",
        "question_analysis_intents",
        type_="unique",
    )
    op.drop_constraint("pk_question_analysis_intents", "question_analysis_intents", type_="primary")
    op.add_column(
        "question_analysis_intents",
        sa.Column("rank", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "question_analysis_intents",
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.execute("UPDATE question_analysis_intents SET is_primary = (priority = 'PRIMARY')")
    op.alter_column("question_analysis_intents", "rank", server_default=None)
    op.alter_column("question_analysis_intents", "is_primary", server_default=None)
    op.create_primary_key(
        "pk_question_analysis_intents",
        "question_analysis_intents",
        ["question_analysis_id", "intent_code", "intent_taxonomy_version"],
    )
    op.create_index(
        "uq_question_analysis_intents_primary",
        "question_analysis_intents",
        ["question_analysis_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )
    op.drop_constraint("source_span_end_ordered", "question_analysis_intents", type_="check")
    op.drop_constraint("source_span_ordered", "question_analysis_intents", type_="check")
    op.drop_constraint("priority_allowed", "question_analysis_intents", type_="check")
    op.drop_column("question_analysis_intents", "created_at")
    op.drop_column("question_analysis_intents", "rationale")
    op.drop_column("question_analysis_intents", "source_span_end")
    op.drop_column("question_analysis_intents", "source_span_start")
    op.drop_column("question_analysis_intents", "priority")
    op.drop_column("question_analysis_intents", "id")

    _allow_legacy_link_writes()
    op.rename_table(
        "question_analysis_job_requirement_links",
        "question_analysis_requirements",
    )
    op.rename_table(
        "question_analysis_job_requirement_group_links",
        "question_analysis_requirement_groups",
    )
    _rename_legacy_link_constraints_for_downgrade()


def _rename_legacy_link_constraints_for_upgrade() -> None:
    op.execute(
        "ALTER TABLE question_analysis_requirement_groups "
        "RENAME CONSTRAINT pk_question_analysis_requirement_groups "
        "TO pk_question_analysis_job_requirement_group_links"
    )
    op.execute(
        "ALTER TABLE question_analysis_requirement_groups "
        "RENAME CONSTRAINT fk_question_analysis_requirement_groups_question_analys_c824 "
        "TO fk_qarg_legacy_analysis"
    )
    op.execute(
        "ALTER TABLE question_analysis_requirement_groups "
        "RENAME CONSTRAINT fk_question_analysis_requirement_groups_requirement_gro_487f "
        "TO fk_qarg_legacy_posting_group"
    )
    op.execute(
        "ALTER TABLE question_analysis_requirements "
        "RENAME CONSTRAINT pk_question_analysis_requirements "
        "TO pk_question_analysis_job_requirement_links"
    )
    op.execute(
        "ALTER TABLE question_analysis_requirements "
        "RENAME CONSTRAINT fk_question_analysis_requirements_question_analysis_id__0bd5 "
        "TO fk_qar_legacy_analysis"
    )
    op.execute(
        "ALTER TABLE question_analysis_requirements "
        "RENAME CONSTRAINT fk_question_analysis_requirements_requirement_id_requirements "
        "TO fk_qar_legacy_posting_requirement"
    )


def _rename_legacy_link_constraints_for_downgrade() -> None:
    op.execute(
        "ALTER TABLE question_analysis_requirement_groups "
        "RENAME CONSTRAINT pk_question_analysis_job_requirement_group_links "
        "TO pk_question_analysis_requirement_groups"
    )
    op.execute(
        "ALTER TABLE question_analysis_requirement_groups "
        "RENAME CONSTRAINT fk_qarg_legacy_analysis "
        "TO fk_question_analysis_requirement_groups_question_analys_c824"
    )
    op.execute(
        "ALTER TABLE question_analysis_requirement_groups "
        "RENAME CONSTRAINT fk_qarg_legacy_posting_group "
        "TO fk_question_analysis_requirement_groups_requirement_gro_487f"
    )
    op.execute(
        "ALTER TABLE question_analysis_requirements "
        "RENAME CONSTRAINT pk_question_analysis_job_requirement_links "
        "TO pk_question_analysis_requirements"
    )
    op.execute(
        "ALTER TABLE question_analysis_requirements "
        "RENAME CONSTRAINT fk_qar_legacy_analysis "
        "TO fk_question_analysis_requirements_question_analysis_id__0bd5"
    )
    op.execute(
        "ALTER TABLE question_analysis_requirements "
        "RENAME CONSTRAINT fk_qar_legacy_posting_requirement "
        "TO fk_question_analysis_requirements_requirement_id_requirements"
    )


def _reject_legacy_link_writes() -> None:
    op.execute(
        """
        CREATE FUNCTION reject_legacy_question_analysis_link_write()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'legacy question-analysis requirement links are read-only' USING ERRCODE = '55000';
        END;
        $$;
        CREATE TRIGGER trg_question_analysis_job_requirement_group_links_read_only
        BEFORE INSERT OR UPDATE ON question_analysis_job_requirement_group_links
        FOR EACH ROW EXECUTE FUNCTION reject_legacy_question_analysis_link_write();
        CREATE TRIGGER trg_question_analysis_job_requirement_links_read_only
        BEFORE INSERT OR UPDATE ON question_analysis_job_requirement_links
        FOR EACH ROW EXECUTE FUNCTION reject_legacy_question_analysis_link_write();
        """
    )


def _allow_legacy_link_writes() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_question_analysis_job_requirement_links_read_only "
        "ON question_analysis_job_requirement_links"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_question_analysis_job_requirement_group_links_read_only "
        "ON question_analysis_job_requirement_group_links"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_legacy_question_analysis_link_write()")


def _enable_owner_rls(table_name: str, owner_column: str) -> None:
    expression = f"{owner_column} = NULLIF(current_setting('app.current_user_id', true), '')::uuid"
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table_name}_owner_policy ON {table_name} "
        f"USING ({expression}) WITH CHECK ({expression})"
    )


def _disable_owner_rls(table_name: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS {table_name}_owner_policy ON {table_name}")
    op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")
