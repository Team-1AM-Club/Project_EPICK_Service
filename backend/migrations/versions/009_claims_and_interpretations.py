"""Add public claims and physically separate interpretations.

Revision ID: 009_claims_interpretations
Revises: 008_company_source_operations
Create Date: 2026-09-15
"""

# ruff: noqa: E501

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "009_claims_interpretations"
down_revision = "008_company_source_operations"
branch_labels = None
depends_on = None

CLAIM_STATUS_VALUES = "'ACTIVE', 'RETRACTED', 'SUPERSEDED'"
CLAIM_CERTAINTY_VALUES = "'UNVERIFIED', 'SUPPORTED', 'CONFLICTING', 'RETRACTED'"
INTERPRETATION_STATUS_VALUES = "'ACTIVE', 'RETRACTED', 'SUPERSEDED'"
INTERPRETATION_CONFIDENCE_VALUES = "'UNVERIFIED', 'SUPPORTED', 'CONFLICTING'"
EVIDENCE_RELATION_VALUES = "'SUPPORTS', 'CONTRADICTS', 'CONTEXT'"
CLAIM_RELATION_VALUES = "'REFINES', 'CONTRADICTS', 'SUPERSEDES', 'RELATED'"
USER_DECISION_VALUES = "'ACCEPTED', 'DISMISSED', 'QUESTIONED'"
PROJECT_DECISION_VALUES = "'ACCEPTED', 'DISMISSED', 'REVISED'"


