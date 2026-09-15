"""complete source operations and versioned organization references

Revision ID: 008_company_source_operations
Revises: 007_job_postings_requirements
Create Date: 2026-09-14
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "008_company_source_operations"
down_revision = "007_job_postings_requirements"
branch_labels = None
depends_on = None

ORG_RESOURCE_STATUS_VALUES = "'ACTIVE', 'INACTIVE'"
ACCESS_RESULT_VALUES = "'ALLOWED', 'DENIED', 'REVIEW_REQUIRED', 'ERROR'"
STORAGE_RESULT_VALUES = "'STORED_FULL', 'STORED_EXCERPT', 'STORED_METADATA', 'NOT_STORED', 'ERROR'"
PARSE_RESULT_VALUES = "'SUCCEEDED', 'PARTIAL', 'FAILED', 'NOT_ATTEMPTED'"
RESULT_COMPLETENESS_VALUES = "'none', 'partial', 'complete'"


def upgrade() -> None:
    op.create_table(
        "company_relations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column("evidence_span_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("from_company_id <> to_company_id", name="different_companies"),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="valid_range_ordered",
        ),
        sa.ForeignKeyConstraint(
            ["from_company_id"],
            ["companies.id"],
            name="fk_company_relations_from_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["to_company_id"],
            ["companies.id"],
            name="fk_company_relations_to_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_span_id"],
            ["evidence_spans.id"],
            name="fk_company_relations_evidence_span",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_company_relations"),
        sa.UniqueConstraint(
            "from_company_id",
            "to_company_id",
            "relation_type",
            name="uq_company_relations_edge",
        ),
    )

    op.create_table(
        "org_units",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'ACTIVE'"),
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
        sa.CheckConstraint(f"status IN ({ORG_RESOURCE_STATUS_VALUES})", name="status_allowed"),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_org_units_company",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_org_units"),
        sa.UniqueConstraint("id", "company_id", name="uq_org_units_id_company_id"),
    )
    op.create_table(
        "org_unit_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_unit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("parent_org_unit_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("evidence_span_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version_no >= 1", name="version_no_positive"),
        sa.CheckConstraint(
            "parent_org_unit_version_id IS NULL OR parent_org_unit_version_id <> id",
            name="parent_is_not_self",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="valid_range_ordered",
        ),
        sa.ForeignKeyConstraint(
            ["org_unit_id", "company_id"],
            ["org_units.id", "org_units.company_id"],
            name="fk_org_unit_versions_org_unit_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_org_unit_version_id", "company_id"],
            ["org_unit_versions.id", "org_unit_versions.company_id"],
            name="fk_org_unit_versions_parent_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_span_id"],
            ["evidence_spans.id"],
            name="fk_org_unit_versions_evidence_span",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_org_unit_versions"),
        sa.UniqueConstraint(
            "org_unit_id", "version_no", name="uq_org_unit_versions_org_unit_version_no"
        ),
        sa.UniqueConstraint(
            "id", "company_id", name="uq_org_unit_versions_id_company_id"
        ),
        sa.UniqueConstraint(
            "id", "org_unit_id", "company_id",
            name="uq_org_unit_versions_id_org_unit_company",
        ),
    )
    op.create_foreign_key(
        "fk_org_units_current_version_scope",
        "org_units",
        "org_unit_versions",
        ["current_version_id", "id", "company_id"],
        ["id", "org_unit_id", "company_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'ACTIVE'"),
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
        sa.CheckConstraint(f"status IN ({ORG_RESOURCE_STATUS_VALUES})", name="status_allowed"),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_roles_company",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.UniqueConstraint("id", "company_id", name="uq_roles_id_company_id"),
    )
    op.create_table(
        "role_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("org_unit_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("employment_category", sa.String(length=64), nullable=True),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("evidence_span_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version_no >= 1", name="version_no_positive"),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="valid_range_ordered",
        ),
        sa.ForeignKeyConstraint(
            ["role_id", "company_id"],
            ["roles.id", "roles.company_id"],
            name="fk_role_versions_role_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_unit_version_id", "company_id"],
            ["org_unit_versions.id", "org_unit_versions.company_id"],
            name="fk_role_versions_org_unit_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_span_id"],
            ["evidence_spans.id"],
            name="fk_role_versions_evidence_span",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_role_versions"),
        sa.UniqueConstraint("role_id", "version_no", name="uq_role_versions_role_id_version_no"),
        sa.UniqueConstraint("id", "company_id", name="uq_role_versions_id_company_id"),
        sa.UniqueConstraint(
            "id", "role_id", "company_id", name="uq_role_versions_id_role_company"
        ),
    )
    op.create_foreign_key(
        "fk_roles_current_version_scope",
        "roles",
        "role_versions",
        ["current_version_id", "id", "company_id"],
        ["id", "role_id", "company_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_unique_constraint(
        "uq_job_source_links_id_command_id",
        "job_source_links",
        ["id", "command_id"],
    )
    op.create_table(
        "source_collection_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_source_link_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("access_result", sa.String(length=32), nullable=False),
        sa.Column("storage_result", sa.String(length=32), nullable=False),
        sa.Column("parse_result", sa.String(length=32), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("command_schema_version", sa.String(length=32), nullable=False),
        sa.Column("result_schema_version", sa.String(length=32), nullable=True),
        sa.Column("result_completeness", sa.String(length=16), nullable=True),
        sa.Column("restriction_code", sa.String(length=64), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("safe_failure_message", sa.Text(), nullable=True),
        sa.CheckConstraint("attempt_no >= 1", name="attempt_no_positive"),
        sa.CheckConstraint(
            f"access_result IN ({ACCESS_RESULT_VALUES})", name="access_result_allowed"
        ),
        sa.CheckConstraint(
            f"storage_result IN ({STORAGE_RESULT_VALUES})", name="storage_result_allowed"
        ),
        sa.CheckConstraint(
            f"parse_result IN ({PARSE_RESULT_VALUES})", name="parse_result_allowed"
        ),
        sa.CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599", name="http_status_range"
        ),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at", name="completed_after_started"
        ),
        sa.CheckConstraint(
            "command_id IS NULL OR job_source_link_id IS NOT NULL", name="command_requires_link"
        ),
        sa.CheckConstraint(
            "result_completeness IS NULL OR "
            f"result_completeness IN ({RESULT_COMPLETENESS_VALUES})",
            name="result_completeness_allowed",
        ),
        sa.CheckConstraint(
            "safe_failure_message IS NULL OR octet_length(safe_failure_message) <= 1024",
            name="safe_failure_message_bounded",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_source_collection_attempts_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_source_link_id", "source_id"],
            ["job_source_links.id", "job_source_links.source_id"],
            name="fk_source_attempts_link_source_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_source_link_id", "command_id"],
            ["job_source_links.id", "job_source_links.command_id"],
            name="fk_source_attempts_link_command_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id", "source_id"],
            ["source_versions.id", "source_versions.source_id"],
            name="fk_source_attempts_source_version_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_source_collection_attempts"),
        sa.UniqueConstraint(
            "source_id",
            "idempotency_key",
            "attempt_no",
            name="uq_source_collection_attempts_source_key_attempt",
        ),
    )
    op.create_index(
        "ix_source_collection_attempts_source_started",
        "source_collection_attempts",
        ["source_id", sa.text("started_at DESC")],
    )

    op.add_column(
        "application_project_versions",
        sa.Column("org_unit_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "application_project_versions",
        sa.Column("role_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_project_versions_org_unit_company",
        "application_project_versions",
        "org_unit_versions",
        ["org_unit_version_id", "company_id"],
        ["id", "company_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_project_versions_role_company",
        "application_project_versions",
        "role_versions",
        ["role_version_id", "company_id"],
        ["id", "company_id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "job_posting_versions",
        sa.Column("org_unit_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "job_posting_versions",
        sa.Column("role_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_job_posting_versions_org_unit_company",
        "job_posting_versions",
        "org_unit_versions",
        ["org_unit_version_id", "company_id"],
        ["id", "company_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_job_posting_versions_role_company",
        "job_posting_versions",
        "role_versions",
        ["role_version_id", "company_id"],
        ["id", "company_id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "requirements",
        sa.Column("org_unit_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "requirements",
        sa.Column("role_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_requirements_at_most_one_org_scope",
        "requirements",
        "(org_unit_version_id IS NOT NULL)::integer + (role_version_id IS NOT NULL)::integer <= 1",
    )
    op.create_foreign_key(
        "fk_requirements_org_unit_version",
        "requirements",
        "org_unit_versions",
        ["org_unit_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_requirements_role_version",
        "requirements",
        "role_versions",
        ["role_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.execute(
        """
        CREATE FUNCTION validate_requirement_org_scope()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            expected_company_id uuid;
        BEGIN
            IF NEW.org_unit_version_id IS NULL AND NEW.role_version_id IS NULL THEN
                RETURN NEW;
            END IF;

            SELECT posting.company_id
            INTO expected_company_id
            FROM requirement_groups AS requirement_group
            JOIN job_posting_versions AS posting
              ON posting.id = requirement_group.job_posting_version_id
            WHERE requirement_group.id = NEW.group_id;

            IF expected_company_id IS NULL THEN
                RAISE EXCEPTION 'requirement group has no job posting company scope'
                    USING ERRCODE = '23503';
            END IF;

            IF NEW.org_unit_version_id IS NOT NULL AND NOT EXISTS (
                SELECT 1
                FROM org_unit_versions
                WHERE id = NEW.org_unit_version_id
                  AND company_id = expected_company_id
            ) THEN
                RAISE EXCEPTION 'organization version is outside the requirement company scope'
                    USING ERRCODE = '23503';
            END IF;

            IF NEW.role_version_id IS NOT NULL AND NOT EXISTS (
                SELECT 1
                FROM role_versions
                WHERE id = NEW.role_version_id
                  AND company_id = expected_company_id
            ) THEN
                RAISE EXCEPTION 'role version is outside the requirement company scope'
                    USING ERRCODE = '23503';
            END IF;

            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_requirements_org_scope
        AFTER INSERT OR UPDATE OF group_id, org_unit_version_id, role_version_id ON requirements
        DEFERRABLE INITIALLY IMMEDIATE
        FOR EACH ROW
        EXECUTE FUNCTION validate_requirement_org_scope()
        """
    )

    op.add_column(
        "experience_field_provenance",
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.drop_constraint(
        "ck_experience_field_provenance_origin_user_input_only",
        "experience_field_provenance",
        type_="check",
    )
    op.create_check_constraint(
        "ck_experience_field_provenance_origin_source_scope",
        "experience_field_provenance",
        "(origin = 'USER_INPUT' AND source_version_id IS NULL) OR "
        "(origin = 'EXTERNAL_SOURCE' AND source_version_id IS NOT NULL)",
    )
    op.create_foreign_key(
        "fk_experience_provenance_source_version",
        "experience_field_provenance",
        "source_versions",
        ["source_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_experience_provenance_source_version",
        "experience_field_provenance",
        type_="foreignkey",
    )
    op.drop_constraint(
        "ck_experience_field_provenance_origin_source_scope",
        "experience_field_provenance",
        type_="check",
    )
    op.execute("DELETE FROM experience_field_provenance WHERE source_version_id IS NOT NULL")
    op.drop_column("experience_field_provenance", "source_version_id")
    op.create_check_constraint(
        "ck_experience_field_provenance_origin_user_input_only",
        "experience_field_provenance",
        "origin = 'USER_INPUT'",
    )

    op.execute("DROP TRIGGER IF EXISTS trg_requirements_org_scope ON requirements")
    op.execute("DROP FUNCTION IF EXISTS validate_requirement_org_scope()")
    op.drop_constraint("fk_requirements_role_version", "requirements", type_="foreignkey")
    op.drop_constraint("fk_requirements_org_unit_version", "requirements", type_="foreignkey")
    op.drop_constraint("ck_requirements_at_most_one_org_scope", "requirements", type_="check")
    op.drop_column("requirements", "role_version_id")
    op.drop_column("requirements", "org_unit_version_id")
    op.drop_constraint(
        "fk_job_posting_versions_role_company", "job_posting_versions", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_job_posting_versions_org_unit_company", "job_posting_versions", type_="foreignkey"
    )
    op.drop_column("job_posting_versions", "role_version_id")
    op.drop_column("job_posting_versions", "org_unit_version_id")
    op.drop_constraint(
        "fk_project_versions_role_company", "application_project_versions", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_project_versions_org_unit_company",
        "application_project_versions",
        type_="foreignkey",
    )
    op.drop_column("application_project_versions", "role_version_id")
    op.drop_column("application_project_versions", "org_unit_version_id")

    op.drop_index(
        "ix_source_collection_attempts_source_started", table_name="source_collection_attempts"
    )
    op.drop_table("source_collection_attempts")
    op.drop_constraint(
        "uq_job_source_links_id_command_id", "job_source_links", type_="unique"
    )
    op.drop_constraint("fk_roles_current_version_scope", "roles", type_="foreignkey")
    op.drop_table("role_versions")
    op.drop_table("roles")
    op.drop_constraint("fk_org_units_current_version_scope", "org_units", type_="foreignkey")
    op.drop_table("org_unit_versions")
    op.drop_table("org_units")
    op.drop_table("company_relations")
