"""Add the W2 source collection persistence model.

Revision ID: 0001_source_collection_core
Revises:
Create Date: 2026-09-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_source_collection_core"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("legal_name", sa.Text(), nullable=False),
        sa.Column("aliases", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("official_domains", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("legal_identifiers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("identity_status", sa.String(length=32), nullable=False),
        sa.Column("identity_evidence", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("company_id", name="pk_companies"),
    )
    op.create_table(
        "company_relationships",
        sa.Column("relationship_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("related_company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("evidence_ref", sa.Text(), nullable=False),
        sa.Column("valid_from", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("valid_to", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "company_id <> related_company_id",
            name=op.f("ck_company_relationships_different_companies"),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.company_id"],
            name="fk_company_relationships_company_id",
        ),
        sa.ForeignKeyConstraint(
            ["related_company_id"],
            ["companies.company_id"],
            name="fk_company_relationships_related_company_id",
        ),
        sa.PrimaryKeyConstraint("relationship_id", name="pk_company_relationships"),
    )
    op.create_index(
        "ix_company_relationships_company_id",
        "company_relationships",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        "ix_company_relationships_related_company_id",
        "company_relationships",
        ["related_company_id"],
        unique=False,
    )

    op.create_table(
        "sources",
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("latest_observation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("current_source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("first_collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.company_id"],
            name="fk_sources_company_id",
        ),
        sa.PrimaryKeyConstraint("source_id", name="pk_sources"),
        sa.UniqueConstraint("company_id", "canonical_url", name="uq_sources_company_url"),
        sa.UniqueConstraint("source_id", "company_id", name="uq_sources_id_company"),
    )
    op.create_index(
        "ix_sources_company_id_source_id",
        "sources",
        ["company_id", "source_id"],
        unique=False,
    )

    op.create_table(
        "source_policy_decisions",
        sa.Column("policy_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("official_status", sa.String(length=32), nullable=False),
        sa.Column("access_class", sa.String(length=32), nullable=False),
        sa.Column("collection_permission", sa.String(length=16), nullable=False),
        sa.Column("excerpt_storage_permission", sa.String(length=16), nullable=False),
        sa.Column("body_storage_permission", sa.String(length=16), nullable=False),
        sa.Column("redistribution_permission", sa.String(length=16), nullable=False),
        sa.Column("evidence_refs", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "revision > 0",
            name=op.f("ck_source_policy_decisions_positive_revision"),
        ),
        sa.CheckConstraint(
            "official_status IN ('verified', 'unverified', 'rejected')",
            name=op.f("ck_source_policy_decisions_valid_official_status"),
        ),
        sa.CheckConstraint(
            "access_class IN ('public', 'restricted', 'unavailable', 'unknown')",
            name=op.f("ck_source_policy_decisions_valid_access_class"),
        ),
        sa.CheckConstraint(
            "collection_permission IN ('allowed', 'denied', 'unknown')",
            name=op.f("ck_source_policy_decisions_valid_collection_permission"),
        ),
        sa.CheckConstraint(
            "excerpt_storage_permission IN ('allowed', 'denied', 'unknown')",
            name=op.f("ck_source_policy_decisions_valid_excerpt_storage_permission"),
        ),
        sa.CheckConstraint(
            "body_storage_permission IN ('allowed', 'denied', 'unknown')",
            name=op.f("ck_source_policy_decisions_valid_body_storage_permission"),
        ),
        sa.CheckConstraint(
            "redistribution_permission IN ('allowed', 'denied', 'unknown')",
            name=op.f("ck_source_policy_decisions_valid_redistribution_permission"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.source_id"],
            name="fk_source_policy_decisions_source_id",
        ),
        sa.PrimaryKeyConstraint("policy_decision_id", name="pk_source_policy_decisions"),
        sa.UniqueConstraint(
            "source_id",
            "revision",
            name="uq_source_policy_decisions_source_revision",
        ),
        sa.UniqueConstraint(
            "policy_decision_id",
            "source_id",
            name="uq_source_policy_decisions_id_source",
        ),
    )

    op.create_table(
        "source_versions",
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("hash_profile_version", sa.String(length=128), nullable=False),
        sa.Column("representation", sa.String(length=32), nullable=False),
        sa.Column("first_parser_version", sa.String(length=128), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("valid_from", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("valid_to", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=True),
        sa.Column("policy_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "length(content_hash) > 0",
            name=op.f("ck_source_versions_nonempty_content_hash"),
        ),
        sa.CheckConstraint(
            "length(hash_profile_version) > 0",
            name=op.f("ck_source_versions_nonempty_hash_profile_version"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "company_id"],
            ["sources.source_id", "sources.company_id"],
            name="fk_source_versions_source_company",
        ),
        sa.ForeignKeyConstraint(
            ["policy_decision_id", "source_id"],
            [
                "source_policy_decisions.policy_decision_id",
                "source_policy_decisions.source_id",
            ],
            name="fk_source_versions_policy_same_source",
        ),
        sa.PrimaryKeyConstraint("source_version_id", name="pk_source_versions"),
        sa.UniqueConstraint(
            "source_id",
            "representation",
            "hash_profile_version",
            "content_hash",
            name="uq_source_versions_content",
        ),
        sa.UniqueConstraint(
            "source_version_id",
            "source_id",
            name="uq_source_versions_id_source",
        ),
    )
    op.create_index(
        "ix_source_versions_source_id_collected_at",
        "source_versions",
        ["source_id", "collected_at", "source_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_source_versions_company_id_source_id",
        "source_versions",
        ["company_id", "source_id"],
        unique=False,
    )

    op.create_table(
        "source_observations",
        sa.Column("observation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_decision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("access_class", sa.String(length=32), nullable=False),
        sa.Column("acquisition_status", sa.String(length=32), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("checked_url", sa.Text(), nullable=False),
        sa.Column("retrieval_validator", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("representation", sa.String(length=32), nullable=True),
        sa.CheckConstraint(
            "access_class IN ('public', 'restricted', 'unavailable', 'unknown')",
            name=op.f("ck_source_observations_valid_access_class"),
        ),
        sa.CheckConstraint(
            "acquisition_status IS NULL OR acquisition_status IN "
            "('AVAILABLE', 'PARTIALLY_EXTRACTED', 'ACCESS_DENIED', 'NOT_FOUND', "
            "'RATE_LIMITED', 'EXTRACTION_FAILED')",
            name=op.f("ck_source_observations_valid_acquisition_status"),
        ),
        sa.CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599",
            name=op.f("ck_source_observations_valid_http_status"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.source_id"],
            name="fk_source_observations_source_id",
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id", "source_id"],
            ["source_versions.source_version_id", "source_versions.source_id"],
            name="fk_source_observations_version_same_source",
        ),
        sa.ForeignKeyConstraint(
            ["policy_decision_id", "source_id"],
            [
                "source_policy_decisions.policy_decision_id",
                "source_policy_decisions.source_id",
            ],
            name="fk_source_observations_policy_same_source",
        ),
        sa.PrimaryKeyConstraint("observation_id", name="pk_source_observations"),
        sa.UniqueConstraint(
            "observation_id",
            "source_id",
            name="uq_source_observations_id_source",
        ),
    )
    op.create_index(
        "ix_source_observations_source_id_observed_at",
        "source_observations",
        ["source_id", "observed_at", "observation_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_sources_latest_observation_same_source",
        "sources",
        "source_observations",
        ["latest_observation_id", "source_id"],
        ["observation_id", "source_id"],
    )
    op.create_foreign_key(
        "fk_sources_current_version_same_source",
        "sources",
        "source_versions",
        ["current_source_version_id", "source_id"],
        ["source_version_id", "source_id"],
    )

    op.create_table(
        "extraction_revisions",
        sa.Column("extraction_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parser_version", sa.String(length=128), nullable=False),
        sa.Column("output_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("extraction_status", sa.String(length=32), nullable=False),
        sa.Column("posting_sections", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("date_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("limitations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "extraction_status IN ('complete', 'partial', 'failed', 'not_attempted')",
            name=op.f("ck_extraction_revisions_valid_extraction_status"),
        ),
        sa.CheckConstraint(
            "length(output_hash) > 0",
            name=op.f("ck_extraction_revisions_nonempty_output_hash"),
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["source_versions.source_version_id"],
            name="fk_extraction_revisions_version",
        ),
        sa.PrimaryKeyConstraint("extraction_revision_id", name="pk_extraction_revisions"),
        sa.UniqueConstraint(
            "source_version_id",
            "output_hash",
            name="uq_extraction_revisions_output",
        ),
        sa.UniqueConstraint(
            "extraction_revision_id",
            "source_version_id",
            name="uq_extraction_revisions_id_version",
        ),
    )
    op.create_index(
        "ix_extraction_revisions_source_version_id_created_at",
        "extraction_revisions",
        ["source_version_id", "created_at", "extraction_revision_id"],
        unique=False,
    )

    op.create_table(
        "parser_executions",
        sa.Column("parser_execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("parser_version", sa.String(length=128), nullable=False),
        sa.Column("output_hash", sa.String(length=128), nullable=True),
        sa.Column("extraction_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "extraction_revision_id IS NULL OR source_version_id IS NOT NULL",
            name=op.f("ck_parser_executions_revision_requires_version"),
        ),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed')",
            name=op.f("ck_parser_executions_valid_status"),
        ),
        sa.CheckConstraint(
            "status <> 'succeeded' OR "
            "(output_hash IS NOT NULL AND extraction_revision_id IS NOT NULL)",
            name=op.f("ck_parser_executions_succeeded_requires_output_and_revision"),
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR (output_hash IS NULL AND extraction_revision_id IS NULL)",
            name=op.f("ck_parser_executions_failed_has_no_output_or_revision"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.source_id"],
            name="fk_parser_executions_source_id",
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id", "source_id"],
            ["source_versions.source_version_id", "source_versions.source_id"],
            name="fk_parser_executions_version_same_source",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_revision_id", "source_version_id"],
            [
                "extraction_revisions.extraction_revision_id",
                "extraction_revisions.source_version_id",
            ],
            name="fk_parser_executions_revision_same_version",
        ),
        sa.PrimaryKeyConstraint("parser_execution_id", name="pk_parser_executions"),
    )
    op.create_index(
        "ix_parser_executions_source_id_executed_at",
        "parser_executions",
        ["source_id", "executed_at", "parser_execution_id"],
        unique=False,
    )
    op.create_index(
        "ix_parser_executions_source_version_id",
        "parser_executions",
        ["source_version_id"],
        unique=False,
    )

    op.create_table(
        "evidence",
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_key", sa.String(length=256), nullable=False),
        sa.Column("section_title", sa.Text(), nullable=True),
        sa.Column("text_excerpt", sa.Text(), nullable=False),
        sa.Column("locator", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("chunk_order", sa.Integer(), nullable=False),
        sa.Column("origin_kind", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "length(text_excerpt) > 0",
            name=op.f("ck_evidence_nonempty_text_excerpt"),
        ),
        sa.CheckConstraint(
            "chunk_order >= 0",
            name=op.f("ck_evidence_nonnegative_chunk_order"),
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["source_versions.source_version_id"],
            name="fk_evidence_source_version_id",
        ),
        sa.PrimaryKeyConstraint("evidence_id", name="pk_evidence"),
        sa.UniqueConstraint(
            "source_version_id",
            "evidence_key",
            name="uq_evidence_version_key",
        ),
        sa.UniqueConstraint(
            "evidence_id",
            "source_version_id",
            name="uq_evidence_id_version",
        ),
    )
    op.create_index(
        "ix_evidence_source_version_id_chunk_order",
        "evidence",
        ["source_version_id", "chunk_order", "evidence_id"],
        unique=False,
    )

    op.create_table(
        "extraction_revision_evidence",
        sa.Column("extraction_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["extraction_revision_id", "source_version_id"],
            [
                "extraction_revisions.extraction_revision_id",
                "extraction_revisions.source_version_id",
            ],
            name="fk_extraction_revision_evidence_revision_version",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id", "source_version_id"],
            ["evidence.evidence_id", "evidence.source_version_id"],
            name="fk_extraction_revision_evidence_evidence_version",
        ),
        sa.PrimaryKeyConstraint(
            "extraction_revision_id",
            "evidence_id",
            name="pk_extraction_revision_evidence",
        ),
    )
    op.create_index(
        "ix_extraction_revision_evidence_source_version_id",
        "extraction_revision_evidence",
        ["source_version_id"],
        unique=False,
    )

    op.create_table(
        "collection_attempts",
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("input_version", sa.Integer(), nullable=False),
        sa.Column("target_ref", sa.Text(), nullable=False),
        sa.Column("purpose_ref", sa.Text(), nullable=False),
        sa.Column("core_source_decision", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resume_stage", sa.String(length=32), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=True),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("execution_fence", sa.String(length=256), nullable=False),
        sa.Column("owner_deletion_epoch", sa.Integer(), nullable=False),
        sa.Column("parser_execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("checkpoint_ref", sa.Text(), nullable=True),
        sa.Column("result_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("failures", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("required_actions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "input_version > 0",
            name=op.f("ck_collection_attempts_positive_input_version"),
        ),
        sa.CheckConstraint(
            "result_version > 0",
            name=op.f("ck_collection_attempts_positive_result_version"),
        ),
        sa.CheckConstraint(
            "policy_revision IS NULL OR policy_revision > 0",
            name=op.f("ck_collection_attempts_positive_policy_revision"),
        ),
        sa.CheckConstraint(
            "resume_stage = 'policy' OR policy_revision IS NOT NULL",
            name=op.f("ck_collection_attempts_policy_revision_required_after_policy"),
        ),
        sa.CheckConstraint(
            "owner_deletion_epoch >= 0",
            name=op.f("ck_collection_attempts_nonnegative_deletion_epoch"),
        ),
        sa.CheckConstraint(
            "length(btrim(execution_fence)) > 0",
            name=op.f("ck_collection_attempts_nonempty_execution_fence"),
        ),
        sa.CheckConstraint(
            "(result_payload IS NULL) = (finalized_at IS NULL)",
            name=op.f("ck_collection_attempts_result_payload_finalized_together"),
        ),
        sa.ForeignKeyConstraint(
            ["parser_execution_id"],
            ["parser_executions.parser_execution_id"],
            name="fk_collection_attempts_parser_execution_id",
        ),
        sa.PrimaryKeyConstraint("attempt_id", name="pk_collection_attempts"),
        sa.UniqueConstraint(
            "owner_user_id",
            "command_id",
            name="uq_collection_attempts_owner_command",
        ),
    )
    op.create_index(
        "ix_collection_attempts_owner_job",
        "collection_attempts",
        ["owner_user_id", "job_id", "attempt_id"],
        unique=False,
    )
    op.create_index(
        "ix_collection_attempts_parser_execution_id",
        "collection_attempts",
        ["parser_execution_id"],
        unique=False,
    )

    op.create_table(
        "request_deduplications",
        sa.Column("request_deduplication_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation", sa.String(length=256), nullable=False),
        sa.Column("idempotency_key", sa.String(length=256), nullable=False),
        sa.Column("request_hash", sa.String(length=128), nullable=False),
        sa.Column("accepted_resource_ref", sa.Text(), nullable=False),
        sa.Column("input_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "input_version > 0",
            name=op.f("ck_request_deduplications_positive_input_version"),
        ),
        sa.PrimaryKeyConstraint(
            "request_deduplication_id",
            name="pk_request_deduplications",
        ),
        sa.UniqueConstraint(
            "owner_user_id",
            "operation",
            "idempotency_key",
            name="uq_request_deduplications_owner_operation_key",
        ),
    )

    op.create_table(
        "outbox_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("aggregate_revision", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivery_state", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "aggregate_revision > 0",
            name=op.f("ck_outbox_events_positive_aggregate_revision"),
        ),
        sa.ForeignKeyConstraint(
            ["aggregate_id"],
            ["sources.source_id"],
            name="fk_outbox_events_aggregate_id",
        ),
        sa.PrimaryKeyConstraint("event_id", name="pk_outbox_events"),
        sa.UniqueConstraint(
            "aggregate_id",
            "aggregate_revision",
            name="uq_outbox_events_aggregate_revision",
        ),
    )
    op.create_index(
        "ix_outbox_events_delivery_occurred",
        "outbox_events",
        ["delivery_state", "occurred_at", "event_id"],
        unique=False,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Destructive downgrade is intentionally unsupported; use a forward corrective migration."
    )
