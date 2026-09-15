"""complete claim scope/provenance fields and versioned question intent taxonomy

Revision ID: 018_pg3_claim_and_intent_completion
Revises: 017_deletion_orchestration
Create Date: 2026-09-15
"""

# ruff: noqa: E501

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "018_claim_intent_completion"
down_revision = "017_deletion_orchestration"
branch_labels = None
depends_on = None

CLAIM_VERIFICATION_STATUS_VALUES = "'UNVERIFIED', 'SUPPORTED', 'CONFLICTING', 'RETRACTED'"
EVIDENCE_STANCE_VALUES = "'SUPPORTS', 'CONTRADICTS', 'CONTEXT'"
LEGACY_PREDICATE = "__LEGACY_UNSPECIFIED__"
LEGACY_EXTRACTOR_VERSION = "legacy-pre-018"

# Actual pre-existing constraint name, confirmed against a real PostgreSQL 16
# instance running migrations 000..017 (naming_convention-derived, not guessed).
# NOTE: op.create_check_constraint()/op.drop_constraint(..., type_="check") pass the
# given name through this project's "ck" naming_convention template a SECOND time
# (confirmed against the already-committed 007/008 migrations, which produced doubled
# names such as "ck_job_input_refs_ck_job_input_refs_exactly_one_input"). Every check
# constraint below therefore uses the short, unprefixed suffix so the convention adds
# the "ck_<table>_" prefix exactly once. FK/PK/UQ conventions in this metadata do not
# reference %(constraint_name)s, so explicit full names for those are safe as-is.
_FK_QAI_INTENT_TYPE = "fk_question_analysis_intents_question_intent_type_id_qu_9ada"


