"""add company source provenance and private job-source links

Revision ID: 006_company_sources
Revises: 005_snapshot_recommendation
Create Date: 2026-09-14
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "006_company_sources"
down_revision = "005_snapshot_recommendation"
branch_labels = None
depends_on = None

_STORAGE_POLICY_VALUES = (
    "'FULL_CONTENT_ALLOWED', 'EXCERPT_ONLY', 'METADATA_ONLY', 'DISALLOWED', 'UNKNOWN'"
)
_REUSE_POLICY_VALUES = (
    "'CROSS_USER_ALLOWED', 'SAME_USER_ONLY', 'PROJECT_ONLY', 'DISALLOWED', 'UNKNOWN'"
)
_ACCESS_POLICY_VALUES = "'ALLOWED', 'REVIEW_REQUIRED', 'DISALLOWED', 'UNKNOWN'"
_LAST_COLLECTION_STATUS_VALUES = "'NEVER_COLLECTED', 'PENDING', 'SUCCEEDED', 'PARTIAL', 'FAILED'"
_METADATA_ALLOWED_KEYS = (
    "ARRAY['content_type','http_status','etag','last_modified','charset',"
    "'content_length','parser_warning_codes']::text[]"
)
_DECISION_SCOPE_OWNER_CHECK = (
    "(decision_scope = 'COMPANY_KNOWLEDGE' AND company_id IS NOT NULL "
    "AND question_version_id IS NULL AND decision_owner = 'W3') OR "
    "(decision_scope = 'QUESTION_MATCHING' AND company_id IS NULL "
    "AND question_version_id IS NOT NULL AND decision_owner = 'W4')"
)


def upgrade() -> None:
    op.create_table(
        "company_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("alias_type", sa.String(length=32), nullable=False),
        sa.Column("normalized_alias", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "normalized_alias"),
    )
    op.create_table(
        "company_identifiers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("identifier_type", sa.String(length=32), nullable=False),
        sa.Column("identifier_value", sa.Text(), nullable=False),
        sa.Column("normalized_value", sa.Text(), nullable=False),
        sa.Column("normalization_version", sa.String(length=32), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_company_identifiers_domain",
        "company_identifiers",
        ["identifier_type", "normalized_value"],
        unique=True,
        postgresql_where=sa.text("identifier_type = 'DOMAIN'"),
    )
    op.create_index(
        "uq_company_identifiers_country_bound",
        "company_identifiers",
        ["identifier_type", "country_code", "normalized_value"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=sa.text("identifier_type <> 'DOMAIN'"),
    )
    op.create_table(
        "company_interests",
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("owner_user_id", "company_id"),
    )
    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("canonical_url_hash", sa.String(length=128), nullable=False),
        sa.Column("url_normalization_version", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column(
            "official_status",
            sa.String(length=32),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column(
            "access_policy",
            sa.String(length=32),
            server_default=sa.text("'UNKNOWN'"),
            nullable=False,
        ),
        sa.Column(
            "storage_policy",
            sa.String(length=32),
            server_default=sa.text("'UNKNOWN'"),
            nullable=False,
        ),
        sa.Column(
            "reuse_policy",
            sa.String(length=32),
            server_default=sa.text("'UNKNOWN'"),
            nullable=False,
        ),
        sa.Column("policy_basis", sa.String(length=128), nullable=True),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("policy_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "last_collection_status",
            sa.String(length=32),
            server_default=sa.text("'NEVER_COLLECTED'"),
            nullable=False,
        ),
        sa.Column("first_collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_collected_at", sa.DateTime(timezone=True), nullable=True),
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
            "official_status IN ('VERIFIED', 'PENDING', 'REJECTED')",
            name="official_status_allowed",
        ),
        sa.CheckConstraint(
            "access_policy IN ('ALLOWED', 'REVIEW_REQUIRED', 'DISALLOWED', 'UNKNOWN')",
            name="access_policy_allowed",
        ),
        sa.CheckConstraint(
            f"storage_policy IN ({_STORAGE_POLICY_VALUES})", name="storage_policy_allowed"
        ),
        sa.CheckConstraint(
            f"reuse_policy IN ({_REUSE_POLICY_VALUES})", name="reuse_policy_allowed"
        ),
        sa.CheckConstraint(
            f"last_collection_status IN ({_LAST_COLLECTION_STATUS_VALUES})",
            name="last_collection_status_allowed",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "company_id"),
        sa.UniqueConstraint("company_id", "canonical_url_hash"),
    )
    op.create_table(
        "source_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at_precision", sa.String(length=32), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("content_normalization_version", sa.String(length=64), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("extraction_status", sa.String(length=32), nullable=False),
        sa.Column("access_policy_at_collection", sa.String(length=32), nullable=False),
        sa.Column("storage_policy_at_collection", sa.String(length=32), nullable=False),
        sa.Column("reuse_policy_at_collection", sa.String(length=32), nullable=False),
        sa.Column("policy_version_at_collection", sa.String(length=64), nullable=False),
        sa.Column("normalized_body_ref", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "current_accuracy_status",
            sa.String(length=32),
            server_default=sa.text("'UNVERIFIED'"),
            nullable=False,
        ),
        sa.CheckConstraint("version_no >= 1", name="version_no_positive"),
        sa.CheckConstraint(
            "extraction_status IN ('PENDING', 'SUCCEEDED', 'PARTIAL', 'FAILED')",
            name="extraction_status_allowed",
        ),
        sa.CheckConstraint(
            f"access_policy_at_collection IN ({_ACCESS_POLICY_VALUES})",
            name="access_policy_at_collection_allowed",
        ),
        sa.CheckConstraint(
            f"storage_policy_at_collection IN ({_STORAGE_POLICY_VALUES})",
            name="storage_policy_at_collection_allowed",
        ),
        sa.CheckConstraint(
            f"reuse_policy_at_collection IN ({_REUSE_POLICY_VALUES})",
            name="reuse_policy_at_collection_allowed",
        ),
        sa.CheckConstraint(
            "current_accuracy_status IN ('UNVERIFIED', 'VALID', 'ERROR_CONFIRMED', 'SUPERSEDED')",
            name="current_accuracy_status_allowed",
        ),
        sa.CheckConstraint(
            "storage_policy_at_collection = 'FULL_CONTENT_ALLOWED' OR normalized_body_ref IS NULL",
            name="body_ref_matches_storage_policy",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata) = 'object' AND octet_length(metadata::text) <= 16384",
            name="metadata_is_bounded_object",
        ),
        sa.CheckConstraint(
            f"metadata - {_METADATA_ALLOWED_KEYS} = '{{}}'::jsonb",
            name="metadata_keys_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "company_id"], ["sources.id", "sources.company_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "version_no", name="source_id_version_no"),
        sa.UniqueConstraint("id", "company_id", name="id_company_id"),
        sa.UniqueConstraint("id", "source_id", "company_id", name="id_source_id_company_id"),
        sa.UniqueConstraint("id", "source_id", name="id_source_id"),
    )
    op.create_foreign_key(
        "fk_sources_current_version_scope",
        "sources",
        "source_versions",
        ["current_version_id", "id", "company_id"],
        ["id", "source_id", "company_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_index("ix_source_versions_content_hash", "source_versions", ["content_hash"])
    op.create_table(
        "evidence_spans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_title", sa.Text(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("locator_type", sa.String(length=32), nullable=False),
        sa.Column("locator", sa.String(length=512), nullable=False),
        sa.Column("chunk_order", sa.Integer(), nullable=False),
        sa.Column("excerpt_hash", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("chunk_order >= 0", name="chunk_order_not_negative"),
        sa.ForeignKeyConstraint(["source_version_id"], ["source_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "source_version_id"),
    )
    op.create_index(
        "ix_evidence_spans_source_version_chunk",
        "evidence_spans",
        ["source_version_id", "chunk_order"],
    )
    op.create_table(
        "source_relations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("to_source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relation_type", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "from_source_version_id <> to_source_version_id", name="different_source_versions"
        ),
        sa.ForeignKeyConstraint(
            ["from_source_version_id"], ["source_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["to_source_version_id"], ["source_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("from_source_version_id", "to_source_version_id", "relation_type"),
    )
    op.create_table(
        "job_source_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("purpose_ref", sa.String(length=64), nullable=False),
        sa.Column("analysis_input_version", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_user_id"], ["jobs.id", "jobs.owner_user_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_version_id", "source_id"],
            ["source_versions.id", "source_versions.source_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["command_id", "job_id", "owner_user_id"],
            ["job_commands.id", "job_commands.job_id", "job_commands.owner_user_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "source_id"),
        sa.UniqueConstraint("job_id", "source_id", "purpose_ref"),
    )
    op.create_index(
        "uq_job_source_links_command",
        "job_source_links",
        ["command_id"],
        unique=True,
        postgresql_where=sa.text("command_id IS NOT NULL"),
    )
    op.create_table(
        "analysis_source_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_scope", sa.String(length=32), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("analysis_input_version", sa.String(length=64), nullable=False),
        sa.Column("decision_version", sa.Integer(), nullable=False),
        sa.Column("decision_code", sa.String(length=64), nullable=False),
        sa.Column("decision_owner", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision_scope IN ('COMPANY_KNOWLEDGE', 'QUESTION_MATCHING')",
            name="decision_scope_allowed",
        ),
        sa.CheckConstraint("decision_version >= 1", name="decision_version_positive"),
        sa.CheckConstraint(_DECISION_SCOPE_OWNER_CHECK, name="scope_owner_reference_matches"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["question_version_id"], ["question_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_version_id", "source_id"],
            ["source_versions.id", "source_versions.source_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "decision_scope",
            "company_id",
            "question_version_id",
            "source_id",
            "analysis_input_version",
            "decision_version",
        ),
    )
    op.add_column(
        "job_commands",
        sa.Column("analysis_source_decision_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_job_commands_analysis_source_decision",
        "job_commands",
        "analysis_source_decisions",
        ["analysis_source_decision_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    for table_name in ("company_interests", "job_source_links"):
        _enable_owner_rls(table_name, "owner_user_id")


def downgrade() -> None:
    op.drop_constraint(
        "fk_job_commands_analysis_source_decision", "job_commands", type_="foreignkey"
    )
    op.drop_column("job_commands", "analysis_source_decision_id")
    op.drop_table("analysis_source_decisions")
    op.drop_index("uq_job_source_links_command", table_name="job_source_links")
    op.drop_table("job_source_links")
    op.drop_table("source_relations")
    op.drop_index("ix_evidence_spans_source_version_chunk", table_name="evidence_spans")
    op.drop_table("evidence_spans")
    op.drop_constraint("fk_sources_current_version_scope", "sources", type_="foreignkey")
    op.drop_index("ix_source_versions_content_hash", table_name="source_versions")
    op.drop_table("source_versions")
    op.drop_table("sources")
    op.drop_table("company_interests")
    op.drop_index("uq_company_identifiers_country_bound", table_name="company_identifiers")
    op.drop_index("uq_company_identifiers_domain", table_name="company_identifiers")
    op.drop_table("company_identifiers")
    op.drop_table("company_aliases")


def _enable_owner_rls(table_name: str, owner_column: str) -> None:
    expression = f"{owner_column} = NULLIF(current_setting('app.current_user_id', true), '')::uuid"
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table_name}_owner_policy ON {table_name} "
        f"USING ({expression}) WITH CHECK ({expression})"
    )
