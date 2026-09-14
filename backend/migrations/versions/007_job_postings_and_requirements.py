"""add versioned job postings, requirements, and canonical skills

Revision ID: 007_job_postings_requirements
Revises: 006_company_sources
Create Date: 2026-09-14
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "007_job_postings_requirements"
down_revision = "006_company_sources"
branch_labels = None
depends_on = None

JOB_POSTING_STATUS_VALUES = "'OPEN', 'CLOSED', 'UNKNOWN', 'ARCHIVED'"
JOB_POSTING_ANALYSIS_STATUS_VALUES = "'PENDING', 'SUCCEEDED', 'LIMITED', 'FAILED'"
REQUIREMENT_GROUP_OPERATOR_VALUES = "'AND', 'OR'"
REQUIREMENT_NECESSITY_VALUES = "'REQUIRED', 'PREFERRED', 'GENERAL'"


def upgrade() -> None:
    op.create_table(
        "canonical_skills",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_canonical_skills"),
        sa.UniqueConstraint("canonical_name", name="uq_canonical_skills_canonical_name"),
    )
    op.create_table(
        "skill_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_skill_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("normalized_alias", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["canonical_skill_id"],
            ["canonical_skills.id"],
            name="fk_skill_aliases_canonical_skill_id_canonical_skills",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_skill_aliases"),
    )
    op.create_index(
        "uq_skill_aliases_normalized_alias_language",
        "skill_aliases",
        ["normalized_alias", "language"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    op.create_table(
        "job_postings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'UNKNOWN'"),
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
            f"status IN ({JOB_POSTING_STATUS_VALUES})", name="status_allowed"
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "company_id"],
            ["sources.id", "sources.company_id"],
            name="fk_job_postings_source_company_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_job_postings"),
        sa.UniqueConstraint("id", "company_id", name="uq_job_postings_id_company_id"),
        sa.UniqueConstraint(
            "id", "source_id", "company_id", name="uq_job_postings_id_source_id_company_id"
        ),
    )
    op.create_index("ix_job_postings_company_status", "job_postings", ["company_id", "status"])
    op.create_table(
        "job_posting_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("posting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("organization_name", sa.Text(), nullable=True),
        sa.Column("role_name", sa.Text(), nullable=True),
        sa.Column("employment_type", sa.String(length=64), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "analysis_status",
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
        sa.CheckConstraint("version_no >= 1", name="version_no_positive"),
        sa.CheckConstraint(
            "closes_at IS NULL OR published_at IS NULL OR closes_at >= published_at",
            name="closing_after_publication",
        ),
        sa.CheckConstraint(
            f"analysis_status IN ({JOB_POSTING_ANALYSIS_STATUS_VALUES})",
            name="analysis_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["posting_id", "source_id", "company_id"],
            ["job_postings.id", "job_postings.source_id", "job_postings.company_id"],
            name="fk_job_posting_versions_posting_source_company_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id", "source_id", "company_id"],
            ["source_versions.id", "source_versions.source_id", "source_versions.company_id"],
            name="fk_job_posting_versions_source_version_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_job_posting_versions"),
        sa.UniqueConstraint(
            "posting_id", "version_no", name="uq_job_posting_versions_posting_id_version_no"
        ),
        sa.UniqueConstraint(
            "id", "company_id", name="uq_job_posting_versions_id_company_id"
        ),
        sa.UniqueConstraint(
            "id", "posting_id", "company_id",
            name="uq_job_posting_versions_id_posting_id_company_id",
        ),
    )
    op.create_foreign_key(
        "fk_job_postings_current_version_scope",
        "job_postings",
        "job_posting_versions",
        ["current_version_id", "id", "company_id"],
        ["id", "posting_id", "company_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "requirement_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_posting_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_order", sa.Integer(), nullable=False),
        sa.Column("operator", sa.String(length=8), nullable=False),
        sa.Column(
            "same_experience_required",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("group_order >= 0", name="group_order_not_negative"),
        sa.CheckConstraint(
            f"operator IN ({REQUIREMENT_GROUP_OPERATOR_VALUES})", name="operator_allowed"
        ),
        sa.ForeignKeyConstraint(
            ["job_posting_version_id"],
            ["job_posting_versions.id"],
            name="fk_requirement_groups_posting_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_requirement_groups"),
        sa.UniqueConstraint(
            "job_posting_version_id",
            "group_order",
            name="uq_requirement_groups_job_posting_version_id_group_order",
        ),
    )
    op.create_table(
        "requirements",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_span_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("necessity", sa.String(length=32), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=True),
        sa.Column("comparison_operator", sa.String(length=32), nullable=True),
        sa.Column("comparison_value", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"necessity IN ({REQUIREMENT_NECESSITY_VALUES})", name="necessity_allowed"
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["requirement_groups.id"],
            name="fk_requirements_group_id_requirement_groups",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_span_id"],
            ["evidence_spans.id"],
            name="fk_requirements_evidence_span_id_evidence_spans",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_requirements"),
    )
    op.create_index("ix_requirements_group_necessity", "requirements", ["group_id", "necessity"])
    op.create_table(
        "requirement_skills",
        sa.Column("requirement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("canonical_skill_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_term", sa.Text(), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["requirement_id"],
            ["requirements.id"],
            name="fk_requirement_skills_requirement_id_requirements",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["canonical_skill_id"],
            ["canonical_skills.id"],
            name="fk_requirement_skills_canonical_skill_id_canonical_skills",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "requirement_id",
            "canonical_skill_id",
            "raw_term",
            name="pk_requirement_skills",
        ),
    )

    op.add_column(
        "application_project_versions",
        sa.Column("job_posting_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_application_project_versions_job_posting_company_scope",
        "application_project_versions",
        "job_postings",
        ["job_posting_id", "company_id"],
        ["id", "company_id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "episode_version_skills",
        sa.Column("canonical_skill_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_episode_version_skills_canonical_skill_id_canonical_skills",
        "episode_version_skills",
        "canonical_skills",
        ["canonical_skill_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.add_column(
        "job_input_refs",
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "job_input_refs",
        sa.Column("job_posting_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.drop_constraint("ck_job_input_refs_exactly_one_input", "job_input_refs", type_="check")
    op.create_check_constraint(
        "ck_job_input_refs_exactly_one_input",
        "job_input_refs",
        "(project_version_id IS NOT NULL)::integer + "
        "(question_version_id IS NOT NULL)::integer + "
        "(episode_version_id IS NOT NULL)::integer + "
        "(snapshot_id IS NOT NULL)::integer + "
        "(source_version_id IS NOT NULL)::integer + "
        "(job_posting_version_id IS NOT NULL)::integer + "
        "((policy_name IS NOT NULL AND policy_version IS NOT NULL)::integer) = 1 "
        "AND (policy_name IS NULL) = (policy_version IS NULL)",
    )
    op.create_foreign_key(
        "fk_job_input_refs_source_version_id_source_versions",
        "job_input_refs",
        "source_versions",
        ["source_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_job_input_refs_job_posting_version_id_job_posting_versions",
        "job_input_refs",
        "job_posting_versions",
        ["job_posting_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_job_input_refs_job_source_version",
        "job_input_refs",
        ["job_id", "source_version_id"],
        unique=True,
        postgresql_where=sa.text("source_version_id IS NOT NULL"),
    )
    op.create_index(
        "uq_job_input_refs_job_job_posting_version",
        "job_input_refs",
        ["job_id", "job_posting_version_id"],
        unique=True,
        postgresql_where=sa.text("job_posting_version_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_job_input_refs_job_job_posting_version", table_name="job_input_refs")
    op.drop_index("uq_job_input_refs_job_source_version", table_name="job_input_refs")
    op.drop_constraint(
        "fk_job_input_refs_job_posting_version_id_job_posting_versions",
        "job_input_refs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_job_input_refs_source_version_id_source_versions",
        "job_input_refs",
        type_="foreignkey",
    )
    op.drop_constraint("ck_job_input_refs_exactly_one_input", "job_input_refs", type_="check")
    op.execute(
        "DELETE FROM job_input_refs "
        "WHERE source_version_id IS NOT NULL OR job_posting_version_id IS NOT NULL"
    )
    op.drop_column("job_input_refs", "job_posting_version_id")
    op.drop_column("job_input_refs", "source_version_id")
    op.create_check_constraint(
        "ck_job_input_refs_exactly_one_input",
        "job_input_refs",
        "(project_version_id IS NOT NULL)::integer + "
        "(question_version_id IS NOT NULL)::integer + "
        "(episode_version_id IS NOT NULL)::integer + "
        "(snapshot_id IS NOT NULL)::integer + "
        "((policy_name IS NOT NULL AND policy_version IS NOT NULL)::integer) = 1 "
        "AND (policy_name IS NULL) = (policy_version IS NULL)",
    )

    op.drop_constraint(
        "fk_episode_version_skills_canonical_skill_id_canonical_skills",
        "episode_version_skills",
        type_="foreignkey",
    )
    op.drop_column("episode_version_skills", "canonical_skill_id")
    op.drop_constraint(
        "fk_application_project_versions_job_posting_company_scope",
        "application_project_versions",
        type_="foreignkey",
    )
    op.drop_column("application_project_versions", "job_posting_id")

    op.drop_table("requirement_skills")
    op.drop_index("ix_requirements_group_necessity", table_name="requirements")
    op.drop_table("requirements")
    op.drop_table("requirement_groups")
    op.drop_constraint("fk_job_postings_current_version_scope", "job_postings", type_="foreignkey")
    op.drop_table("job_posting_versions")
    op.drop_index("ix_job_postings_company_status", table_name="job_postings")
    op.drop_table("job_postings")
    op.drop_index("uq_skill_aliases_normalized_alias_language", table_name="skill_aliases")
    op.drop_table("skill_aliases")
    op.drop_table("canonical_skills")