def upgrade() -> None:
    # --- claim_versions: subject/predicate/object triple, scope FKs, numeric/period,
    #     verification provenance -----------------------------------------------------
    op.alter_column("claim_versions", "subject_key", new_column_name="subject_text")
    op.alter_column("claim_versions", "statement", new_column_name="object_text")
    op.alter_column("claim_versions", "certainty", new_column_name="verification_status")
    op.drop_constraint("certainty_allowed", "claim_versions", type_="check")
    op.create_check_constraint(
        "verification_status_allowed",
        "claim_versions",
        f"verification_status IN ({CLAIM_VERIFICATION_STATUS_VALUES})",
    )
    # Claim versions were already append-only at revision 017.  Do not update
    # existing rows to invent provenance: add one-time defaults so a non-empty
    # 017 database can cross this boundary without bypassing that trigger.
    op.add_column(
        "claim_versions",
        sa.Column(
            "predicate",
            sa.Text(),
            nullable=False,
            server_default=sa.text(f"'{LEGACY_PREDICATE}'"),
        ),
    )
    op.alter_column("claim_versions", "predicate", server_default=None)
    op.add_column(
        "claim_versions",
        sa.Column("org_unit_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "claim_versions",
        sa.Column("role_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "claim_versions",
        sa.Column("job_posting_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("claim_versions", sa.Column("numeric_value", sa.Numeric(), nullable=True))
    op.add_column("claim_versions", sa.Column("unit", sa.String(length=32), nullable=True))
    op.add_column("claim_versions", sa.Column("period_from", sa.Date(), nullable=True))
    op.add_column("claim_versions", sa.Column("period_to", sa.Date(), nullable=True))
    op.add_column(
        "claim_versions", sa.Column("validity_precision", sa.String(length=32), nullable=True)
    )
    op.add_column("claim_versions", sa.Column("comparison_basis", sa.Text(), nullable=True))
    op.add_column(
        "claim_versions",
        sa.Column(
            "extractor_version",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text(f"'{LEGACY_EXTRACTOR_VERSION}'"),
        ),
    )
    op.alter_column("claim_versions", "extractor_version", server_default=None)
    op.add_column(
        "claim_versions",
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.alter_column("claim_versions", "extracted_at", server_default=None)
    op.create_check_constraint(
        "period_ordered",
        "claim_versions",
        "period_to IS NULL OR period_from IS NULL OR period_to >= period_from",
    )
    op.create_check_constraint(
        "at_most_one_scope",
        "claim_versions",
        "(org_unit_version_id IS NOT NULL)::integer + "
        "(role_version_id IS NOT NULL)::integer + "
        "(job_posting_version_id IS NOT NULL)::integer <= 1",
    )
    op.create_foreign_key(
        "fk_claim_versions_org_unit_version_company_scope",
        "claim_versions",
        "org_unit_versions",
        ["org_unit_version_id", "company_id"],
        ["id", "company_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_claim_versions_role_version_company_scope",
        "claim_versions",
        "role_versions",
        ["role_version_id", "company_id"],
        ["id", "company_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_claim_versions_job_posting_version_company_scope",
        "claim_versions",
        "job_posting_versions",
        ["job_posting_version_id", "company_id"],
        ["id", "company_id"],
        ondelete="RESTRICT",
    )

    # --- claim_evidence_links: relation type is part of its natural key -------------
    op.drop_constraint("pk_claim_evidence_links", "claim_evidence_links", type_="primary")
    op.create_primary_key(
        "pk_claim_evidence_links",
        "claim_evidence_links",
        ["claim_version_id", "evidence_span_id", "relation_type"],
    )

    # --- interpretation_evidence_links: surrogate id, ClaimVersion OR EvidenceSpan,
    #     rename relation_type -> stance -----------------------------------------------
    op.add_column(
        "interpretation_evidence_links",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
    )
    op.alter_column("interpretation_evidence_links", "id", server_default=None)
    op.drop_constraint(
        "pk_interpretation_evidence_links", "interpretation_evidence_links", type_="primary"
    )
    op.create_primary_key(
        "pk_interpretation_evidence_links", "interpretation_evidence_links", ["id"]
    )
    op.create_unique_constraint(
        "uq_interpretation_evidence_links_version_evidence_span",
        "interpretation_evidence_links",
        ["interpretation_version_id", "evidence_span_id"],
    )
    op.alter_column("interpretation_evidence_links", "evidence_span_id", nullable=True)
    op.add_column(
        "interpretation_evidence_links",
        sa.Column("claim_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.alter_column("interpretation_evidence_links", "relation_type", new_column_name="stance")
    op.drop_constraint(
        "relation_type_allowed",
        "interpretation_evidence_links",
        type_="check",
    )
    op.create_check_constraint(
        "stance_allowed",
        "interpretation_evidence_links",
        f"stance IN ({EVIDENCE_STANCE_VALUES})",
    )
    op.create_check_constraint(
        "exactly_one_reference",
        "interpretation_evidence_links",
        "(evidence_span_id IS NOT NULL)::integer + (claim_version_id IS NOT NULL)::integer = 1",
    )
    op.create_foreign_key(
        "fk_interpretation_evidence_links_claim_version",
        "interpretation_evidence_links",
        "claim_versions",
        ["claim_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    # 009's scope trigger assumed evidence_span_id was always present. Now that
    # ClaimVersion is an alternative reference, branch the company-scope check on
    # which reference is populated instead of always joining evidence_spans.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION validate_interpretation_evidence_company_scope()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.evidence_span_id IS NOT NULL THEN
                IF NOT EXISTS (
                    SELECT 1
                    FROM interpretation_versions AS interpretation_version
                    JOIN evidence_spans AS evidence_span ON evidence_span.id = NEW.evidence_span_id
                    JOIN source_versions AS source_version ON source_version.id = evidence_span.source_version_id
                    WHERE interpretation_version.id = NEW.interpretation_version_id
                      AND interpretation_version.company_id = source_version.company_id
                ) THEN
                    RAISE EXCEPTION 'interpretation evidence is outside the interpretation company scope' USING ERRCODE = '23503';
                END IF;
            ELSE
                IF NOT EXISTS (
                    SELECT 1
                    FROM interpretation_versions AS interpretation_version
                    JOIN claim_versions AS claim_version ON claim_version.id = NEW.claim_version_id
                    WHERE interpretation_version.id = NEW.interpretation_version_id
                      AND interpretation_version.company_id = claim_version.company_id
                ) THEN
                    RAISE EXCEPTION 'interpretation claim evidence is outside the interpretation company scope' USING ERRCODE = '23503';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )

    # --- question_intent_types: versioned taxonomy code (PK code+taxonomy_version) --
    op.add_column(
        "question_analysis_intents",
        sa.Column("intent_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "question_analysis_intents",
        sa.Column("intent_taxonomy_version", sa.String(length=32), nullable=True),
    )
    op.execute(
        """
        UPDATE question_analysis_intents AS qai
        SET intent_code = qit.code,
            intent_taxonomy_version = 'v1'
        FROM question_intent_types AS qit
        WHERE qit.id = qai.question_intent_type_id
        """
    )
    op.drop_constraint(_FK_QAI_INTENT_TYPE, "question_analysis_intents", type_="foreignkey")
    op.drop_constraint("pk_question_analysis_intents", "question_analysis_intents", type_="primary")
    op.drop_column("question_analysis_intents", "question_intent_type_id")

    op.add_column(
        "question_intent_types",
        sa.Column("taxonomy_version", sa.String(length=32), nullable=False, server_default="v1"),
    )
    op.alter_column("question_intent_types", "taxonomy_version", server_default=None)
    op.add_column(
        "question_intent_types",
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.drop_constraint("uq_question_intent_types_code", "question_intent_types", type_="unique")
    op.drop_constraint("pk_question_intent_types", "question_intent_types", type_="primary")
    op.drop_column("question_intent_types", "id")
    op.create_primary_key(
        "pk_question_intent_types", "question_intent_types", ["code", "taxonomy_version"]
    )

    op.alter_column("question_analysis_intents", "intent_code", nullable=False)
    op.alter_column("question_analysis_intents", "intent_taxonomy_version", nullable=False)
    op.create_primary_key(
        "pk_question_analysis_intents",
        "question_analysis_intents",
        ["question_analysis_id", "intent_code", "intent_taxonomy_version"],
    )
    op.create_foreign_key(
        "fk_question_analysis_intents_intent_taxonomy",
        "question_analysis_intents",
        "question_intent_types",
        ["intent_code", "intent_taxonomy_version"],
        ["code", "taxonomy_version"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    # --- question_analysis_intents / question_intent_types: back to surrogate id ----
    op.drop_constraint(
        "fk_question_analysis_intents_intent_taxonomy",
        "question_analysis_intents",
        type_="foreignkey",
    )
    op.drop_constraint("pk_question_analysis_intents", "question_analysis_intents", type_="primary")

    op.add_column(
        "question_intent_types",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
    )
    op.alter_column("question_intent_types", "id", server_default=None)
    op.drop_constraint("pk_question_intent_types", "question_intent_types", type_="primary")
    op.create_unique_constraint("uq_question_intent_types_code", "question_intent_types", ["code"])
    op.drop_column("question_intent_types", "is_active")
    op.drop_column("question_intent_types", "taxonomy_version")
    op.create_primary_key("pk_question_intent_types", "question_intent_types", ["id"])

    op.add_column(
        "question_analysis_intents",
        sa.Column("question_intent_type_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        """
        UPDATE question_analysis_intents AS qai
        SET question_intent_type_id = qit.id
        FROM question_intent_types AS qit
        WHERE qit.code = qai.intent_code
        """
    )
    op.alter_column("question_analysis_intents", "question_intent_type_id", nullable=False)
    op.create_primary_key(
        "pk_question_analysis_intents",
        "question_analysis_intents",
        ["question_analysis_id", "question_intent_type_id"],
    )
    op.create_foreign_key(
        _FK_QAI_INTENT_TYPE,
        "question_analysis_intents",
        "question_intent_types",
        ["question_intent_type_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_column("question_analysis_intents", "intent_taxonomy_version")
    op.drop_column("question_analysis_intents", "intent_code")

    # --- interpretation_evidence_links: back to composite PK, relation_type, EvidenceSpan only
    op.execute(
        """
        CREATE OR REPLACE FUNCTION validate_interpretation_evidence_company_scope()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM interpretation_versions AS interpretation_version
                JOIN evidence_spans AS evidence_span ON evidence_span.id = NEW.evidence_span_id
                JOIN source_versions AS source_version ON source_version.id = evidence_span.source_version_id
                WHERE interpretation_version.id = NEW.interpretation_version_id
                  AND interpretation_version.company_id = source_version.company_id
            ) THEN
                RAISE EXCEPTION 'interpretation evidence is outside the interpretation company scope' USING ERRCODE = '23503';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.drop_constraint(
        "fk_interpretation_evidence_links_claim_version",
        "interpretation_evidence_links",
        type_="foreignkey",
    )
    op.drop_constraint(
        "exactly_one_reference",
        "interpretation_evidence_links",
        type_="check",
    )
    op.drop_constraint(
        "stance_allowed",
        "interpretation_evidence_links",
        type_="check",
    )
    op.alter_column("interpretation_evidence_links", "stance", new_column_name="relation_type")
    op.create_check_constraint(
        "relation_type_allowed",
        "interpretation_evidence_links",
        f"relation_type IN ({EVIDENCE_STANCE_VALUES})",
    )
    op.execute("DELETE FROM interpretation_evidence_links WHERE evidence_span_id IS NULL")
    op.drop_column("interpretation_evidence_links", "claim_version_id")
    op.alter_column("interpretation_evidence_links", "evidence_span_id", nullable=False)
    op.drop_constraint(
        "uq_interpretation_evidence_links_version_evidence_span",
        "interpretation_evidence_links",
        type_="unique",
    )
    op.drop_constraint(
        "pk_interpretation_evidence_links", "interpretation_evidence_links", type_="primary"
    )
    op.drop_column("interpretation_evidence_links", "id")
    op.create_primary_key(
        "pk_interpretation_evidence_links",
        "interpretation_evidence_links",
        ["interpretation_version_id", "evidence_span_id"],
    )

    # --- claim_evidence_links: back to the revision-017 natural key -----------------
    op.drop_constraint("pk_claim_evidence_links", "claim_evidence_links", type_="primary")
    op.create_primary_key(
        "pk_claim_evidence_links",
        "claim_evidence_links",
        ["claim_version_id", "evidence_span_id"],
    )

    # --- claim_versions: drop added scope/provenance fields, restore certainty -------
    op.drop_constraint(
        "fk_claim_versions_job_posting_version_company_scope",
        "claim_versions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_claim_versions_role_version_company_scope", "claim_versions", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_claim_versions_org_unit_version_company_scope", "claim_versions", type_="foreignkey"
    )
    op.drop_constraint("at_most_one_scope", "claim_versions", type_="check")
    op.drop_constraint("period_ordered", "claim_versions", type_="check")
    op.drop_column("claim_versions", "extracted_at")
    op.drop_column("claim_versions", "extractor_version")
    op.drop_column("claim_versions", "comparison_basis")
    op.drop_column("claim_versions", "validity_precision")
    op.drop_column("claim_versions", "period_to")
    op.drop_column("claim_versions", "period_from")
    op.drop_column("claim_versions", "unit")
    op.drop_column("claim_versions", "numeric_value")
    op.drop_column("claim_versions", "job_posting_version_id")
    op.drop_column("claim_versions", "role_version_id")
    op.drop_column("claim_versions", "org_unit_version_id")
    op.drop_column("claim_versions", "predicate")
    op.drop_constraint("verification_status_allowed", "claim_versions", type_="check")
    op.alter_column("claim_versions", "verification_status", new_column_name="certainty")
    op.create_check_constraint(
        "certainty_allowed",
        "claim_versions",
        f"certainty IN ({CLAIM_VERIFICATION_STATUS_VALUES})",
    )
    op.alter_column("claim_versions", "object_text", new_column_name="statement")
    op.alter_column("claim_versions", "subject_text", new_column_name="subject_key")
