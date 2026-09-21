from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

OFFICIAL_STATUS_VALUES = "'VERIFIED', 'PENDING', 'REJECTED'"
ACCESS_POLICY_VALUES = "'ALLOWED', 'REVIEW_REQUIRED', 'DISALLOWED', 'UNKNOWN'"
STORAGE_POLICY_VALUES = (
    "'FULL_CONTENT_ALLOWED', 'EXCERPT_ONLY', 'METADATA_ONLY', 'DISALLOWED', 'UNKNOWN'"
)
REUSE_POLICY_VALUES = (
    "'CROSS_USER_ALLOWED', 'SAME_USER_ONLY', 'PROJECT_ONLY', 'DISALLOWED', 'UNKNOWN'"
)
LAST_COLLECTION_STATUS_VALUES = "'NEVER_COLLECTED', 'PENDING', 'SUCCEEDED', 'PARTIAL', 'FAILED'"
EXTRACTION_STATUS_VALUES = "'PENDING', 'SUCCEEDED', 'PARTIAL', 'FAILED'"
CURRENT_ACCURACY_STATUS_VALUES = "'UNVERIFIED', 'VALID', 'ERROR_CONFIRMED', 'SUPERSEDED'"
DECISION_SCOPE_VALUES = (
    "'COMPANY_KNOWLEDGE', 'QUESTION_MATCHING', 'DIRECT_SOURCE_REGISTRATION'"
)
ACCESS_RESULT_VALUES = "'ALLOWED', 'DENIED', 'REVIEW_REQUIRED', 'ERROR'"
STORAGE_RESULT_VALUES = "'STORED_FULL', 'STORED_EXCERPT', 'STORED_METADATA', 'NOT_STORED', 'ERROR'"
PARSE_RESULT_VALUES = "'SUCCEEDED', 'PARTIAL', 'FAILED', 'NOT_ATTEMPTED'"
RESULT_COMPLETENESS_VALUES = "'none', 'partial', 'complete'"