def upgrade() -> None:
    op.create_table(
        "claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'ACTIVE'"), nullable=False
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
        sa.CheckConstraint(f"status IN ({CLAIM_STATUS_VALUES})", name="status_allowed"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "company_id", name="uq_claims_id_company_id"),
    )
    op.create_table(
        "claim_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("claim_type", sa.String(length=64), nullable=False),
        sa.Column("subject_key", sa.Text(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column(
            "certainty",
            sa.String(length=16),
            server_default=sa.text("'UNVERIFIED'"),
            nullable=False,
        ),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version_no >= 1", name="version_no_positive"),
        sa.CheckConstraint(f"certainty IN ({CLAIM_CERTAINTY_VALUES})", name="certainty_allowed"),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="valid_range_ordered",
        ),
        sa.ForeignKeyConstraint(
            ["claim_id", "company_id"], ["claims.id", "claims.company_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claim_id", "version_no", name="uq_claim_versions_claim_id_version_no"),
        sa.UniqueConstraint(
            "id", "claim_id", "company_id", name="uq_claim_versions_id_claim_id_company_id"
        ),
        sa.UniqueConstraint("id", "company_id", name="uq_claim_versions_id_company_id"),
    )
    op.create_foreign_key(
        "fk_claims_current_version_scope",
        "claims",
        "claim_versions",
        ["current_version_id", "id", "company_id"],
        ["id", "claim_id", "company_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "claim_evidence_links",
        sa.Column("claim_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_span_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation_type", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"relation_type IN ({EVIDENCE_RELATION_VALUES})", name="relation_type_allowed"
        ),
        sa.ForeignKeyConstraint(["claim_version_id"], ["claim_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["evidence_span_id"], ["evidence_spans.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("claim_version_id", "evidence_span_id"),
    )
    op.create_table(
        "claim_relations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_claim_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_claim_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation_type", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "from_claim_version_id <> to_claim_version_id", name="different_claim_versions"
        ),
        sa.CheckConstraint(
            f"relation_type IN ({CLAIM_RELATION_VALUES})", name="relation_type_allowed"
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["from_claim_version_id", "company_id"],
            ["claim_versions.id", "claim_versions.company_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_claim_version_id", "company_id"],
            ["claim_versions.id", "claim_versions.company_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "from_claim_version_id",
            "to_claim_version_id",
            "relation_type",
            name="uq_claim_relations_from_to_type",
        ),
    )

    op.create_table(
        "interpretations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'ACTIVE'"), nullable=False
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
        sa.CheckConstraint(f"status IN ({INTERPRETATION_STATUS_VALUES})", name="status_allowed"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "company_id", name="uq_interpretations_id_company_id"),
    )
    op.create_table(
        "interpretation_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("interpretation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("interpretation_type", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "confidence",
            sa.String(length=16),
            server_default=sa.text("'UNVERIFIED'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version_no >= 1", name="version_no_positive"),
        sa.CheckConstraint(
            f"confidence IN ({INTERPRETATION_CONFIDENCE_VALUES})", name="confidence_allowed"
        ),
        sa.ForeignKeyConstraint(
            ["interpretation_id", "company_id"],
            ["interpretations.id", "interpretations.company_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "interpretation_id",
            "version_no",
            name="uq_interpretation_versions_interpretation_id_version_no",
        ),
        sa.UniqueConstraint(
            "id",
            "interpretation_id",
            "company_id",
            name="uq_interpretation_versions_id_interpretation_id_company_id",
        ),
        sa.UniqueConstraint("id", "company_id", name="uq_interpretation_versions_id_company_id"),
    )
    op.create_foreign_key(
        "fk_interpretations_current_version_scope",
        "interpretations",
        "interpretation_versions",
        ["current_version_id", "id", "company_id"],
        ["id", "interpretation_id", "company_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "interpretation_evidence_links",
        sa.Column("interpretation_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_span_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation_type", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"relation_type IN ({EVIDENCE_RELATION_VALUES})", name="relation_type_allowed"
        ),
        sa.ForeignKeyConstraint(
            ["interpretation_version_id"], ["interpretation_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["evidence_span_id"], ["evidence_spans.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("interpretation_version_id", "evidence_span_id"),
    )

    op.create_table(
        "project_interpretations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'ACTIVE'"), nullable=False
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
        sa.CheckConstraint(f"status IN ({INTERPRETATION_STATUS_VALUES})", name="status_allowed"),
        sa.ForeignKeyConstraint(
            ["project_id", "owner_user_id"],
            ["application_projects.id", "application_projects.owner_user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "owner_user_id",
            "project_id",
            "company_id",
            name="uq_project_interpretations_id_owner_project_company",
        ),
    )
    op.create_table(
        "project_interpretation_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_interpretation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("public_interpretation_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "confidence",
            sa.String(length=16),
            server_default=sa.text("'UNVERIFIED'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version_no >= 1", name="version_no_positive"),
        sa.CheckConstraint(
            f"confidence IN ({INTERPRETATION_CONFIDENCE_VALUES})", name="confidence_allowed"
        ),
        sa.ForeignKeyConstraint(
            ["project_interpretation_id", "owner_user_id", "project_id", "company_id"],
            [
                "project_interpretations.id",
                "project_interpretations.owner_user_id",
                "project_interpretations.project_id",
                "project_interpretations.company_id",
            ],
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
            ["public_interpretation_version_id", "company_id"],
            ["interpretation_versions.id", "interpretation_versions.company_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_interpretation_id",
            "version_no",
            name="uq_project_interpretation_versions_interpretation_id_version_no",
        ),
        sa.UniqueConstraint(
            "id",
            "project_interpretation_id",
            "owner_user_id",
            "project_id",
            "company_id",
            name="uq_piv_id_interpretation_scope",
        ),
        sa.UniqueConstraint(
            "id", "owner_user_id", name="uq_project_interpretation_versions_id_owner_user_id"
        ),
    )
    op.create_foreign_key(
        "fk_project_interpretations_current_version_scope",
        "project_interpretations",
        "project_interpretation_versions",
        ["current_version_id", "id", "owner_user_id", "project_id", "company_id"],
        ["id", "project_interpretation_id", "owner_user_id", "project_id", "company_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "project_interpretation_evidence_links",
        sa.Column(
            "project_interpretation_version_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_span_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation_type", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"relation_type IN ({EVIDENCE_RELATION_VALUES})", name="relation_type_allowed"
        ),
        sa.ForeignKeyConstraint(
            ["project_interpretation_version_id", "owner_user_id"],
            ["project_interpretation_versions.id", "project_interpretation_versions.owner_user_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["evidence_span_id"], ["evidence_spans.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("project_interpretation_version_id", "evidence_span_id"),
    )
    op.create_table(
        "project_interpretation_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "project_interpretation_version_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_code", sa.String(length=16), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"decision_code IN ({PROJECT_DECISION_VALUES})", name="decision_code_allowed"
        ),
        sa.ForeignKeyConstraint(
            ["project_interpretation_version_id", "owner_user_id"],
            ["project_interpretation_versions.id", "project_interpretation_versions.owner_user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "user_interpretation_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("interpretation_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_code", sa.String(length=16), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"decision_code IN ({USER_DECISION_VALUES})", name="decision_code_allowed"
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
            ["interpretation_version_id", "company_id"],
            ["interpretation_versions.id", "interpretation_versions.company_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_user_id",
            "project_version_id",
            "interpretation_version_id",
            name="uq_uid_owner_project_version_interpretation",
        ),
    )

    op.create_index(
        "ix_claim_versions_company_type", "claim_versions", ["company_id", "claim_type"]
    )
    op.create_index(
        "ix_interpretation_versions_company_type",
        "interpretation_versions",
        ["company_id", "interpretation_type"],
    )
    op.create_index(
        "ix_project_interpretation_versions_owner_project",
        "project_interpretation_versions",
        ["owner_user_id", "project_id"],
    )

    op.execute(
        """
        CREATE FUNCTION reject_append_only_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = '55000';
        END;
        $$
        """
    )
    for table_name in (
        "source_versions",
        "evidence_spans",
        "source_collection_attempts",
        "claim_versions",
        "claim_evidence_links",
        "claim_relations",
        "interpretation_versions",
        "interpretation_evidence_links",
        "project_interpretation_versions",
        "project_interpretation_evidence_links",
        "project_interpretation_decisions",
        "user_interpretation_decisions",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_append_only BEFORE UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION reject_append_only_mutation()"
        )

    op.execute(
        """
        CREATE FUNCTION validate_claim_evidence_company_scope()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM claim_versions AS claim_version
                JOIN evidence_spans AS evidence_span ON evidence_span.id = NEW.evidence_span_id
                JOIN source_versions AS source_version ON source_version.id = evidence_span.source_version_id
                WHERE claim_version.id = NEW.claim_version_id
                  AND claim_version.company_id = source_version.company_id
            ) THEN
                RAISE EXCEPTION 'claim evidence is outside the claim company scope' USING ERRCODE = '23503';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION validate_interpretation_evidence_company_scope()
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
    op.execute(
        """
        CREATE FUNCTION validate_project_interpretation_evidence_company_scope()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM project_interpretation_versions AS project_interpretation_version
                JOIN evidence_spans AS evidence_span ON evidence_span.id = NEW.evidence_span_id
                JOIN source_versions AS source_version ON source_version.id = evidence_span.source_version_id
                WHERE project_interpretation_version.id = NEW.project_interpretation_version_id
                  AND project_interpretation_version.owner_user_id = NEW.owner_user_id
                  AND project_interpretation_version.company_id = source_version.company_id
            ) THEN
                RAISE EXCEPTION 'project interpretation evidence is outside the project company scope' USING ERRCODE = '23503';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_claim_evidence_company_scope
        AFTER INSERT ON claim_evidence_links DEFERRABLE INITIALLY IMMEDIATE
        FOR EACH ROW EXECUTE FUNCTION validate_claim_evidence_company_scope();
        CREATE CONSTRAINT TRIGGER trg_interpretation_evidence_company_scope
        AFTER INSERT ON interpretation_evidence_links DEFERRABLE INITIALLY IMMEDIATE
        FOR EACH ROW EXECUTE FUNCTION validate_interpretation_evidence_company_scope();
        CREATE CONSTRAINT TRIGGER trg_project_interpretation_evidence_company_scope
        AFTER INSERT ON project_interpretation_evidence_links DEFERRABLE INITIALLY IMMEDIATE
        FOR EACH ROW EXECUTE FUNCTION validate_project_interpretation_evidence_company_scope();
        """
    )
    for table_name in (
        "project_interpretations",
        "project_interpretation_versions",
        "project_interpretation_evidence_links",
        "project_interpretation_decisions",
        "user_interpretation_decisions",
    ):
        _enable_owner_rls(table_name, "owner_user_id")


def downgrade() -> None:
    for table_name in (
        "project_interpretations",
        "project_interpretation_versions",
        "project_interpretation_evidence_links",
        "project_interpretation_decisions",
        "user_interpretation_decisions",
    ):
        op.execute(f"DROP POLICY IF EXISTS {table_name}_owner_policy ON {table_name}")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_project_interpretation_evidence_company_scope ON project_interpretation_evidence_links"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_interpretation_evidence_company_scope ON interpretation_evidence_links"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_claim_evidence_company_scope ON claim_evidence_links")
    op.execute("DROP FUNCTION IF EXISTS validate_project_interpretation_evidence_company_scope()")
    op.execute("DROP FUNCTION IF EXISTS validate_interpretation_evidence_company_scope()")
    op.execute("DROP FUNCTION IF EXISTS validate_claim_evidence_company_scope()")
    for table_name in (
        "source_versions",
        "evidence_spans",
        "source_collection_attempts",
        "claim_versions",
        "claim_evidence_links",
        "claim_relations",
        "interpretation_versions",
        "interpretation_evidence_links",
        "project_interpretation_versions",
        "project_interpretation_evidence_links",
        "project_interpretation_decisions",
        "user_interpretation_decisions",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS reject_append_only_mutation()")
    op.drop_index(
        "ix_project_interpretation_versions_owner_project",
        table_name="project_interpretation_versions",
    )
    op.drop_index("ix_interpretation_versions_company_type", table_name="interpretation_versions")
    op.drop_index("ix_claim_versions_company_type", table_name="claim_versions")
    op.drop_constraint(
        "fk_project_interpretations_current_version_scope",
        "project_interpretations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_interpretations_current_version_scope", "interpretations", type_="foreignkey"
    )
    op.drop_constraint("fk_claims_current_version_scope", "claims", type_="foreignkey")
    op.drop_table("user_interpretation_decisions")
    op.drop_table("project_interpretation_decisions")
    op.drop_table("project_interpretation_evidence_links")
    op.drop_table("project_interpretation_versions")
    op.drop_table("project_interpretations")
    op.drop_table("interpretation_evidence_links")
    op.drop_table("interpretation_versions")
    op.drop_table("interpretations")
    op.drop_table("claim_relations")
    op.drop_table("claim_evidence_links")
    op.drop_table("claim_versions")
    op.drop_table("claims")


def _enable_owner_rls(table_name: str, owner_column: str) -> None:
    expression = f"{owner_column} = NULLIF(current_setting('app.current_user_id', true), '')::uuid"
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table_name}_owner_policy ON {table_name} "
        f"USING ({expression}) WITH CHECK ({expression})"
    )