class CompanyAlias(Base):
    __tablename__ = "company_aliases"
    __table_args__ = (
        ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        UniqueConstraint("company_id", "normalized_alias", name="company_id_normalized_alias"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    alias: Mapped[str] = mapped_column(Text)
    alias_type: Mapped[str] = mapped_column(String(32))
    normalized_alias: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CompanyIdentifier(Base):
    __tablename__ = "company_identifiers"
    __table_args__ = (ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),)

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    identifier_type: Mapped[str] = mapped_column(String(32))
    identifier_value: Mapped[str] = mapped_column(Text)
    normalized_value: Mapped[str] = mapped_column(Text)
    normalization_version: Mapped[str] = mapped_column(String(32))
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CompanyInterest(Base):
    __tablename__ = "company_interests"
    __table_args__ = (
        ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
    )

    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        UniqueConstraint("id", "company_id", name="id_company_id"),
        UniqueConstraint("company_id", "canonical_url_hash", name="company_id_canonical_url_hash"),
        CheckConstraint(
            f"official_status IN ({OFFICIAL_STATUS_VALUES})", name="official_status_allowed"
        ),
        CheckConstraint(
            f"access_policy IN ({ACCESS_POLICY_VALUES})", name="access_policy_allowed"
        ),
        CheckConstraint(
            f"storage_policy IN ({STORAGE_POLICY_VALUES})", name="storage_policy_allowed"
        ),
        CheckConstraint(f"reuse_policy IN ({REUSE_POLICY_VALUES})", name="reuse_policy_allowed"),
        CheckConstraint(
            f"last_collection_status IN ({LAST_COLLECTION_STATUS_VALUES})",
            name="last_collection_status_allowed",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    source_type: Mapped[str] = mapped_column(String(32))
    canonical_url: Mapped[str] = mapped_column(Text)
    canonical_url_hash: Mapped[str] = mapped_column(String(128))
    url_normalization_version: Mapped[str] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    official_status: Mapped[str] = mapped_column(String(32), server_default="PENDING")
    access_policy: Mapped[str] = mapped_column(String(32), server_default="UNKNOWN")
    storage_policy: Mapped[str] = mapped_column(String(32), server_default="UNKNOWN")
    reuse_policy: Mapped[str] = mapped_column(String(32), server_default="UNKNOWN")
    policy_basis: Mapped[str | None] = mapped_column(String(128), nullable=True)
    policy_version: Mapped[str] = mapped_column(String(64))
    policy_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Circular FK to source_versions is added by migration 006 via a deferred
    # ALTER TABLE; deliberately not declared here (matches the codebase's existing
    # current_version_id convention, e.g. Activity/Episode/ApplicationProject).
    current_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    last_collection_status: Mapped[str] = mapped_column(
        String(32), server_default="NEVER_COLLECTED"
    )
    first_collected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_collected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceVersion(Base):
    __tablename__ = "source_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "company_id"],
            ["sources.id", "sources.company_id"],
            name="source_id_company_id_sources",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("source_id", "version_no", name="source_id_version_no"),
        UniqueConstraint("id", "company_id", name="id_company_id"),
        UniqueConstraint("id", "source_id", "company_id", name="id_source_id_company_id"),
        UniqueConstraint("id", "source_id", name="id_source_id"),
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        CheckConstraint(
            f"extraction_status IN ({EXTRACTION_STATUS_VALUES})", name="extraction_status_allowed"
        ),
        CheckConstraint(
            f"access_policy_at_collection IN ({ACCESS_POLICY_VALUES})",
            name="access_policy_at_collection_allowed",
        ),
        CheckConstraint(
            f"storage_policy_at_collection IN ({STORAGE_POLICY_VALUES})",
            name="storage_policy_at_collection_allowed",
        ),
        CheckConstraint(
            f"reuse_policy_at_collection IN ({REUSE_POLICY_VALUES})",
            name="reuse_policy_at_collection_allowed",
        ),
        CheckConstraint(
            f"current_accuracy_status IN ({CURRENT_ACCURACY_STATUS_VALUES})",
            name="current_accuracy_status_allowed",
        ),
        CheckConstraint(
            "storage_policy_at_collection = 'FULL_CONTENT_ALLOWED' OR normalized_body_ref IS NULL",
            name="body_ref_matches_storage_policy",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    version_no: Mapped[int] = mapped_column(Integer)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at_precision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(128))
    parser_version: Mapped[str] = mapped_column(String(64))
    content_normalization_version: Mapped[str] = mapped_column(String(64))
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    extraction_status: Mapped[str] = mapped_column(String(32))
    access_policy_at_collection: Mapped[str] = mapped_column(String(32))
    storage_policy_at_collection: Mapped[str] = mapped_column(String(32))
    reuse_policy_at_collection: Mapped[str] = mapped_column(String(32))
    policy_version_at_collection: Mapped[str] = mapped_column(String(64))
    normalized_body_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, server_default="{}"
    )
    current_accuracy_status: Mapped[str] = mapped_column(String(32), server_default="UNVERIFIED")


class EvidenceSpan(Base):
    __tablename__ = "evidence_spans"
    __table_args__ = (
        ForeignKeyConstraint(["source_version_id"], ["source_versions.id"], ondelete="CASCADE"),
        UniqueConstraint("id", "source_version_id", name="id_source_version_id"),
        CheckConstraint("chunk_order >= 0", name="chunk_order_not_negative"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    source_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    section_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    excerpt: Mapped[str] = mapped_column(Text)
    locator_type: Mapped[str] = mapped_column(String(32))
    locator: Mapped[str] = mapped_column(String(512))
    chunk_order: Mapped[int] = mapped_column(Integer)
    excerpt_hash: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceRelation(Base):
    __tablename__ = "source_relations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["from_source_version_id"], ["source_versions.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(["to_source_version_id"], ["source_versions.id"], ondelete="RESTRICT"),
        UniqueConstraint(
            "from_source_version_id",
            "to_source_version_id",
            "relation_type",
            name="from_source_version_id_to_source_version_id_relation_type",
        ),
        CheckConstraint(
            "from_source_version_id <> to_source_version_id", name="different_source_versions"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    from_source_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    to_source_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    relation_type: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceCollectionAttempt(Base):
    __tablename__ = "source_collection_attempts"
    __table_args__ = (
        ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["job_source_link_id", "source_id"],
            ["job_source_links.id", "job_source_links.source_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["job_source_link_id", "command_id"],
            ["job_source_links.id", "job_source_links.command_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_version_id", "source_id"],
            ["source_versions.id", "source_versions.source_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "source_id",
            "idempotency_key",
            "attempt_no",
            name="source_id_idempotency_key_attempt_no",
        ),
        CheckConstraint("attempt_no >= 1", name="attempt_no_positive"),
        CheckConstraint(f"access_result IN ({ACCESS_RESULT_VALUES})", name="access_result_allowed"),
        CheckConstraint(
            f"storage_result IN ({STORAGE_RESULT_VALUES})", name="storage_result_allowed"
        ),
        CheckConstraint(f"parse_result IN ({PARSE_RESULT_VALUES})", name="parse_result_allowed"),
        CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599", name="http_status_range"
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at", name="completed_after_started"
        ),
        CheckConstraint(
            "command_id IS NULL OR job_source_link_id IS NOT NULL", name="command_requires_link"
        ),
        CheckConstraint(
            f"result_completeness IS NULL OR result_completeness IN ({RESULT_COMPLETENESS_VALUES})",
            name="result_completeness_allowed",
        ),
        CheckConstraint(
            "safe_failure_message IS NULL OR octet_length(safe_failure_message) <= 1024",
            name="safe_failure_message_bounded",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    source_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    job_source_link_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    command_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    attempt_no: Mapped[int] = mapped_column(Integer)
    access_result: Mapped[str] = mapped_column(String(32))
    storage_result: Mapped[str] = mapped_column(String(32))
    parse_result: Mapped[str] = mapped_column(String(32))
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy_version: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    command_schema_version: Mapped[str] = mapped_column(String(32))
    result_schema_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result_completeness: Mapped[str | None] = mapped_column(String(16), nullable=True)
    restriction_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    safe_failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class JobSourceLink(Base):
    __tablename__ = "job_source_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "owner_user_id"], ["jobs.id", "jobs.owner_user_id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["source_version_id", "source_id"],
            ["source_versions.id", "source_versions.source_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["command_id", "job_id", "owner_user_id"],
            ["job_commands.id", "job_commands.job_id", "job_commands.owner_user_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "source_id", name="id_source_id"),
        UniqueConstraint("id", "command_id", name="id_command_id"),
        Index(
            "uq_job_source_links_unbound",
            "job_id",
            "source_id",
            "purpose_ref",
            unique=True,
            postgresql_where=text("command_id IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    source_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    source_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    command_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    purpose_ref: Mapped[str] = mapped_column(String(64))
    analysis_input_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalysisSourceDecision(Base):
    # Accepted W3 events are linked from ``JobCoreDecisionBinding`` through a
    # SQL-owned FK. Keep the existing no-relationship convention used by
    # JobCommand so replay/audit rows are loaded explicitly under lock.
    __tablename__ = "analysis_source_decisions"
    __table_args__ = (
        ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["question_version_id"], ["question_versions.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["source_version_id", "source_id"],
            ["source_versions.id", "source_versions.source_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "decision_scope",
            "company_id",
            "question_version_id",
            "source_id",
            "analysis_input_version",
            "decision_version",
            name="decision_scope_company_id_question_version_id_source_id_ana",
        ),
        CheckConstraint(
            f"decision_scope IN ({DECISION_SCOPE_VALUES})", name="decision_scope_allowed"
        ),
        CheckConstraint("decision_version >= 1", name="decision_version_positive"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    decision_scope: Mapped[str] = mapped_column(String(32))
    company_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    question_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    source_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    source_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    analysis_input_version: Mapped[str] = mapped_column(String(64))
    decision_version: Mapped[int] = mapped_column(Integer)
    decision_code: Mapped[str] = mapped_column(String(64))
    decision_owner: Mapped[str] = mapped_column(String(16))
    reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
