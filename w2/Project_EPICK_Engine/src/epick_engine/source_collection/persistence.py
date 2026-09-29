"""Synchronous PostgreSQL connection boundary for W2 source collection."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Engine,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    and_,
    create_engine,
    delete,
    exists,
    func,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from epick_engine.source_collection.commit_gate_contracts import (
    CommitGateAckProposal,
    StagedResultProposal,
)
from epick_engine.source_collection.contracts import (
    AccuracyStatus,
    CollectionCommand,
    CollectionResult,
    CollectionStage,
    DateValue,
    ExtractionStatus,
    Locator,
    Representation,
    RestrictionStatus,
    RetentionScope,
    SourceEnvelope,
    SourceEvent,
    SourceEventType,
    SourceObservationSnapshot,
    SourceRestrictionSnapshot,
    SourceType,
)
from epick_engine.source_collection.contracts import (
    PostingSection as PostingSectionValue,
)
from epick_engine.source_collection.w1_transport import W1Dispatch

if TYPE_CHECKING:
    from epick_engine.source_collection.private_deletion_v2 import PrivateDeletionCommandV2
    from epick_engine.source_collection.private_scope import PrivateWriteScope
    from epick_engine.source_collection.worker import PrivateDeletionCommand

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative metadata for the W2 PostgreSQL persistence boundary."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Company(Base):
    __tablename__ = "companies"

    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    legal_name: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    official_domains: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    legal_identifiers: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    identity_status: Mapped[str] = mapped_column(String(32), nullable=False)
    identity_evidence: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)


class CompanyRelationship(Base):
    __tablename__ = "company_relationships"
    __table_args__ = (
        CheckConstraint(
            "company_id <> related_company_id",
            name="different_companies",
        ),
        Index("ix_company_relationships_company_id", "company_id"),
        Index("ix_company_relationships_related_company_id", "related_company_id"),
    )

    relationship_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("companies.company_id", name="fk_company_relationships_company_id"),
        nullable=False,
    )
    related_company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "companies.company_id",
            name="fk_company_relationships_related_company_id",
        ),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    valid_to: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("company_id", "canonical_url", name="uq_sources_company_url"),
        UniqueConstraint("source_id", "company_id", name="uq_sources_id_company"),
        ForeignKeyConstraint(
            ["latest_observation_id", "source_id"],
            ["source_observations.observation_id", "source_observations.source_id"],
            name="fk_sources_latest_observation_same_source",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["current_source_version_id", "source_id"],
            ["source_versions.source_version_id", "source_versions.source_id"],
            name="fk_sources_current_version_same_source",
            use_alter=True,
        ),
        CheckConstraint(
            "pointer_update_mode IN ('LEGACY_SAME_DB', 'FINALIZE_GATE')",
            name="valid_pointer_update_mode",
        ),
        CheckConstraint(
            "0 <= last_promoted_observation_order "
            "AND last_promoted_observation_order <= next_observation_order",
            name="valid_observation_order_counters",
        ),
        Index("ix_sources_company_id_source_id", "company_id", "source_id"),
    )

    source_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("companies.company_id", name="fk_sources_company_id"),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_observation_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=True,
    )
    current_source_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=True,
    )
    first_collected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_collected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    pointer_update_mode: Mapped[str] = mapped_column(
        String(32),
        default="LEGACY_SAME_DB",
        server_default=text("'LEGACY_SAME_DB'"),
        nullable=False,
    )
    next_observation_order: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        server_default=text("0"),
        nullable=False,
    )
    last_promoted_observation_order: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        server_default=text("0"),
        nullable=False,
    )


class JobPosting(Base):
    __tablename__ = "job_postings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "company_id"],
            ["sources.source_id", "sources.company_id"],
            name="fk_job_postings_source_company",
        ),
        UniqueConstraint("source_id", name="uq_job_postings_source_id"),
    )

    job_posting_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)


class SourcePolicyDecision(Base):
    __tablename__ = "source_policy_decisions"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "revision",
            name="uq_source_policy_decisions_source_revision",
        ),
        UniqueConstraint(
            "policy_decision_id",
            "source_id",
            name="uq_source_policy_decisions_id_source",
        ),
        CheckConstraint("revision > 0", name="positive_revision"),
        CheckConstraint(
            "official_status IN ('verified', 'unverified', 'rejected')",
            name="valid_official_status",
        ),
        CheckConstraint(
            "access_class IN ('public', 'restricted', 'unavailable', 'unknown')",
            name="valid_access_class",
        ),
        CheckConstraint(
            "collection_permission IN ('allowed', 'denied', 'unknown')",
            name="valid_collection_permission",
        ),
        CheckConstraint(
            "excerpt_storage_permission IN ('allowed', 'denied', 'unknown')",
            name="valid_excerpt_storage_permission",
        ),
        CheckConstraint(
            "body_storage_permission IN ('allowed', 'denied', 'unknown')",
            name="valid_body_storage_permission",
        ),
        CheckConstraint(
            "redistribution_permission IN ('allowed', 'denied', 'unknown')",
            name="valid_redistribution_permission",
        ),
    )

    policy_decision_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    source_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("sources.source_id", name="fk_source_policy_decisions_source_id"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    official_status: Mapped[str] = mapped_column(String(32), nullable=False)
    access_class: Mapped[str] = mapped_column(String(32), nullable=False)
    collection_permission: Mapped[str] = mapped_column(String(16), nullable=False)
    excerpt_storage_permission: Mapped[str] = mapped_column(String(16), nullable=False)
    body_storage_permission: Mapped[str] = mapped_column(String(16), nullable=False)
    redistribution_permission: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(128), nullable=False)


class SourceVersion(Base):
    __tablename__ = "source_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "company_id"],
            ["sources.source_id", "sources.company_id"],
            name="fk_source_versions_source_company",
        ),
        ForeignKeyConstraint(
            ["policy_decision_id", "source_id"],
            [
                "source_policy_decisions.policy_decision_id",
                "source_policy_decisions.source_id",
            ],
            name="fk_source_versions_policy_same_source",
        ),
        UniqueConstraint(
            "source_id",
            "representation",
            "hash_profile_version",
            "content_hash",
            name="uq_source_versions_content",
        ),
        UniqueConstraint(
            "source_version_id",
            "source_id",
            name="uq_source_versions_id_source",
        ),
        CheckConstraint("length(content_hash) > 0", name="nonempty_content_hash"),
        CheckConstraint(
            "length(hash_profile_version) > 0",
            name="nonempty_hash_profile_version",
        ),
        Index(
            "ix_source_versions_source_id_collected_at",
            "source_id",
            "collected_at",
            "source_version_id",
        ),
        Index("ix_source_versions_company_id_source_id", "company_id", "source_id"),
    )

    source_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    source_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    hash_profile_version: Mapped[str] = mapped_column(String(128), nullable=False)
    representation: Mapped[str] = mapped_column(String(32), nullable=False)
    first_parser_version: Mapped[str] = mapped_column(String(128), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    valid_from: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    valid_to: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_decision_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)


class SourceRestrictionIdentity(Base):
    """Stable Source/Version scope for one internal restriction identity."""

    __tablename__ = "source_restriction_identities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_version_id", "source_id"],
            ["source_versions.source_version_id", "source_versions.source_id"],
            name="fk_source_restriction_identities_version_same_source",
        ),
        UniqueConstraint(
            "restriction_id",
            "source_id",
            name="uq_source_restriction_identities_id_source",
        ),
        Index(
            "ix_source_restriction_identities_source_id_restriction_id",
            "source_id",
            "restriction_id",
        ),
    )

    restriction_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    source_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("sources.source_id", name="fk_source_restriction_identities_source_id"),
        nullable=False,
    )
    source_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=True,
    )


class SourceRestriction(Base):
    """One immutable revision in a stable restriction identity's history."""

    __tablename__ = "source_restrictions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["restriction_id", "source_id"],
            [
                "source_restriction_identities.restriction_id",
                "source_restriction_identities.source_id",
            ],
            name="fk_source_restrictions_identity_same_source",
        ),
        UniqueConstraint(
            "source_id",
            "restriction_revision",
            name="uq_source_restrictions_source_revision",
        ),
        CheckConstraint("restriction_revision > 0", name="positive_restriction_revision"),
        CheckConstraint(
            "restriction_status IN ('active', 'cleared')",
            name="valid_restriction_status",
        ),
        CheckConstraint(
            "accuracy_status IN "
            "('unverified', 'verified_in_scope', 'error_confirmed', 'superseded')",
            name="valid_accuracy_status",
        ),
        CheckConstraint("length(btrim(reason_code)) > 0", name="nonempty_reason_code"),
    )

    restriction_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    restriction_revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    restriction_status: Mapped[str] = mapped_column(String(32), nullable=False)
    accuracy_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_refs: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    replacement_ref: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("sources.source_id", name="fk_source_restrictions_replacement_ref"),
        nullable=True,
    )


class RestrictionMutationReceipt(Base):
    """Private idempotency receipt bound to one immutable restriction revision."""

    __tablename__ = "restriction_mutation_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["restriction_id", "restriction_revision"],
            ["source_restrictions.restriction_id", "source_restrictions.restriction_revision"],
            name="fk_restriction_mutation_receipts_restriction_history",
        ),
        CheckConstraint("btrim(authority_ref) <> ''", name="nonempty_authority_ref"),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="valid_request_hash",
        ),
        CheckConstraint("restriction_revision > 0", name="positive_restriction_revision"),
    )

    authority_ref: Mapped[str] = mapped_column(Text, primary_key=True)
    request_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    restriction_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    restriction_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RetainedBody(Base):
    __tablename__ = "retained_bodies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_version_id", "source_id"],
            ["source_versions.source_version_id", "source_versions.source_id"],
            name="fk_retained_bodies_version_same_source",
        ),
        ForeignKeyConstraint(
            ["policy_decision_id", "source_id"],
            [
                "source_policy_decisions.policy_decision_id",
                "source_policy_decisions.source_id",
            ],
            name="fk_retained_bodies_policy_same_source",
        ),
        UniqueConstraint(
            "source_version_id",
            "normalization_version",
            name="uq_retained_bodies_version_normalization",
        ),
        CheckConstraint(
            "length(btrim(normalization_version)) > 0",
            name="nonempty_normalization_version",
        ),
        CheckConstraint("length(btrim(body_text)) > 0", name="nonempty_body_text"),
        CheckConstraint(
            "length(btrim(necessity_reason)) > 0",
            name="nonempty_necessity_reason",
        ),
        CheckConstraint(
            "length(btrim(retention_policy_version)) > 0",
            name="nonempty_retention_policy_version",
        ),
        CheckConstraint(
            "retention_limit_bytes > 0",
            name="positive_retention_limit_bytes",
        ),
        CheckConstraint(
            "octet_length(body_text) BETWEEN 1 AND retention_limit_bytes",
            name="body_within_retention_limit",
        ),
    )

    body_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    source_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    normalization_version: Mapped[str] = mapped_column(String(128), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    necessity_reason: Mapped[str] = mapped_column(Text, nullable=False)
    policy_decision_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    retained_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retention_policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    retention_limit_bytes: Mapped[int] = mapped_column(Integer, nullable=False)


class SourceOrigin(Base):
    __tablename__ = "source_origins"
    __table_args__ = (
        CheckConstraint(
            "origin_source_id IS NOT NULL OR origin_url IS NOT NULL",
            name="has_target",
        ),
        CheckConstraint(
            "origin_source_id IS NULL OR origin_source_id <> source_id",
            name="not_self_origin",
        ),
        CheckConstraint(
            "origin_url IS NULL OR length(btrim(origin_url)) > 0",
            name="nonempty_origin_url",
        ),
        CheckConstraint(
            "length(btrim(relationship_kind)) > 0",
            name="nonempty_relationship_kind",
        ),
        CheckConstraint(
            "length(btrim(verification_status)) > 0",
            name="nonempty_verification_status",
        ),
        Index(
            "uq_source_origins_source_kind_origin_source",
            "source_id",
            "relationship_kind",
            "origin_source_id",
            unique=True,
            postgresql_where=text("origin_source_id IS NOT NULL"),
        ),
        Index(
            "uq_source_origins_source_kind_origin_url",
            "source_id",
            "relationship_kind",
            "origin_url",
            unique=True,
            postgresql_where=text("origin_url IS NOT NULL"),
        ),
    )

    origin_relation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    source_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("sources.source_id", name="fk_source_origins_source_id"),
        nullable=False,
    )
    origin_source_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("sources.source_id", name="fk_source_origins_origin_source_id"),
        nullable=True,
    )
    origin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    relationship_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(64), nullable=False)


class SourceObservation(Base):
    __tablename__ = "source_observations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_version_id", "source_id"],
            ["source_versions.source_version_id", "source_versions.source_id"],
            name="fk_source_observations_version_same_source",
        ),
        ForeignKeyConstraint(
            ["policy_decision_id", "source_id"],
            [
                "source_policy_decisions.policy_decision_id",
                "source_policy_decisions.source_id",
            ],
            name="fk_source_observations_policy_same_source",
        ),
        UniqueConstraint(
            "observation_id",
            "source_id",
            name="uq_source_observations_id_source",
        ),
        CheckConstraint(
            "access_class IN ('public', 'restricted', 'unavailable', 'unknown')",
            name="valid_access_class",
        ),
        CheckConstraint(
            "acquisition_status IS NULL OR acquisition_status IN "
            "('AVAILABLE', 'PARTIALLY_EXTRACTED', 'ACCESS_DENIED', 'NOT_FOUND', "
            "'RATE_LIMITED', 'EXTRACTION_FAILED')",
            name="valid_acquisition_status",
        ),
        CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599",
            name="valid_http_status",
        ),
        Index(
            "ix_source_observations_source_id_observed_at",
            "source_id",
            "observed_at",
            "observation_id",
        ),
    )

    observation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    source_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("sources.source_id", name="fk_source_observations_source_id"),
        nullable=False,
    )
    source_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=True,
    )
    policy_decision_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=True,
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    access_class: Mapped[str] = mapped_column(String(32), nullable=False)
    acquisition_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checked_url: Mapped[str] = mapped_column(Text, nullable=False)
    retrieval_validator: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    representation: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ExtractionRevision(Base):
    __tablename__ = "extraction_revisions"
    __table_args__ = (
        UniqueConstraint(
            "source_version_id",
            "output_hash",
            name="uq_extraction_revisions_output",
        ),
        UniqueConstraint(
            "extraction_revision_id",
            "source_version_id",
            name="uq_extraction_revisions_id_version",
        ),
        CheckConstraint(
            "extraction_status IN ('complete', 'partial', 'failed', 'not_attempted')",
            name="valid_extraction_status",
        ),
        CheckConstraint("length(output_hash) > 0", name="nonempty_output_hash"),
        Index(
            "ix_extraction_revisions_source_version_id_created_at",
            "source_version_id",
            "created_at",
            "extraction_revision_id",
        ),
    )

    extraction_revision_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    source_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("source_versions.source_version_id", name="fk_extraction_revisions_version"),
        nullable=False,
    )
    parser_version: Mapped[str] = mapped_column(String(128), nullable=False)
    output_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    extraction_status: Mapped[str] = mapped_column(String(32), nullable=False)
    posting_sections: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    date_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    limitations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)


class PostingSection(Base):
    __tablename__ = "posting_sections"
    __table_args__ = (
        UniqueConstraint(
            "extraction_revision_id",
            "section_order",
            name="uq_posting_sections_revision_order",
        ),
        CheckConstraint("length(section_key) > 0", name="nonempty_section_key"),
        CheckConstraint(
            "kind IN ('title', 'role', 'organization', 'duties', 'required', "
            "'preferred', 'general', 'location', 'employment_type', 'published', "
            "'deadline')",
            name="valid_kind",
        ),
        CheckConstraint(
            "heading_raw IS NULL OR length(heading_raw) > 0",
            name="nonempty_heading_raw",
        ),
        CheckConstraint("length(text_raw) > 0", name="nonempty_text_raw"),
        CheckConstraint("section_order >= 0", name="nonnegative_section_order"),
        CheckConstraint(
            "relation_text IS NULL OR length(relation_text) > 0",
            name="nonempty_relation_text",
        ),
    )

    extraction_revision_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "extraction_revisions.extraction_revision_id",
            name="fk_posting_sections_extraction_revision_id",
        ),
        primary_key=True,
    )
    section_key: Mapped[str] = mapped_column(Text, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    heading_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_raw: Mapped[str] = mapped_column(Text, nullable=False)
    order: Mapped[int] = mapped_column("section_order", Integer, nullable=False)
    relation_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class ParserExecution(Base):
    __tablename__ = "parser_executions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_version_id", "source_id"],
            ["source_versions.source_version_id", "source_versions.source_id"],
            name="fk_parser_executions_version_same_source",
        ),
        ForeignKeyConstraint(
            ["extraction_revision_id", "source_version_id"],
            [
                "extraction_revisions.extraction_revision_id",
                "extraction_revisions.source_version_id",
            ],
            name="fk_parser_executions_revision_same_version",
        ),
        CheckConstraint(
            "extraction_revision_id IS NULL OR source_version_id IS NOT NULL",
            name="revision_requires_version",
        ),
        CheckConstraint(
            "status IN ('succeeded', 'failed')",
            name="valid_status",
        ),
        CheckConstraint(
            "status <> 'succeeded' OR "
            "(output_hash IS NOT NULL AND extraction_revision_id IS NOT NULL)",
            name="succeeded_requires_output_and_revision",
        ),
        CheckConstraint(
            "status <> 'failed' OR (output_hash IS NULL AND extraction_revision_id IS NULL)",
            name="failed_has_no_output_or_revision",
        ),
        Index(
            "ix_parser_executions_source_id_executed_at",
            "source_id",
            "executed_at",
            "parser_execution_id",
        ),
        Index(
            "ix_parser_executions_source_version_id",
            "source_version_id",
        ),
    )

    parser_execution_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    source_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("sources.source_id", name="fk_parser_executions_source_id"),
        nullable=False,
    )
    source_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=True,
    )
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(128), nullable=False)
    output_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    extraction_revision_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint(
            "source_version_id",
            "evidence_key",
            name="uq_evidence_version_key",
        ),
        UniqueConstraint(
            "evidence_id",
            "source_version_id",
            name="uq_evidence_id_version",
        ),
        CheckConstraint("length(text_excerpt) > 0", name="nonempty_text_excerpt"),
        CheckConstraint("chunk_order >= 0", name="nonnegative_chunk_order"),
        Index(
            "ix_evidence_source_version_id_chunk_order",
            "source_version_id",
            "chunk_order",
            "evidence_id",
        ),
    )

    evidence_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    source_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("source_versions.source_version_id", name="fk_evidence_source_version_id"),
        nullable=False,
    )
    evidence_key: Mapped[str] = mapped_column(String(256), nullable=False)
    section_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    chunk_order: Mapped[int] = mapped_column(Integer, nullable=False)
    origin_kind: Mapped[str] = mapped_column(String(64), nullable=False)


class SourceOriginEvidence(Base):
    __tablename__ = "source_origin_evidence"

    origin_relation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "source_origins.origin_relation_id",
            name="fk_source_origin_evidence_origin_relation_id",
        ),
        primary_key=True,
    )
    evidence_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("evidence.evidence_id", name="fk_source_origin_evidence_evidence_id"),
        primary_key=True,
    )


class ExtractionRevisionEvidence(Base):
    __tablename__ = "extraction_revision_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["extraction_revision_id", "source_version_id"],
            [
                "extraction_revisions.extraction_revision_id",
                "extraction_revisions.source_version_id",
            ],
            name="fk_extraction_revision_evidence_revision_version",
        ),
        ForeignKeyConstraint(
            ["evidence_id", "source_version_id"],
            ["evidence.evidence_id", "evidence.source_version_id"],
            name="fk_extraction_revision_evidence_evidence_version",
        ),
        Index(
            "ix_extraction_revision_evidence_source_version_id",
            "source_version_id",
        ),
    )

    extraction_revision_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    evidence_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    source_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )


class PostingSectionEvidence(Base):
    __tablename__ = "posting_section_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["extraction_revision_id", "section_key"],
            ["posting_sections.extraction_revision_id", "posting_sections.section_key"],
            name="fk_posting_section_evidence_section",
        ),
        ForeignKeyConstraint(
            ["extraction_revision_id", "evidence_id"],
            [
                "extraction_revision_evidence.extraction_revision_id",
                "extraction_revision_evidence.evidence_id",
            ],
            name="fk_posting_section_evidence_revision_evidence",
        ),
        UniqueConstraint(
            "extraction_revision_id",
            "section_key",
            "evidence_order",
            name="uq_posting_section_evidence_section_order",
        ),
        CheckConstraint("evidence_order >= 0", name="nonnegative_evidence_order"),
    )

    extraction_revision_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    evidence_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    section_key: Mapped[str] = mapped_column(Text, primary_key=True)
    evidence_order: Mapped[int] = mapped_column(Integer, nullable=False)


class CollectionAttempt(Base):
    __tablename__ = "collection_attempts"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "command_id",
            name="uq_collection_attempts_owner_command",
        ),
        CheckConstraint("input_version > 0", name="positive_input_version"),
        CheckConstraint("result_version > 0", name="positive_result_version"),
        CheckConstraint(
            "policy_revision IS NULL OR policy_revision > 0",
            name="positive_policy_revision",
        ),
        CheckConstraint(
            "resume_stage = 'policy' OR policy_revision IS NOT NULL",
            name="policy_revision_required_after_policy",
        ),
        CheckConstraint("owner_deletion_epoch >= 0", name="nonnegative_deletion_epoch"),
        CheckConstraint(
            "(private_scope_kind = 'PROJECT' AND project_id IS NOT NULL) OR "
            "(private_scope_kind = 'ACCOUNT' AND project_id IS NULL) OR "
            "private_scope_kind = 'UNKNOWN'",
            name="valid_private_scope",
        ),
        CheckConstraint("length(btrim(execution_fence)) > 0", name="nonempty_execution_fence"),
        CheckConstraint(
            "(result_payload IS NULL) = (finalized_at IS NULL)",
            name="result_payload_finalized_together",
        ),
        Index(
            "ix_collection_attempts_owner_job",
            "owner_user_id",
            "job_id",
            "attempt_id",
        ),
        Index(
            "ix_collection_attempts_parser_execution_id",
            "parser_execution_id",
        ),
        Index(
            "ix_collection_attempts_owner_private_scope",
            "owner_user_id",
            "private_scope_kind",
            "project_id",
        ),
    )

    attempt_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    job_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    private_scope_kind: Mapped[str] = mapped_column(
        String(16),
        default="UNKNOWN",
        server_default=text("'UNKNOWN'"),
        nullable=False,
    )
    command_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    input_version: Mapped[int] = mapped_column(Integer, nullable=False)
    target_ref: Mapped[str] = mapped_column(Text, nullable=False)
    purpose_ref: Mapped[str] = mapped_column(Text, nullable=False)
    core_source_decision: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    resume_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_version: Mapped[int] = mapped_column(Integer, nullable=False)
    execution_fence: Mapped[str] = mapped_column(String(256), nullable=False)
    owner_deletion_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parser_execution_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "parser_executions.parser_execution_id",
            name="fk_collection_attempts_parser_execution_id",
        ),
        nullable=True,
    )
    checkpoint_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    failures: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    required_actions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CollectionRuntimeAttempt(Base):
    __tablename__ = "collection_runtime_attempts"
    __table_args__ = (
        UniqueConstraint("attempt_id", name="uq_collection_runtime_attempts_attempt_id"),
        UniqueConstraint(
            "source_id",
            "observation_order",
            name="uq_collection_runtime_attempts_source_observation_order",
        ),
        ForeignKeyConstraint(
            ["source_id", "company_id"],
            ["sources.source_id", "sources.company_id"],
            name="fk_collection_runtime_attempts_source_company",
        ),
        ForeignKeyConstraint(
            ["observation_id", "source_id"],
            ["source_observations.observation_id", "source_observations.source_id"],
            name="fk_collection_runtime_attempts_observation_same_source",
        ),
        ForeignKeyConstraint(
            ["source_version_id", "source_id"],
            ["source_versions.source_version_id", "source_versions.source_id"],
            name="fk_collection_runtime_attempts_version_same_source",
        ),
        CheckConstraint("observation_order > 0", name="positive_observation_order"),
        CheckConstraint(
            "effective_policy_revision > 0",
            name="positive_policy_revision",
        ),
        CheckConstraint(
            "dispatch_digest ~ '^[0-9a-f]{64}$'",
            name="valid_dispatch_digest",
        ),
        CheckConstraint(
            "state IN ('RESERVED', 'PERSISTED', 'FINALIZED', 'INVALIDATED')",
            name="valid_state",
        ),
        CheckConstraint(
            "(claim_token IS NULL) = (claim_expires_at IS NULL)",
            name="claim_fields_together",
        ),
        CheckConstraint(
            "state = 'RESERVED' OR (claim_token IS NULL AND claim_expires_at IS NULL)",
            name="claim_fields_reserved_only",
        ),
        CheckConstraint(
            "(private_scope_kind = 'PROJECT' AND project_id IS NOT NULL) OR "
            "(private_scope_kind = 'ACCOUNT' AND project_id IS NULL) OR "
            "private_scope_kind = 'UNKNOWN'",
            name="valid_private_scope",
        ),
        Index("ix_collection_runtime_attempts_owner_ref", "owner_ref"),
        Index("ix_collection_runtime_attempts_job_id", "job_id"),
        Index(
            "ix_collection_runtime_attempts_owner_private_scope",
            "owner_ref",
            "private_scope_kind",
            "project_id",
        ),
    )

    command_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    attempt_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    dispatch_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_ref: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    job_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    private_scope_kind: Mapped[str] = mapped_column(
        String(16),
        default="UNKNOWN",
        server_default=text("'UNKNOWN'"),
        nullable=False,
    )
    project_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    source_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    observation_order: Mapped[int] = mapped_column(BigInteger, nullable=False)
    effective_policy_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    claim_token: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=True,
    )
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    observation_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=True,
    )
    source_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RequestDeduplication(Base):
    __tablename__ = "request_deduplications"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "operation",
            "idempotency_key",
            name="uq_request_deduplications_owner_operation_key",
        ),
        CheckConstraint("input_version > 0", name="positive_input_version"),
        CheckConstraint(
            "(private_scope_kind = 'PROJECT' AND project_id IS NOT NULL) OR "
            "(private_scope_kind = 'ACCOUNT' AND project_id IS NULL) OR "
            "private_scope_kind = 'UNKNOWN'",
            name="valid_private_scope",
        ),
        Index(
            "ix_request_deduplications_owner_private_scope",
            "owner_user_id",
            "private_scope_kind",
            "project_id",
        ),
    )

    request_deduplication_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    private_scope_kind: Mapped[str] = mapped_column(
        String(16),
        default="UNKNOWN",
        server_default=text("'UNKNOWN'"),
        nullable=False,
    )
    project_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    operation: Mapped[str] = mapped_column(String(256), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    accepted_resource_ref: Mapped[str] = mapped_column(Text, nullable=False)
    input_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PrivateDeletionOwnerState(Base):
    __tablename__ = "private_deletion_owner_states"

    owner_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    latest_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )


class PrivateDeletionProjectTombstone(Base):
    __tablename__ = "private_deletion_project_tombstones"
    __table_args__ = (CheckConstraint("deletion_epoch > 0", name="positive_deletion_epoch"),)

    owner_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey(
            "private_deletion_owner_states.owner_user_id",
            name="fk_private_deletion_project_tombstones_owner_state",
        ),
        primary_key=True,
    )
    project_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    deletion_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)


class PrivateDeletionReceipt(Base):
    __tablename__ = "private_deletion_receipts"
    __table_args__ = (
        UniqueConstraint(
            "owner_user_id",
            "deletion_epoch",
            name="uq_private_deletion_receipts_owner_epoch",
        ),
        CheckConstraint("deletion_epoch > 0", name="positive_deletion_epoch"),
    )

    deletion_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        primary_key=True,
    )
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    deletion_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    contract_version: Mapped[str] = mapped_column(
        String(32),
        default="w2.private-deletion.v1",
        server_default=text("'w2.private-deletion.v1'"),
        nullable=False,
    )
    command_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ack_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint(
            "aggregate_id",
            "aggregate_revision",
            name="uq_outbox_events_aggregate_revision",
        ),
        CheckConstraint("aggregate_revision > 0", name="positive_aggregate_revision"),
        Index(
            "ix_outbox_events_delivery_occurred",
            "delivery_state",
            "occurred_at",
            "event_id",
        ),
    )

    event_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    aggregate_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("sources.source_id", name="fk_outbox_events_aggregate_id"),
        nullable=False,
    )
    aggregate_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivery_state: Mapped[str] = mapped_column(String(32), nullable=False)


class StaleExecution(RuntimeError):
    """Raised when a private collection attempt no longer owns its execution lease."""


class PersistenceConflict(RuntimeError):
    """Raised when an idempotency key or immutable persisted value changes payload."""


def _private_deletion_command_digest(command: PrivateDeletionCommand) -> str:
    payload = {
        "deletion_id": str(command.deletion_id),
        "owner_user_id": str(command.owner_user_id),
        "deletion_epoch": command.deletion_epoch,
        "attempt_ids": sorted(str(attempt_id) for attempt_id in command.attempt_ids),
        "request_deduplication_ids": sorted(
            str(request_id) for request_id in command.request_deduplication_ids
        ),
        "private_reference_keys": sorted(command.private_reference_keys),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def apply_private_deletion(
    session: Session,
    command: PrivateDeletionCommand,
) -> Literal["APPLIED", "DUPLICATE", "STALE"]:
    """Delete one owner's named private rows and persist monotonic receipt state."""

    command_digest = _private_deletion_command_digest(command)
    session.execute(
        postgresql_insert(PrivateDeletionOwnerState)
        .values(owner_user_id=command.owner_user_id, latest_epoch=0)
        .on_conflict_do_nothing(index_elements=["owner_user_id"])
    )
    owner_state = session.scalar(
        select(PrivateDeletionOwnerState)
        .where(PrivateDeletionOwnerState.owner_user_id == command.owner_user_id)
        .with_for_update()
    )
    if owner_state is None:
        raise RuntimeError("private deletion owner state row was not found")

    receipt = session.get(PrivateDeletionReceipt, command.deletion_id)
    if receipt is not None:
        if receipt.command_digest != command_digest:
            raise PersistenceConflict("deletion receipt does not match command")
        if command.deletion_epoch < owner_state.latest_epoch:
            return "STALE"
        return "DUPLICATE"

    if command.deletion_epoch <= owner_state.latest_epoch:
        return "STALE"

    attempts = list(
        session.scalars(
            select(CollectionAttempt).where(CollectionAttempt.attempt_id.in_(command.attempt_ids))
        )
    )
    request_deduplications = list(
        session.scalars(
            select(RequestDeduplication).where(
                RequestDeduplication.request_deduplication_id.in_(command.request_deduplication_ids)
            )
        )
    )
    if any(
        attempt.attempt_id not in command.attempt_ids
        or attempt.owner_user_id != command.owner_user_id
        for attempt in attempts
    ) or any(
        request.request_deduplication_id not in command.request_deduplication_ids
        or request.owner_user_id != command.owner_user_id
        for request in request_deduplications
    ):
        raise PersistenceConflict("private deletion candidate belongs to another owner")

    for attempt in attempts:
        session.delete(attempt)
    for request in request_deduplications:
        session.delete(request)

    owner_state.latest_epoch = command.deletion_epoch
    session.add(
        PrivateDeletionReceipt(
            deletion_id=command.deletion_id,
            owner_user_id=command.owner_user_id,
            deletion_epoch=command.deletion_epoch,
            command_digest=command_digest,
            outcome="APPLIED",
            created_at=datetime.now(UTC),
        )
    )
    return "APPLIED"


def apply_private_deletion_v2(
    session: Session,
    command: PrivateDeletionCommandV2,
) -> Literal["APPLIED", "DUPLICATE", "STALE"]:
    """Delete every W2-private row in an authenticated account or Project scope."""

    from epick_engine.source_collection.commit_gate_store import (  # noqa: PLC0415
        PrivateCommitGateAck,
        PrivateCommitGateInbox,
        PrivateCommitGateReceipt,
        PrivateCommitStage,
        PrivateStagedOutbox,
    )
    from epick_engine.source_collection.private_deletion_v2 import (  # noqa: PLC0415
        command_digest_v2,
    )
    from epick_engine.source_collection.private_scope import (  # noqa: PLC0415
        ScopeUnclassified,
    )

    command_digest = command_digest_v2(command)
    session.execute(
        postgresql_insert(PrivateDeletionOwnerState)
        .values(
            owner_user_id=command.owner_user_id,
            latest_epoch=0,
            account_deleted=False,
        )
        .on_conflict_do_nothing(index_elements=["owner_user_id"])
    )
    owner_state = session.scalar(
        select(PrivateDeletionOwnerState)
        .where(PrivateDeletionOwnerState.owner_user_id == command.owner_user_id)
        .with_for_update()
    )
    if owner_state is None:
        raise RuntimeError("private deletion owner state row was not found")

    receipt = session.scalar(
        select(PrivateDeletionReceipt)
        .where(PrivateDeletionReceipt.deletion_id == command.deletion_id)
        .with_for_update()
    )
    if receipt is not None:
        if receipt.contract_version != "w2.private-deletion.v2":
            raise PersistenceConflict("deletion receipt contract version does not match command")
        if receipt.command_digest != command_digest:
            raise PersistenceConflict("deletion receipt does not match command")
        if command.deletion_epoch < owner_state.latest_epoch:
            return "STALE"
        if command.deletion_epoch > owner_state.latest_epoch:
            raise PersistenceConflict("deletion receipt is ahead of owner state")
        return "DUPLICATE"

    if command.deletion_epoch < owner_state.latest_epoch:
        return "STALE"
    if command.deletion_epoch == owner_state.latest_epoch:
        raise PersistenceConflict("deletion epoch is already bound to another command")

    if command.scope.kind == "PROJECT":
        unknown_rows = (
            session.scalar(
                select(CollectionAttempt.attempt_id)
                .where(
                    CollectionAttempt.owner_user_id == command.owner_user_id,
                    CollectionAttempt.private_scope_kind == "UNKNOWN",
                )
                .limit(1)
            ),
            session.scalar(
                select(RequestDeduplication.request_deduplication_id)
                .where(
                    RequestDeduplication.owner_user_id == command.owner_user_id,
                    RequestDeduplication.private_scope_kind == "UNKNOWN",
                )
                .limit(1)
            ),
            session.scalar(
                select(CollectionRuntimeAttempt.command_id)
                .where(
                    CollectionRuntimeAttempt.owner_ref == command.owner_user_id,
                    CollectionRuntimeAttempt.private_scope_kind == "UNKNOWN",
                )
                .limit(1)
            ),
            session.scalar(
                select(PrivateCommitStage.command_id)
                .where(
                    PrivateCommitStage.owner_ref == command.owner_user_id,
                    PrivateCommitStage.private_scope_kind == "UNKNOWN",
                )
                .limit(1)
            ),
        )
        if any(row_id is not None for row_id in unknown_rows):
            raise ScopeUnclassified("Project deletion found owner-private unclassified state")

        assert command.scope.project_id is not None
        attempt_scope = and_(
            CollectionAttempt.owner_user_id == command.owner_user_id,
            CollectionAttempt.private_scope_kind == "PROJECT",
            CollectionAttempt.project_id == command.scope.project_id,
        )
        deduplication_scope = and_(
            RequestDeduplication.owner_user_id == command.owner_user_id,
            RequestDeduplication.private_scope_kind == "PROJECT",
            RequestDeduplication.project_id == command.scope.project_id,
        )
        runtime_scope = and_(
            CollectionRuntimeAttempt.owner_ref == command.owner_user_id,
            CollectionRuntimeAttempt.private_scope_kind == "PROJECT",
            CollectionRuntimeAttempt.project_id == command.scope.project_id,
        )
        stage_scope = and_(
            PrivateCommitStage.owner_ref == command.owner_user_id,
            PrivateCommitStage.private_scope_kind == "PROJECT",
            PrivateCommitStage.project_id == command.scope.project_id,
        )
    else:
        attempt_scope = CollectionAttempt.owner_user_id == command.owner_user_id
        deduplication_scope = RequestDeduplication.owner_user_id == command.owner_user_id
        runtime_scope = CollectionRuntimeAttempt.owner_ref == command.owner_user_id
        stage_scope = PrivateCommitStage.owner_ref == command.owner_user_id

    stage_ids = select(PrivateCommitStage.command_id).where(stage_scope)
    retained_stage_ids = select(PrivateCommitStage.command_id).where(
        stage_scope,
        exists(
            select(PrivateCommitGateAck.message_id).where(
                PrivateCommitGateAck.command_id == PrivateCommitStage.command_id
            )
        ),
    )
    disposable_stage_ids = select(PrivateCommitStage.command_id).where(
        stage_scope,
        ~exists(
            select(PrivateCommitGateAck.message_id).where(
                PrivateCommitGateAck.command_id == PrivateCommitStage.command_id
            )
        ),
    )
    session.execute(
        delete(PrivateStagedOutbox).where(PrivateStagedOutbox.command_id.in_(stage_ids))
    )
    session.execute(
        delete(PrivateCommitGateReceipt).where(
            PrivateCommitGateReceipt.command_id.in_(disposable_stage_ids)
        )
    )
    session.execute(
        delete(PrivateCommitGateInbox).where(
            PrivateCommitGateInbox.command_id.in_(disposable_stage_ids)
        )
    )
    session.execute(
        delete(PrivateCommitGateAck).where(
            PrivateCommitGateAck.command_id.in_(disposable_stage_ids)
        )
    )
    session.execute(
        delete(PrivateCommitStage).where(PrivateCommitStage.command_id.in_(disposable_stage_ids))
    )
    session.execute(
        update(PrivateCommitStage)
        .where(PrivateCommitStage.command_id.in_(retained_stage_ids))
        .values(result_payload=None, payload_purged=True)
    )

    session.execute(delete(CollectionAttempt).where(attempt_scope))
    session.execute(delete(RequestDeduplication).where(deduplication_scope))
    session.execute(delete(CollectionRuntimeAttempt).where(runtime_scope))

    owner_state.latest_epoch = command.deletion_epoch
    if command.scope.kind == "ACCOUNT":
        owner_state.account_deleted = True
    else:
        assert command.scope.project_id is not None
        tombstone = session.scalar(
            select(PrivateDeletionProjectTombstone)
            .where(
                PrivateDeletionProjectTombstone.owner_user_id == command.owner_user_id,
                PrivateDeletionProjectTombstone.project_id == command.scope.project_id,
            )
            .with_for_update()
        )
        if tombstone is None:
            session.add(
                PrivateDeletionProjectTombstone(
                    owner_user_id=command.owner_user_id,
                    project_id=command.scope.project_id,
                    deletion_epoch=command.deletion_epoch,
                )
            )
        else:
            tombstone.deletion_epoch = command.deletion_epoch

    session.add(
        PrivateDeletionReceipt(
            deletion_id=command.deletion_id,
            owner_user_id=command.owner_user_id,
            deletion_epoch=command.deletion_epoch,
            contract_version="w2.private-deletion.v2",
            command_digest=command_digest,
            outcome="APPLIED",
            created_at=datetime.now(UTC),
        )
    )
    return "APPLIED"


class InvalidPreparedCollection(ValueError):
    """Raised when prepared data is internally inconsistent with its command."""


@dataclass(frozen=True, slots=True)
class ExecutionAuthorityGrant:
    """Immutable proof returned after W1 authority rows are locked and validated."""

    attempt_id: UUID
    owner_user_id: UUID
    job_id: UUID
    command_id: UUID
    company_id: UUID
    source_id: UUID
    input_version: int
    execution_fence: str
    owner_deletion_epoch: int
    pointer_eligible: bool
    private_scope: PrivateWriteScope


class ExecutionAuthorityLocker(Protocol):
    """W1-owned same-transaction execution-authority lock boundary."""

    def __call__(
        self,
        session: Session,
        *,
        command: CollectionCommand,
        attempt_id: UUID,
    ) -> ExecutionAuthorityGrant: ...


@dataclass(frozen=True, slots=True)
class PreparedSourceVersion:
    source_version_id: UUID
    source_id: UUID
    company_id: UUID
    title: str | None
    source_type: SourceType
    canonical_url: str
    content_hash: str
    hash_profile_version: str
    representation: Representation
    first_parser_version: str
    collected_at: datetime
    published_at: DateValue
    valid_from: DateValue
    valid_to: DateValue
    language: str | None
    policy_decision_id: UUID


@dataclass(frozen=True, slots=True)
class PreparedEvidence:
    evidence_id: UUID
    source_version_id: UUID
    evidence_key: str
    section_title: str | None
    text_excerpt: str
    locator: Locator
    chunk_order: int
    origin_kind: str


@dataclass(frozen=True, slots=True)
class PreparedRetainedBody:
    body_id: UUID
    source_version_id: UUID
    source_id: UUID
    normalization_version: str
    body_text: str
    necessity_reason: str
    policy_decision_id: UUID
    retained_at: datetime
    retention_policy_version: str
    retention_limit_bytes: int


@dataclass(frozen=True, slots=True)
class PreparedSourceOrigin:
    origin_relation_id: UUID
    source_id: UUID
    origin_source_id: UUID | None
    origin_url: str | None
    relationship_kind: str
    verification_status: str
    evidence_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class PreparedExtractionRevision:
    extraction_revision_id: UUID
    source_version_id: UUID
    parser_version: str
    output_hash: str
    created_at: datetime
    extraction_status: ExtractionStatus
    posting_sections: tuple[PostingSectionValue, ...]
    date_values: Mapping[str, DateValue]
    limitations: tuple[str, ...]
    evidence_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class PreparedParserExecution:
    parser_execution_id: UUID
    source_id: UUID
    source_version_id: UUID | None
    content_hash: str
    parser_version: str
    output_hash: str | None
    extraction_revision_id: UUID | None
    status: Literal["succeeded", "failed"]
    executed_at: datetime


@dataclass(frozen=True, slots=True)
class PreparedSourceObservation:
    snapshot: SourceObservationSnapshot
    retrieval_validator: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class PreparedCollectionCommit:
    attempt_id: UUID
    result: CollectionResult
    finalized_at: datetime
    source_version: PreparedSourceVersion | None = None
    evidence: tuple[PreparedEvidence, ...] = ()
    retained_body: PreparedRetainedBody | None = None
    source_origins: tuple[PreparedSourceOrigin, ...] = ()
    extraction_revision: PreparedExtractionRevision | None = None
    parser_execution: PreparedParserExecution | None = None
    observation: PreparedSourceObservation | None = None
    events: tuple[SourceEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class CanonicalPublicCommit:
    source: Source
    prepared: PreparedCollectionCommit


def assert_current_attempt(
    session: Session,
    *,
    attempt_id: UUID,
    owner_user_id: UUID,
    execution_fence: str,
    owner_deletion_epoch: int,
) -> CollectionAttempt:
    """Lock and return an attempt only when its owner, fence, and deletion epoch match."""

    attempt = session.scalar(
        select(CollectionAttempt)
        .where(
            CollectionAttempt.attempt_id == attempt_id,
            CollectionAttempt.owner_user_id == owner_user_id,
            CollectionAttempt.execution_fence == execution_fence,
            CollectionAttempt.owner_deletion_epoch == owner_deletion_epoch,
        )
        .with_for_update()
    )
    if attempt is None:
        raise StaleExecution("collection attempt is no longer current")
    return attempt


class PersistenceConfigurationError(RuntimeError):
    """Raised when the required PostgreSQL connection boundary is not configured."""


class Closable(Protocol):
    def close(self) -> None: ...


def _require_postgresql(url: URL) -> URL:
    if url.drivername != "postgresql+psycopg":
        raise PersistenceConfigurationError(
            "W2 persistence requires PostgreSQL with the synchronous psycopg driver"
        )
    return url


def database_url_from_environment(variable: str = "EPICK_DATABASE_URL") -> URL:
    """Read a PostgreSQL URL without logging or embedding its credential value."""

    raw_url = os.environ.get(variable)
    if not raw_url:
        raise PersistenceConfigurationError(f"Required database setting is missing: {variable}")
    try:
        url = make_url(raw_url)
    except Exception as error:
        raise PersistenceConfigurationError(f"Invalid database setting: {variable}") from error
    return _require_postgresql(url)


def create_database_engine(url: URL | str) -> Engine:
    """Create the synchronous engine; no connection is opened until first use."""

    parsed = _require_postgresql(make_url(url) if isinstance(url, str) else url)
    return create_engine(parsed, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a factory that creates one non-shared Session per request or execution."""

    return sessionmaker(
        bind=engine,
        class_=Session,
        autoflush=False,
        expire_on_commit=False,
    )


@contextmanager
def session_scope[SessionValue: Closable](
    factory: Callable[[], SessionValue],
) -> Iterator[SessionValue]:
    """Close a Session-like value reliably; transaction ownership remains with the caller."""

    session = factory()
    try:
        yield session
    finally:
        session.close()


_PUBLIC_EVENT_PRIVATE_KEYS = frozenset(
    {
        "authenticated_owner_ref",
        "owner_user_id",
        "job_id",
        "project_id",
        "project_ref",
        "purpose_ref",
        "core_source_decision",
        "execution_fence",
        "owner_deletion_epoch",
        "body_text",
        "result_payload",
        "result_refs",
        "failures",
        "required_actions",
    }
)


def _require_aware(value: datetime, *, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidPreparedCollection(f"{field} must be timezone-aware")


def _require_positive_limit(limit: int) -> None:
    if limit <= 0:
        raise ValueError("limit must be positive")


def _require_query_cursor_time(value: datetime, *, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} cursor timestamp must be timezone-aware")


def get_current_source_version(
    session: Session,
    *,
    source_id: UUID,
) -> SourceVersion | None:
    """Return the current version pointer for one Source without modifying it."""

    return session.scalar(
        select(SourceVersion)
        .join(
            Source,
            Source.current_source_version_id == SourceVersion.source_version_id,
        )
        .where(
            Source.source_id == source_id,
            SourceVersion.source_id == source_id,
        )
    )


def get_latest_source_observation(
    session: Session,
    *,
    source_id: UUID,
) -> SourceObservation | None:
    """Return the latest observation pointer for one Source without modifying it."""

    return session.scalar(
        select(SourceObservation)
        .join(
            Source,
            Source.latest_observation_id == SourceObservation.observation_id,
        )
        .where(
            Source.source_id == source_id,
            SourceObservation.source_id == source_id,
        )
    )


def get_source_version(
    session: Session,
    *,
    source_id: UUID,
    source_version_id: UUID,
) -> SourceVersion | None:
    """Return a historical version only when it belongs to the requested Source."""

    return session.scalar(
        select(SourceVersion).where(
            SourceVersion.source_id == source_id,
            SourceVersion.source_version_id == source_version_id,
        )
    )


def lock_source_policy_scope(session: Session, source_id: UUID) -> Source:
    source = session.scalar(
        select(Source)
        .where(Source.source_id == source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if source is None:
        raise PersistenceConflict("source policy scope is not registered")
    return source


def append_source_policy_decision(
    session: Session,
    decision: SourcePolicyDecision,
) -> SourcePolicyDecision:
    lock_source_policy_scope(session, decision.source_id)
    session.add(decision)
    session.flush()
    return decision


def _restriction_snapshot(
    row: SourceRestriction,
    identity: SourceRestrictionIdentity,
) -> SourceRestrictionSnapshot:
    return SourceRestrictionSnapshot(
        restriction_id=row.restriction_id,
        source_id=row.source_id,
        source_version_id=identity.source_version_id,
        restriction_revision=row.restriction_revision,
        restriction_status=RestrictionStatus(row.restriction_status),
        accuracy_status=AccuracyStatus(row.accuracy_status),
        reason_code=row.reason_code,
        changed_at=row.changed_at,
        replacement_ref=row.replacement_ref,
    )


def _assert_restriction_replay(
    row: SourceRestriction,
    snapshot: SourceRestrictionSnapshot,
    evidence_refs: Sequence[str],
) -> None:
    expected = {
        "restriction_id": snapshot.restriction_id,
        "source_id": snapshot.source_id,
        "restriction_revision": snapshot.restriction_revision,
        "restriction_status": snapshot.restriction_status.value,
        "accuracy_status": snapshot.accuracy_status.value,
        "reason_code": snapshot.reason_code,
        "evidence_refs": list(evidence_refs),
        "changed_at": snapshot.changed_at,
        "replacement_ref": snapshot.replacement_ref,
    }
    if any(getattr(row, field) != value for field, value in expected.items()):
        raise PersistenceConflict("restriction replay payload changed")


def lock_source_restriction_revision(session: Session, *, source_id: UUID) -> int:
    """Lock a registered Source and return its next restriction revision."""

    source = session.scalar(select(Source).where(Source.source_id == source_id).with_for_update())
    if source is None:
        raise PersistenceConflict("restriction source is not registered")
    latest_revision = session.scalar(
        select(func.coalesce(func.max(SourceRestriction.restriction_revision), 0)).where(
            SourceRestriction.source_id == source_id
        )
    )
    return int(latest_revision or 0) + 1


def get_source_restriction_revision(
    session: Session,
    *,
    restriction_id: UUID,
    restriction_revision: int,
) -> SourceRestrictionSnapshot | None:
    """Return exactly one immutable restriction history revision."""

    result = session.execute(
        select(SourceRestriction, SourceRestrictionIdentity)
        .join(
            SourceRestrictionIdentity,
            and_(
                SourceRestrictionIdentity.restriction_id == SourceRestriction.restriction_id,
                SourceRestrictionIdentity.source_id == SourceRestriction.source_id,
            ),
        )
        .where(
            SourceRestriction.restriction_id == restriction_id,
            SourceRestriction.restriction_revision == restriction_revision,
        )
    ).one_or_none()
    if result is None:
        return None
    row, identity = result
    return _restriction_snapshot(row, identity)


def record_source_restriction(
    session: Session,
    *,
    snapshot: SourceRestrictionSnapshot,
    evidence_refs: Sequence[str] = (),
) -> SourceRestrictionSnapshot:
    """Persist a restriction revision and its public event in one transaction."""

    _require_aware(snapshot.changed_at, field="restriction changed_at")
    source = session.scalar(
        select(Source).where(Source.source_id == snapshot.source_id).with_for_update()
    )
    if source is None:
        raise PersistenceConflict("restriction source is not registered")

    identity = session.get(SourceRestrictionIdentity, snapshot.restriction_id)
    if identity is not None and (
        identity.source_id != snapshot.source_id
        or identity.source_version_id != snapshot.source_version_id
    ):
        raise PersistenceConflict("restriction identity scope changed")
    if (
        snapshot.source_version_id is not None
        and get_source_version(
            session,
            source_id=snapshot.source_id,
            source_version_id=snapshot.source_version_id,
        )
        is None
    ):
        raise PersistenceConflict("restriction version does not belong to the source")
    if (
        snapshot.replacement_ref is not None
        and session.get(Source, snapshot.replacement_ref) is None
    ):
        raise PersistenceConflict("restriction replacement source is not registered")

    source_revision = session.scalar(
        select(SourceRestriction).where(
            SourceRestriction.source_id == snapshot.source_id,
            SourceRestriction.restriction_revision == snapshot.restriction_revision,
        )
    )
    if source_revision is not None:
        if source_revision.restriction_id != snapshot.restriction_id:
            raise PersistenceConflict("restriction revision is already used by another identity")
        if identity is None:
            raise RuntimeError("restriction history has no identity")
        _assert_restriction_replay(source_revision, snapshot, evidence_refs)
        return _restriction_snapshot(source_revision, identity)

    latest_revision = int(
        session.scalar(
            select(func.coalesce(func.max(SourceRestriction.restriction_revision), 0)).where(
                SourceRestriction.source_id == snapshot.source_id
            )
        )
        or 0
    )
    if snapshot.restriction_revision != latest_revision + 1:
        raise PersistenceConflict("next restriction revision must be source-wide and gap-free")

    if identity is None:
        inserted_id = session.scalar(
            postgresql_insert(SourceRestrictionIdentity)
            .values(
                restriction_id=snapshot.restriction_id,
                source_id=snapshot.source_id,
                source_version_id=snapshot.source_version_id,
            )
            .on_conflict_do_nothing(
                index_elements=[SourceRestrictionIdentity.restriction_id],
            )
            .returning(SourceRestrictionIdentity.restriction_id)
        )
        identity = session.get(SourceRestrictionIdentity, snapshot.restriction_id)
        if identity is None:
            raise RuntimeError("restriction identity conflict row was not found")
        if inserted_id is None and (
            identity.source_id != snapshot.source_id
            or identity.source_version_id != snapshot.source_version_id
        ):
            raise PersistenceConflict("restriction identity scope changed")

    created = SourceRestriction(
        restriction_id=snapshot.restriction_id,
        source_id=snapshot.source_id,
        restriction_revision=snapshot.restriction_revision,
        restriction_status=snapshot.restriction_status.value,
        accuracy_status=snapshot.accuracy_status.value,
        reason_code=snapshot.reason_code,
        evidence_refs=list(evidence_refs),
        changed_at=snapshot.changed_at,
        replacement_ref=snapshot.replacement_ref,
    )
    session.add(created)
    session.flush()
    stored = _restriction_snapshot(created, identity)
    record_source_restriction_public_event(session, snapshot=stored)
    return stored


def get_current_source_restriction(
    session: Session,
    *,
    source_id: UUID,
    restriction_id: UUID,
) -> SourceRestrictionSnapshot | None:
    """Return the highest stored revision for one stable restriction identity."""

    row = session.scalar(
        select(SourceRestriction)
        .where(
            SourceRestriction.source_id == source_id,
            SourceRestriction.restriction_id == restriction_id,
        )
        .order_by(SourceRestriction.restriction_revision.desc())
        .limit(1)
    )
    if row is None:
        return None
    identity = session.get(SourceRestrictionIdentity, restriction_id)
    if identity is None:
        raise RuntimeError("restriction history has no identity")
    return _restriction_snapshot(row, identity)


def list_source_restrictions(
    session: Session,
    *,
    source_id: UUID,
    restriction_id: UUID | None = None,
) -> tuple[SourceRestrictionSnapshot, ...]:
    """Return immutable restriction history ordered by Source-wide revision."""

    statement = select(SourceRestriction).where(SourceRestriction.source_id == source_id)
    if restriction_id is not None:
        statement = statement.where(SourceRestriction.restriction_id == restriction_id)
    rows = session.scalars(statement.order_by(SourceRestriction.restriction_revision)).all()
    identities = {
        identity.restriction_id: identity
        for identity in session.scalars(
            select(SourceRestrictionIdentity).where(
                SourceRestrictionIdentity.source_id == source_id
            )
        )
    }
    return tuple(_restriction_snapshot(row, identities[row.restriction_id]) for row in rows)


def list_current_source_restrictions(
    session: Session,
    *,
    source_id: UUID,
) -> tuple[SourceRestrictionSnapshot, ...]:
    """Return each restriction identity's latest revision without deciding effective use."""

    latest = (
        select(
            SourceRestriction.restriction_id,
            func.max(SourceRestriction.restriction_revision).label("latest_revision"),
        )
        .where(SourceRestriction.source_id == source_id)
        .group_by(SourceRestriction.restriction_id)
        .subquery()
    )
    rows = session.scalars(
        select(SourceRestriction)
        .join(
            latest,
            and_(
                SourceRestriction.restriction_id == latest.c.restriction_id,
                SourceRestriction.restriction_revision == latest.c.latest_revision,
            ),
        )
        .where(SourceRestriction.source_id == source_id)
        .order_by(SourceRestriction.restriction_revision)
    ).all()
    identities = {
        identity.restriction_id: identity
        for identity in session.scalars(
            select(SourceRestrictionIdentity).where(
                SourceRestrictionIdentity.source_id == source_id
            )
        )
    }
    return tuple(_restriction_snapshot(row, identities[row.restriction_id]) for row in rows)


def get_retained_body_for_reextraction(
    session: Session,
    *,
    source_id: UUID,
    source_version_id: UUID,
    normalization_version: str,
    current_policy_decision_id: UUID,
) -> RetainedBody | None:
    """Return a retained body only through the policy-checked re-extraction boundary."""

    if not normalization_version.strip():
        raise ValueError("normalization_version must not be blank")
    latest_revision = (
        select(func.max(SourcePolicyDecision.revision))
        .where(SourcePolicyDecision.source_id == source_id)
        .scalar_subquery()
    )
    current_policy = session.scalar(
        select(SourcePolicyDecision).where(
            SourcePolicyDecision.policy_decision_id == current_policy_decision_id,
            SourcePolicyDecision.source_id == source_id,
            SourcePolicyDecision.revision == latest_revision,
            SourcePolicyDecision.collection_permission == "allowed",
            SourcePolicyDecision.body_storage_permission == "allowed",
        )
    )
    if current_policy is None:
        return None
    return session.scalar(
        select(RetainedBody).where(
            RetainedBody.source_id == source_id,
            RetainedBody.source_version_id == source_version_id,
            RetainedBody.normalization_version == normalization_version,
        )
    )


def list_source_origins(
    session: Session,
    *,
    source_id: UUID,
    limit: int,
    after: tuple[str, UUID] | None = None,
) -> tuple[SourceOrigin, ...]:
    """List Source origins by ``(relationship_kind, origin_relation_id)`` ascending."""

    _require_positive_limit(limit)
    statement = select(SourceOrigin).where(SourceOrigin.source_id == source_id)
    if after is not None:
        relationship_kind, origin_relation_id = after
        statement = statement.where(
            or_(
                SourceOrigin.relationship_kind > relationship_kind,
                and_(
                    SourceOrigin.relationship_kind == relationship_kind,
                    SourceOrigin.origin_relation_id > origin_relation_id,
                ),
            )
        )
    statement = statement.order_by(
        SourceOrigin.relationship_kind,
        SourceOrigin.origin_relation_id,
    )
    return tuple(session.scalars(statement.limit(limit)).all())


def list_source_origin_evidence(
    session: Session,
    *,
    source_id: UUID,
    origin_relation_id: UUID,
    limit: int,
    after: tuple[UUID, int, UUID] | None = None,
) -> tuple[Evidence, ...] | None:
    """List one origin's Evidence by ``(source_version_id, chunk_order, evidence_id)``."""

    _require_positive_limit(limit)
    origin = session.scalar(
        select(SourceOrigin).where(
            SourceOrigin.origin_relation_id == origin_relation_id,
            SourceOrigin.source_id == source_id,
        )
    )
    if origin is None:
        return None
    statement = (
        select(Evidence)
        .join(
            SourceOriginEvidence,
            SourceOriginEvidence.evidence_id == Evidence.evidence_id,
        )
        .where(SourceOriginEvidence.origin_relation_id == origin_relation_id)
    )
    if after is not None:
        source_version_id, chunk_order, evidence_id = after
        if chunk_order < 0:
            raise ValueError("after cursor chunk_order must be nonnegative")
        statement = statement.where(
            or_(
                Evidence.source_version_id > source_version_id,
                and_(
                    Evidence.source_version_id == source_version_id,
                    Evidence.chunk_order > chunk_order,
                ),
                and_(
                    Evidence.source_version_id == source_version_id,
                    Evidence.chunk_order == chunk_order,
                    Evidence.evidence_id > evidence_id,
                ),
            )
        )
    statement = statement.order_by(
        Evidence.source_version_id,
        Evidence.chunk_order,
        Evidence.evidence_id,
    )
    return tuple(session.scalars(statement.limit(limit)).all())


def get_extraction_revision(
    session: Session,
    *,
    source_version_id: UUID,
    extraction_revision_id: UUID,
) -> ExtractionRevision | None:
    """Return a revision only when it belongs to the requested SourceVersion."""

    return session.scalar(
        select(ExtractionRevision).where(
            ExtractionRevision.source_version_id == source_version_id,
            ExtractionRevision.extraction_revision_id == extraction_revision_id,
        )
    )


def list_source_versions(
    session: Session,
    *,
    source_id: UUID,
    limit: int,
    before: tuple[datetime, UUID] | None = None,
) -> tuple[SourceVersion, ...]:
    """List one Source's versions by ``(collected_at, source_version_id)`` descending."""

    _require_positive_limit(limit)
    statement = select(SourceVersion).where(SourceVersion.source_id == source_id)
    if before is not None:
        collected_at, source_version_id = before
        _require_query_cursor_time(collected_at, field="before")
        statement = statement.where(
            or_(
                SourceVersion.collected_at < collected_at,
                and_(
                    SourceVersion.collected_at == collected_at,
                    SourceVersion.source_version_id < source_version_id,
                ),
            )
        )
    statement = statement.order_by(
        SourceVersion.collected_at.desc(),
        SourceVersion.source_version_id.desc(),
    )
    return tuple(session.scalars(statement.limit(limit)).all())


def list_revision_evidence(
    session: Session,
    *,
    source_version_id: UUID,
    extraction_revision_id: UUID,
    limit: int,
    after: tuple[int, UUID] | None = None,
) -> tuple[Evidence, ...] | None:
    """List a revision's Evidence by ``(chunk_order, evidence_id)`` ascending.

    ``None`` means the revision does not belong to ``source_version_id``; an empty tuple means
    a valid revision has no linked Evidence.
    """

    _require_positive_limit(limit)
    if (
        get_extraction_revision(
            session,
            source_version_id=source_version_id,
            extraction_revision_id=extraction_revision_id,
        )
        is None
    ):
        return None

    statement = (
        select(Evidence)
        .join(
            ExtractionRevisionEvidence,
            and_(
                ExtractionRevisionEvidence.evidence_id == Evidence.evidence_id,
                ExtractionRevisionEvidence.source_version_id == Evidence.source_version_id,
            ),
        )
        .where(
            ExtractionRevisionEvidence.extraction_revision_id == extraction_revision_id,
            ExtractionRevisionEvidence.source_version_id == source_version_id,
            Evidence.source_version_id == source_version_id,
        )
    )
    if after is not None:
        chunk_order, evidence_id = after
        if chunk_order < 0:
            raise ValueError("after cursor chunk_order must be nonnegative")
        statement = statement.where(
            or_(
                Evidence.chunk_order > chunk_order,
                and_(
                    Evidence.chunk_order == chunk_order,
                    Evidence.evidence_id > evidence_id,
                ),
            )
        )
    statement = statement.order_by(Evidence.chunk_order, Evidence.evidence_id)
    return tuple(session.scalars(statement.limit(limit)).all())


def list_parser_executions(
    session: Session,
    *,
    source_id: UUID,
    limit: int,
    before: tuple[datetime, UUID] | None = None,
    source_version_id: UUID | None = None,
) -> tuple[ParserExecution, ...]:
    """List parser executions by ``(executed_at, parser_execution_id)`` descending."""

    _require_positive_limit(limit)
    conditions = [ParserExecution.source_id == source_id]
    if source_version_id is not None:
        conditions.append(ParserExecution.source_version_id == source_version_id)
    statement = select(ParserExecution).where(*conditions)
    if before is not None:
        executed_at, parser_execution_id = before
        _require_query_cursor_time(executed_at, field="before")
        statement = statement.where(
            or_(
                ParserExecution.executed_at < executed_at,
                and_(
                    ParserExecution.executed_at == executed_at,
                    ParserExecution.parser_execution_id < parser_execution_id,
                ),
            )
        )
    statement = statement.order_by(
        ParserExecution.executed_at.desc(),
        ParserExecution.parser_execution_id.desc(),
    )
    return tuple(session.scalars(statement.limit(limit)).all())


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _contract_json(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _contract_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_contract_json(item) for item in value]
    return _enum_value(value)


def _replace_contract_ids(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _replace_contract_ids(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_contract_ids(item, replacements) for item in value]
    if isinstance(value, str):
        return replacements.get(value, value)
    return value


def _replace_posting_section_evidence_ids(
    section: PostingSectionValue,
    replacements: Mapping[UUID, UUID],
) -> PostingSectionValue:
    return section.model_copy(
        update={
            "evidence_ids": [
                replacements.get(evidence_id, evidence_id) for evidence_id in section.evidence_ids
            ]
        }
    )


def _canonical_result(
    result: CollectionResult,
    replacements: Mapping[str, str],
) -> CollectionResult:
    payload = _replace_contract_ids(
        result.model_dump(mode="json"),
        replacements,
    )
    return CollectionResult.model_validate(payload)


def _canonical_event(
    session: Session,
    event: SourceEvent,
    replacements: Mapping[str, str],
) -> SourceEvent:
    payload = _replace_contract_ids(
        event.model_dump(mode="json"),
        replacements,
    )
    canonical_event = SourceEvent.model_validate(payload)
    if not isinstance(canonical_event.payload, SourceEnvelope):
        return canonical_event

    revision = session.get(
        ExtractionRevision,
        canonical_event.payload.extraction_revision_id,
    )
    if revision is None or revision.source_version_id != canonical_event.payload.source_version_id:
        raise InvalidPreparedCollection(
            "source envelope extraction revision does not match source version"
        )
    canonical_payload = canonical_event.payload.model_copy(
        update={"parser_version": revision.parser_version}
    )
    return canonical_event.model_copy(update={"payload": canonical_payload})


def _assert_immutable_row(
    row: Any,
    expected: Mapping[str, Any],
    *,
    entity: str,
) -> None:
    for field, expected_value in expected.items():
        if getattr(row, field) != expected_value:
            raise PersistenceConflict(f"{entity} has different immutable payload")


def _assert_public_payload(value: Any) -> None:
    if isinstance(value, Mapping):
        forbidden = _PUBLIC_EVENT_PRIVATE_KEYS.intersection(str(key) for key in value)
        if forbidden:
            names = ", ".join(sorted(forbidden))
            raise InvalidPreparedCollection(f"public source event contains private fields: {names}")
        for item in value.values():
            _assert_public_payload(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_public_payload(item)


def _validate_authority(
    *,
    grant: ExecutionAuthorityGrant,
    command: CollectionCommand,
    attempt_id: UUID,
    private_scope: PrivateWriteScope,
) -> None:
    expected = {
        "attempt_id": attempt_id,
        "owner_user_id": command.authenticated_owner_ref,
        "job_id": command.job_id,
        "command_id": command.command_id,
        "company_id": command.company_id,
        "source_id": command.source_id,
        "input_version": command.input_version,
        "execution_fence": command.execution_fence,
        "owner_deletion_epoch": command.owner_deletion_epoch,
    }
    if any(getattr(grant, field) != value for field, value in expected.items()):
        raise StaleExecution("execution authority does not match the collection command")
    if grant.private_scope != private_scope:
        raise StaleExecution("execution authority private scope does not match")


def bind_private_write_scope(
    proof: PrivateWriteScope | None,
    *,
    owner_user_id: UUID,
    owner_deletion_epoch: int,
    command_id: UUID | None = None,
    job_id: UUID | None = None,
    project_ref: str | None = None,
    bind_project_ref: bool = False,
) -> tuple[Literal["ACCOUNT", "PROJECT"], UUID | None]:
    """Validate an authenticated proof without deriving its scope from wire data."""

    from epick_engine.source_collection.private_scope import (
        PrivateScopeRejected,
        PrivateWriteScope,
    )

    if not isinstance(proof, PrivateWriteScope):
        raise PrivateScopeRejected("a trusted private write scope is required")
    proof.assert_bound_to(
        owner_user_id=owner_user_id,
        owner_deletion_epoch=owner_deletion_epoch,
        command_id=command_id,
        job_id=job_id,
    )
    if bind_project_ref:
        if proof.kind == "ACCOUNT":
            if project_ref is not None:
                raise PrivateScopeRejected("account scope does not match Project wire binding")
        elif project_ref != str(proof.project_id):
            raise PrivateScopeRejected("private scope Project binding does not match")
    return proof.kind, proof.project_id


def _validate_result_against_command(
    result: CollectionResult,
    command: CollectionCommand,
) -> None:
    matches = (
        result.command_id == command.command_id
        and result.job_id == command.job_id
        and result.input_version == command.input_version
        and result.source_id == command.source_id
        and result.policy_revision == command.policy_revision
    )
    if not matches:
        raise InvalidPreparedCollection("collection result does not match its command")


def _validate_posting_sections(revision: PreparedExtractionRevision) -> None:
    revision_evidence_ids = set(revision.evidence_ids)
    if len(revision_evidence_ids) != len(revision.evidence_ids):
        raise InvalidPreparedCollection("extraction evidence ids must be unique")

    section_keys: set[str] = set()
    for expected_order, section in enumerate(revision.posting_sections):
        if section.section_key in section_keys:
            raise InvalidPreparedCollection("posting section keys must be unique")
        section_keys.add(section.section_key)
        if section.order != expected_order:
            raise InvalidPreparedCollection(
                "posting section order must be contiguous and match payload order"
            )
        if not section.evidence_ids:
            raise InvalidPreparedCollection("posting sections require evidence")
        if len(set(section.evidence_ids)) != len(section.evidence_ids):
            raise InvalidPreparedCollection("posting section evidence ids must be unique")
        if not set(section.evidence_ids) <= revision_evidence_ids:
            raise InvalidPreparedCollection(
                "posting section evidence must belong to the extraction revision"
            )


def _validate_prepared_collection(
    command: CollectionCommand,
    prepared: PreparedCollectionCommit,
) -> None:
    _validate_result_against_command(prepared.result, command)
    _require_aware(prepared.finalized_at, field="finalized_at")

    version = prepared.source_version
    if version is not None:
        _require_aware(version.collected_at, field="source_version.collected_at")
        if version.source_id != command.source_id or version.company_id != command.company_id:
            raise InvalidPreparedCollection("prepared source version has wrong source scope")

    evidence_ids: set[UUID] = set()
    for item in prepared.evidence:
        if item.evidence_id in evidence_ids:
            raise InvalidPreparedCollection("prepared evidence ids must be unique")
        evidence_ids.add(item.evidence_id)
        if version is None or item.source_version_id != version.source_version_id:
            raise InvalidPreparedCollection("prepared evidence has wrong source version")

    retained_body = prepared.retained_body
    if retained_body is not None:
        _require_aware(retained_body.retained_at, field="retained_body.retained_at")
        if (
            version is None
            or retained_body.source_version_id != version.source_version_id
            or retained_body.source_id != command.source_id
        ):
            raise InvalidPreparedCollection("retained body has wrong source version")
        if retained_body.policy_decision_id != version.policy_decision_id:
            raise InvalidPreparedCollection("retained body has wrong policy decision")
        if not retained_body.normalization_version.strip():
            raise InvalidPreparedCollection("retained body normalization version is required")
        if not retained_body.necessity_reason.strip():
            raise InvalidPreparedCollection("retained body necessity reason is required")
        if not retained_body.retention_policy_version.strip():
            raise InvalidPreparedCollection("retained body retention policy version is required")
        if not retained_body.body_text.strip():
            raise InvalidPreparedCollection("retained body text is required")
        if retained_body.retention_limit_bytes <= 0:
            raise InvalidPreparedCollection("retained body byte limit must be positive")
        try:
            body_size = len(retained_body.body_text.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise InvalidPreparedCollection("retained body text must be valid UTF-8") from error
        if body_size > retained_body.retention_limit_bytes:
            raise InvalidPreparedCollection("retained body exceeds its applied byte limit")

    origin_relation_ids: set[UUID] = set()
    source_origin_keys: set[tuple[UUID, str, UUID]] = set()
    url_origin_keys: set[tuple[UUID, str, str]] = set()
    for origin in prepared.source_origins:
        if origin.origin_relation_id in origin_relation_ids:
            raise InvalidPreparedCollection("prepared source origin ids must be unique")
        origin_relation_ids.add(origin.origin_relation_id)
        if origin.source_id != command.source_id:
            raise InvalidPreparedCollection("source origin has wrong source")
        if origin.origin_source_id is None and origin.origin_url is None:
            raise InvalidPreparedCollection("source origin requires a source or URL target")
        if origin.origin_source_id == command.source_id:
            raise InvalidPreparedCollection("source origin cannot target itself")
        if origin.origin_url is not None and not origin.origin_url.strip():
            raise InvalidPreparedCollection("source origin URL cannot be blank")
        if not origin.relationship_kind.strip():
            raise InvalidPreparedCollection("source origin relationship kind is required")
        if not origin.verification_status.strip():
            raise InvalidPreparedCollection("source origin verification status is required")
        if not origin.evidence_ids:
            raise InvalidPreparedCollection("source origin requires evidence")
        if len(set(origin.evidence_ids)) != len(origin.evidence_ids):
            raise InvalidPreparedCollection("source origin evidence ids must be unique")
        if not set(origin.evidence_ids) <= evidence_ids:
            raise InvalidPreparedCollection(
                "source origin evidence must belong to this prepared commit"
            )
        if origin.origin_source_id is not None:
            source_key = (
                origin.source_id,
                origin.relationship_kind,
                origin.origin_source_id,
            )
            if source_key in source_origin_keys:
                raise InvalidPreparedCollection("prepared source origins must be naturally unique")
            source_origin_keys.add(source_key)
        if origin.origin_url is not None:
            url_key = (origin.source_id, origin.relationship_kind, origin.origin_url)
            if url_key in url_origin_keys:
                raise InvalidPreparedCollection("prepared source origins must be naturally unique")
            url_origin_keys.add(url_key)

    revision = prepared.extraction_revision
    if revision is not None:
        _require_aware(revision.created_at, field="extraction_revision.created_at")
        if version is None or revision.source_version_id != version.source_version_id:
            raise InvalidPreparedCollection("prepared extraction has wrong source version")
        if revision.posting_sections and version.source_type is not SourceType.JOB_POSTING:
            raise InvalidPreparedCollection("posting sections require a job_posting source version")
        if set(revision.evidence_ids) != evidence_ids:
            raise InvalidPreparedCollection(
                "extraction evidence ids must exactly match prepared evidence"
            )
        _validate_posting_sections(revision)

    execution = prepared.parser_execution
    if execution is not None:
        _require_aware(execution.executed_at, field="parser_execution.executed_at")
        if execution.source_id != command.source_id:
            raise InvalidPreparedCollection("parser execution has wrong source")
        if execution.source_version_id is not None and (
            version is None or execution.source_version_id != version.source_version_id
        ):
            raise InvalidPreparedCollection("parser execution has wrong source version")
        if execution.status == "succeeded":
            if (
                revision is None
                or execution.extraction_revision_id != revision.extraction_revision_id
            ):
                raise InvalidPreparedCollection(
                    "successful parser execution must reference its extraction"
                )
            if execution.output_hash != revision.output_hash:
                raise InvalidPreparedCollection(
                    "parser output hash must match its extraction revision"
                )
        elif execution.output_hash is not None or execution.extraction_revision_id is not None:
            raise InvalidPreparedCollection("failed parser execution cannot have output")

    observation = prepared.observation
    if observation is not None:
        snapshot = observation.snapshot
        _require_aware(snapshot.observed_at, field="observation.observed_at")
        if snapshot.source_id != command.source_id:
            raise InvalidPreparedCollection("observation has wrong source")
        if snapshot.source_version_id is not None and (
            version is None or snapshot.source_version_id != version.source_version_id
        ):
            raise InvalidPreparedCollection("observation has wrong source version")

    for reference in prepared.result.successful_source_refs:
        if version is None or revision is None:
            raise InvalidPreparedCollection("successful result requires version and extraction")
        if (
            reference.source_id != command.source_id
            or reference.source_version_id != version.source_version_id
            or reference.extraction_revision_id != revision.extraction_revision_id
        ):
            raise InvalidPreparedCollection("successful result has wrong persisted references")

    event_ids: set[UUID] = set()
    aggregate_revisions: set[int] = set()
    body_envelope_seen = False
    for event in prepared.events:
        if event.event_id in event_ids or event.aggregate_revision in aggregate_revisions:
            raise InvalidPreparedCollection("prepared source events must be unique")
        event_ids.add(event.event_id)
        aggregate_revisions.add(event.aggregate_revision)
        if event.aggregate_id != command.source_id:
            raise InvalidPreparedCollection("source event has wrong aggregate")
        if isinstance(event.payload, SourceEnvelope):
            if version is None or revision is None:
                raise InvalidPreparedCollection(
                    "source envelope requires a prepared source version and extraction revision"
                )
            if (
                event.payload.source_id != command.source_id
                or event.payload.company_id != command.company_id
            ):
                raise InvalidPreparedCollection("source envelope has wrong command scope")
            if (
                event.payload.source_version_id != version.source_version_id
                or event.payload.extraction_revision_id != revision.extraction_revision_id
            ):
                raise InvalidPreparedCollection(
                    "source envelope has wrong prepared version or extraction revision"
                )
            if retained_body is None:
                if event.payload.normalized_body_ref is not None:
                    raise InvalidPreparedCollection(
                        "source envelope cannot claim an unprepared retained body"
                    )
            else:
                body_envelope_seen = True
                if (
                    event.payload.retention_scope is not RetentionScope.NORMALIZED_BODY
                    or event.payload.normalized_body_ref != retained_body.body_id
                    or event.payload.policy.policy_decision_id != retained_body.policy_decision_id
                ):
                    raise InvalidPreparedCollection(
                        "source envelope retained body reference does not match prepared body"
                    )
        _assert_public_payload(_contract_json(event.payload))
    if retained_body is not None and not body_envelope_seen:
        raise InvalidPreparedCollection("retained body requires a version source event")
    if (
        prepared.source_version is not None or prepared.observation is not None
    ) and not prepared.events:
        raise InvalidPreparedCollection("public source writes require at least one source event")


def _replay_attempt(
    session: Session,
    *,
    command: CollectionCommand,
    attempt_id: UUID,
    private_scope: PrivateWriteScope,
    allow_deliver_resume_stage: bool = False,
    allow_initial_policy_revision: bool = False,
) -> CollectionResult | None:
    attempt = session.scalar(
        select(CollectionAttempt)
        .where(
            CollectionAttempt.owner_user_id == command.authenticated_owner_ref,
            CollectionAttempt.command_id == command.command_id,
        )
        .with_for_update()
    )
    if attempt is None:
        return None
    if (
        attempt.private_scope_kind != private_scope.kind
        or attempt.project_id != private_scope.project_id
    ):
        from epick_engine.source_collection.private_scope import PrivateScopeRejected

        raise PrivateScopeRejected("collection attempt private scope does not match")
    expected = {
        "attempt_id": attempt_id,
        "job_id": command.job_id,
        "project_id": private_scope.project_id,
        "input_version": command.input_version,
        "target_ref": str(command.source_id),
        "purpose_ref": str(command.purpose_ref),
        "core_source_decision": command.model_dump(mode="json")["core_source_decision"],
        "resume_stage": command.resume_stage.value,
        "execution_fence": command.execution_fence,
        "owner_deletion_epoch": command.owner_deletion_epoch,
        "policy_revision": command.policy_revision,
    }
    is_initial_policy_replay = (
        allow_initial_policy_revision
        and command.resume_stage is CollectionStage.POLICY
        and command.policy_revision is None
    )
    if allow_deliver_resume_stage and command.resume_stage is CollectionStage.DELIVER:
        expected.pop("resume_stage")
    if is_initial_policy_replay:
        expected.pop("policy_revision")
    _assert_immutable_row(attempt, expected, entity="collection attempt")
    if (attempt.result_payload is None) != (attempt.finalized_at is None):
        raise PersistenceConflict("collection attempt finalization state is inconsistent")
    if attempt.result_payload is None:
        raise PersistenceConflict("collection attempt is not finalized")
    try:
        result = CollectionResult.model_validate(attempt.result_payload)
    except ValueError as error:
        raise PersistenceConflict("stored collection result is invalid") from error
    result_payload = result.model_dump(mode="json")
    if attempt.result_payload != result_payload:
        raise PersistenceConflict("stored collection result is inconsistent")
    _assert_immutable_row(
        attempt,
        {
            "result_version": result.result_version,
            "checkpoint_ref": result.checkpoint_ref,
            "result_refs": result_payload["successful_source_refs"],
            "failures": result_payload["failures"],
            "required_actions": result_payload["required_actions"],
        },
        entity="collection attempt result",
    )
    replay_command = command
    if is_initial_policy_replay:
        _assert_immutable_row(
            attempt,
            {"policy_revision": result.policy_revision},
            entity="collection attempt",
        )
        replay_command = command.model_copy(update={"policy_revision": result.policy_revision})
    _validate_result_against_command(result, replay_command)
    return result


def _assert_policy_decision(
    session: Session,
    *,
    policy_decision_id: UUID,
    command: CollectionCommand,
) -> SourcePolicyDecision:
    decision = session.get(SourcePolicyDecision, policy_decision_id)
    if decision is None or decision.source_id != command.source_id:
        raise InvalidPreparedCollection("policy decision does not belong to the source")
    if command.policy_revision is None or decision.revision != command.policy_revision:
        raise InvalidPreparedCollection("policy decision revision does not match command")
    return decision


def _assert_retained_body_policy(
    decision: SourcePolicyDecision,
) -> None:
    if decision.collection_permission != "allowed" or decision.body_storage_permission != "allowed":
        raise InvalidPreparedCollection(
            "retained body collection and storage are not allowed by policy"
        )


def _assert_source_envelope_event_policy(
    prepared: PreparedCollectionCommit,
    decision: SourcePolicyDecision,
) -> None:
    for event in prepared.events:
        if not isinstance(event.payload, SourceEnvelope):
            continue
        policy = event.payload.policy
        matches = (
            policy.policy_decision_id == decision.policy_decision_id
            and policy.official_status.value == decision.official_status
            and policy.access_class.value == decision.access_class
            and policy.collection_permission.value == decision.collection_permission
            and policy.excerpt_storage_permission.value == decision.excerpt_storage_permission
            and policy.body_storage_permission.value == decision.body_storage_permission
            and policy.redistribution_permission.value == decision.redistribution_permission
            and policy.checked_at == decision.checked_at
            and policy.policy_version == decision.policy_version
        )
        if not matches:
            raise InvalidPreparedCollection(
                "source envelope policy does not match source version policy decision"
            )


def _resolve_source_version(
    session: Session,
    value: PreparedSourceVersion,
) -> SourceVersion:
    expected = {
        "source_version_id": value.source_version_id,
        "source_id": value.source_id,
        "company_id": value.company_id,
        "title": value.title,
        "source_type": value.source_type.value,
        "canonical_url": value.canonical_url,
        "content_hash": value.content_hash,
        "hash_profile_version": value.hash_profile_version,
        "representation": value.representation.value,
        "first_parser_version": value.first_parser_version,
        "collected_at": value.collected_at,
        "published_at": value.published_at.model_dump(mode="json"),
        "valid_from": value.valid_from.model_dump(mode="json"),
        "valid_to": value.valid_to.model_dump(mode="json"),
        "language": value.language,
        "policy_decision_id": value.policy_decision_id,
    }
    existing = session.get(SourceVersion, value.source_version_id)
    if existing is not None:
        _assert_immutable_row(existing, expected, entity="source version")
        return existing
    natural = session.scalar(
        select(SourceVersion).where(
            SourceVersion.source_id == value.source_id,
            SourceVersion.representation == value.representation.value,
            SourceVersion.hash_profile_version == value.hash_profile_version,
            SourceVersion.content_hash == value.content_hash,
        )
    )
    if natural is not None:
        return natural
    created = SourceVersion(**expected)
    session.add(created)
    session.flush()
    return created


def resolve_job_posting(
    session: Session,
    *,
    candidate_job_posting_id: UUID,
    source_id: UUID,
    company_id: UUID,
) -> JobPosting:
    """Create or replay the stable JobPosting identity for one locked Source."""

    source = session.scalar(select(Source).where(Source.source_id == source_id).with_for_update())
    if source is None:
        raise ValueError("source does not exist")
    if source.company_id != company_id:
        raise ValueError("source does not belong to the requested company")
    if source.source_type != SourceType.JOB_POSTING.value:
        raise ValueError("source is not a job posting")

    inserted_id = session.scalar(
        postgresql_insert(JobPosting)
        .values(
            job_posting_id=candidate_job_posting_id,
            company_id=company_id,
            source_id=source_id,
        )
        .on_conflict_do_nothing(index_elements=["source_id"])
        .returning(JobPosting.job_posting_id)
    )
    if inserted_id is not None:
        created = session.get(JobPosting, inserted_id)
        if created is None:
            raise RuntimeError("inserted job posting row was not found")
        return created

    existing = session.scalar(
        select(JobPosting).where(JobPosting.source_id == source_id).with_for_update()
    )
    if existing is None:
        raise RuntimeError("job posting conflict row was not found")
    return existing


def _resolve_evidence(session: Session, value: PreparedEvidence) -> Evidence:
    expected = {
        "evidence_id": value.evidence_id,
        "source_version_id": value.source_version_id,
        "evidence_key": value.evidence_key,
        "section_title": value.section_title,
        "text_excerpt": value.text_excerpt,
        "locator": value.locator.model_dump(mode="json"),
        "chunk_order": value.chunk_order,
        "origin_kind": value.origin_kind,
    }
    existing = session.get(Evidence, value.evidence_id)
    if existing is not None:
        _assert_immutable_row(existing, expected, entity="evidence")
        return existing
    natural = session.scalar(
        select(Evidence).where(
            Evidence.source_version_id == value.source_version_id,
            Evidence.evidence_key == value.evidence_key,
        )
    )
    if natural is not None:
        semantic = {
            field: expected[field]
            for field in (
                "section_title",
                "text_excerpt",
                "locator",
                "chunk_order",
                "origin_kind",
            )
        }
        _assert_immutable_row(natural, semantic, entity="evidence")
        return natural
    created = Evidence(**expected)
    session.add(created)
    session.flush()
    return created


def _resolve_retained_body(
    session: Session,
    value: PreparedRetainedBody,
) -> RetainedBody:
    expected = {
        "body_id": value.body_id,
        "source_version_id": value.source_version_id,
        "source_id": value.source_id,
        "normalization_version": value.normalization_version,
        "body_text": value.body_text,
        "necessity_reason": value.necessity_reason,
        "policy_decision_id": value.policy_decision_id,
        "retained_at": value.retained_at,
        "retention_policy_version": value.retention_policy_version,
        "retention_limit_bytes": value.retention_limit_bytes,
    }
    existing = session.get(RetainedBody, value.body_id)
    if existing is not None:
        _assert_immutable_row(existing, expected, entity="retained body")
        return existing
    natural = session.scalar(
        select(RetainedBody).where(
            RetainedBody.source_version_id == value.source_version_id,
            RetainedBody.normalization_version == value.normalization_version,
        )
    )
    if natural is not None:
        semantic = {field: expected[field] for field in ("source_id", "body_text")}
        _assert_immutable_row(natural, semantic, entity="retained body")
        return natural
    created = RetainedBody(**expected)
    session.add(created)
    session.flush()
    return created


def _resolve_source_origin(
    session: Session,
    value: PreparedSourceOrigin,
) -> SourceOrigin:
    expected = {
        "origin_relation_id": value.origin_relation_id,
        "source_id": value.source_id,
        "origin_source_id": value.origin_source_id,
        "origin_url": value.origin_url,
        "relationship_kind": value.relationship_kind,
        "verification_status": value.verification_status,
    }
    existing = session.get(SourceOrigin, value.origin_relation_id)
    if existing is not None:
        _assert_immutable_row(existing, expected, entity="source origin")
        return existing

    natural_predicates = []
    if value.origin_source_id is not None:
        natural_predicates.append(
            and_(
                SourceOrigin.source_id == value.source_id,
                SourceOrigin.relationship_kind == value.relationship_kind,
                SourceOrigin.origin_source_id == value.origin_source_id,
            )
        )
    if value.origin_url is not None:
        natural_predicates.append(
            and_(
                SourceOrigin.source_id == value.source_id,
                SourceOrigin.relationship_kind == value.relationship_kind,
                SourceOrigin.origin_url == value.origin_url,
            )
        )
    naturals = list(session.scalars(select(SourceOrigin).where(or_(*natural_predicates))).all())
    if naturals:
        natural = naturals[0]
        if any(item.origin_relation_id != natural.origin_relation_id for item in naturals[1:]):
            raise PersistenceConflict("source origin natural targets map to different rows")
        semantic = {field: expected[field] for field in expected if field != "origin_relation_id"}
        _assert_immutable_row(natural, semantic, entity="source origin")
        return natural
    created = SourceOrigin(**expected)
    session.add(created)
    session.flush()
    return created


def _link_source_origin_evidence(
    session: Session,
    *,
    origin_relation_id: UUID,
    evidence_ids: tuple[UUID, ...],
) -> None:
    for evidence_id in evidence_ids:
        session.execute(
            postgresql_insert(SourceOriginEvidence)
            .values(
                origin_relation_id=origin_relation_id,
                evidence_id=evidence_id,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    SourceOriginEvidence.origin_relation_id,
                    SourceOriginEvidence.evidence_id,
                ]
            )
        )


def _resolve_extraction_revision(
    session: Session,
    value: PreparedExtractionRevision,
) -> tuple[ExtractionRevision, bool]:
    expected = {
        "extraction_revision_id": value.extraction_revision_id,
        "source_version_id": value.source_version_id,
        "parser_version": value.parser_version,
        "output_hash": value.output_hash,
        "created_at": value.created_at,
        "extraction_status": value.extraction_status.value,
        "posting_sections": [item.model_dump(mode="json") for item in value.posting_sections],
        "date_values": {
            key: item.model_dump(mode="json") for key, item in value.date_values.items()
        },
        "limitations": list(value.limitations),
    }
    existing = session.get(ExtractionRevision, value.extraction_revision_id)
    if existing is not None:
        _assert_immutable_row(existing, expected, entity="extraction revision")
        return existing, False
    natural = session.scalar(
        select(ExtractionRevision).where(
            ExtractionRevision.source_version_id == value.source_version_id,
            ExtractionRevision.output_hash == value.output_hash,
        )
    )
    if natural is not None:
        semantic = {
            field: expected[field]
            for field in (
                "extraction_status",
                "posting_sections",
                "date_values",
                "limitations",
            )
        }
        _assert_immutable_row(
            natural,
            semantic,
            entity="extraction revision",
        )
        return natural, False
    created = ExtractionRevision(**expected)
    session.add(created)
    session.flush()
    return created, True


def _link_extraction_evidence(
    session: Session,
    *,
    revision: PreparedExtractionRevision,
    revision_was_created: bool,
) -> None:
    if revision_was_created:
        session.add_all(
            [
                ExtractionRevisionEvidence(
                    extraction_revision_id=revision.extraction_revision_id,
                    evidence_id=evidence_id,
                    source_version_id=revision.source_version_id,
                )
                for evidence_id in revision.evidence_ids
            ]
        )
        session.flush()
        return

    expected_links = {
        evidence_id: revision.source_version_id for evidence_id in revision.evidence_ids
    }
    existing_links = {
        evidence_id: source_version_id
        for evidence_id, source_version_id in session.execute(
            select(
                ExtractionRevisionEvidence.evidence_id,
                ExtractionRevisionEvidence.source_version_id,
            ).where(
                ExtractionRevisionEvidence.extraction_revision_id == revision.extraction_revision_id
            )
        )
    }
    if existing_links != expected_links:
        raise PersistenceConflict("extraction evidence links have different immutable payload")


def _persist_posting_sections(
    session: Session,
    *,
    revision: PreparedExtractionRevision,
    revision_was_created: bool,
) -> None:
    expected_sections = tuple(revision.posting_sections)
    if not revision_was_created:
        existing_sections = tuple(
            session.scalars(
                select(PostingSection)
                .where(PostingSection.extraction_revision_id == revision.extraction_revision_id)
                .order_by(PostingSection.order)
            )
        )
        if len(existing_sections) != len(expected_sections):
            raise PersistenceConflict("posting sections have different immutable keys")

        for existing, expected in zip(existing_sections, expected_sections, strict=True):
            expected_scalar = {
                "section_key": expected.section_key,
                "kind": expected.kind.value,
                "heading_raw": expected.heading_raw,
                "text_raw": expected.text_raw,
                "order": expected.order,
                "relation_text": expected.relation_text,
            }
            _assert_immutable_row(existing, expected_scalar, entity="posting section")
            actual_evidence = tuple(
                session.execute(
                    select(
                        PostingSectionEvidence.evidence_id,
                        PostingSectionEvidence.evidence_order,
                    )
                    .where(
                        PostingSectionEvidence.extraction_revision_id
                        == revision.extraction_revision_id,
                        PostingSectionEvidence.section_key == expected.section_key,
                    )
                    .order_by(PostingSectionEvidence.evidence_order)
                )
            )
            expected_evidence = tuple(enumerate(expected.evidence_ids))
            if actual_evidence != tuple(
                (evidence_id, evidence_order) for evidence_order, evidence_id in expected_evidence
            ):
                raise PersistenceConflict("posting section evidence has different immutable order")
        return

    session.add_all(
        [
            PostingSection(
                extraction_revision_id=revision.extraction_revision_id,
                section_key=section.section_key,
                kind=section.kind.value,
                heading_raw=section.heading_raw,
                text_raw=section.text_raw,
                order=section.order,
                relation_text=section.relation_text,
            )
            for section in expected_sections
        ]
    )
    session.flush()
    session.add_all(
        [
            PostingSectionEvidence(
                extraction_revision_id=revision.extraction_revision_id,
                evidence_id=evidence_id,
                section_key=section.section_key,
                evidence_order=evidence_order,
            )
            for section in expected_sections
            for evidence_order, evidence_id in enumerate(section.evidence_ids)
        ]
    )
    session.flush()


def _resolve_parser_execution(
    session: Session,
    value: PreparedParserExecution,
) -> ParserExecution:
    expected = {
        "parser_execution_id": value.parser_execution_id,
        "source_id": value.source_id,
        "source_version_id": value.source_version_id,
        "content_hash": value.content_hash,
        "parser_version": value.parser_version,
        "output_hash": value.output_hash,
        "extraction_revision_id": value.extraction_revision_id,
        "status": value.status,
        "executed_at": value.executed_at,
    }
    existing = session.get(ParserExecution, value.parser_execution_id)
    if existing is not None:
        _assert_immutable_row(existing, expected, entity="parser execution")
        return existing
    created = ParserExecution(**expected)
    session.add(created)
    session.flush()
    return created


def _resolve_observation(
    session: Session,
    value: PreparedSourceObservation,
) -> SourceObservation:
    snapshot = value.snapshot
    expected = {
        "observation_id": snapshot.observation_id,
        "source_id": snapshot.source_id,
        "source_version_id": snapshot.source_version_id,
        "policy_decision_id": snapshot.policy_decision_id,
        "observed_at": snapshot.observed_at,
        "access_class": snapshot.access_class.value,
        "acquisition_status": (
            snapshot.acquisition_status.value if snapshot.acquisition_status is not None else None
        ),
        "http_status": snapshot.http_status,
        "checked_url": str(snapshot.checked_url),
        "retrieval_validator": (
            _contract_json(value.retrieval_validator)
            if value.retrieval_validator is not None
            else None
        ),
        "error_code": snapshot.error_code,
        "representation": (
            snapshot.representation.value if snapshot.representation is not None else None
        ),
    }
    existing = session.get(SourceObservation, snapshot.observation_id)
    if existing is not None:
        _assert_immutable_row(existing, expected, entity="source observation")
        return existing
    created = SourceObservation(**expected)
    session.add(created)
    session.flush()
    return created


def _resolve_outbox_event(
    session: Session,
    event: SourceEvent,
    *,
    aggregate_revision: int,
) -> OutboxEvent:
    payload = event.payload.model_dump(mode="json")
    _assert_public_payload(payload)
    immutable = {
        "event_id": event.event_id,
        "aggregate_id": event.aggregate_id,
        "event_type": event.event_type.value,
        "schema_version": event.schema_version,
        "payload": payload,
        "occurred_at": event.occurred_at,
    }
    existing = session.get(OutboxEvent, event.event_id)
    if existing is not None:
        _assert_immutable_row(existing, immutable, entity="outbox event")
        return existing
    revision_event = session.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_id == event.aggregate_id,
            OutboxEvent.aggregate_revision == aggregate_revision,
        )
    )
    if revision_event is not None:
        raise PersistenceConflict("aggregate revision already maps to another event")
    created = OutboxEvent(
        **immutable,
        aggregate_revision=aggregate_revision,
        delivery_state="pending",
    )
    session.add(created)
    session.flush()
    return created


def record_source_restriction_public_event(
    session: Session,
    *,
    snapshot: SourceRestrictionSnapshot,
) -> SourceEvent:
    """Atomically append the public event for one newly stored restriction revision.

    The Source row serializes this allocation with collection commits and other
    restriction mutations. The caller owns the transaction and must not publish
    to W3 until it has committed.
    """

    source = session.scalar(
        select(Source)
        .where(Source.source_id == snapshot.source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        source is None
        or get_source_restriction_revision(
            session,
            restriction_id=snapshot.restriction_id,
            restriction_revision=snapshot.restriction_revision,
        )
        != snapshot
    ):
        raise PersistenceConflict("restriction event requires a stored immutable revision")
    next_revision = (
        session.scalar(
            select(func.max(OutboxEvent.aggregate_revision)).where(
                OutboxEvent.aggregate_id == snapshot.source_id
            )
        )
        or 0
    ) + 1
    event = SourceEvent(
        event_id=uuid4(),
        event_type=SourceEventType.RESTRICTION_CHANGED,
        schema_version="w2.source.v1",
        aggregate_id=snapshot.source_id,
        aggregate_revision=next_revision,
        occurred_at=snapshot.changed_at,
        payload=snapshot,
    )
    _resolve_outbox_event(session, event, aggregate_revision=next_revision)
    return event


def _persist_canonical_entities(
    session: Session,
    prepared: PreparedCollectionCommit,
) -> PreparedCollectionCommit:
    replacements: dict[str, str] = {}

    canonical_version = prepared.source_version
    if canonical_version is not None:
        persisted_version = _resolve_source_version(session, canonical_version)
        replacements[str(canonical_version.source_version_id)] = str(
            persisted_version.source_version_id
        )
        canonical_version = replace(
            canonical_version,
            source_version_id=persisted_version.source_version_id,
        )

    canonical_evidence: list[PreparedEvidence] = []
    evidence_id_replacements: dict[UUID, UUID] = {}
    for item in prepared.evidence:
        if canonical_version is None:
            raise InvalidPreparedCollection("prepared evidence requires a source version")
        adjusted = replace(
            item,
            source_version_id=canonical_version.source_version_id,
        )
        persisted_evidence = _resolve_evidence(session, adjusted)
        replacements[str(item.evidence_id)] = str(persisted_evidence.evidence_id)
        evidence_id_replacements[item.evidence_id] = persisted_evidence.evidence_id
        canonical_evidence.append(replace(adjusted, evidence_id=persisted_evidence.evidence_id))

    canonical_retained_body = prepared.retained_body
    if canonical_retained_body is not None:
        if canonical_version is None:
            raise InvalidPreparedCollection("prepared retained body requires a source version")
        adjusted_body = replace(
            canonical_retained_body,
            source_version_id=canonical_version.source_version_id,
        )
        persisted_body = _resolve_retained_body(session, adjusted_body)
        replacements[str(canonical_retained_body.body_id)] = str(persisted_body.body_id)
        canonical_retained_body = PreparedRetainedBody(
            body_id=persisted_body.body_id,
            source_version_id=persisted_body.source_version_id,
            source_id=persisted_body.source_id,
            normalization_version=persisted_body.normalization_version,
            body_text=persisted_body.body_text,
            necessity_reason=persisted_body.necessity_reason,
            policy_decision_id=persisted_body.policy_decision_id,
            retained_at=persisted_body.retained_at,
            retention_policy_version=persisted_body.retention_policy_version,
            retention_limit_bytes=persisted_body.retention_limit_bytes,
        )

    canonical_origins: list[PreparedSourceOrigin] = []
    for origin in prepared.source_origins:
        adjusted_evidence_ids = tuple(
            evidence_id_replacements[evidence_id] for evidence_id in origin.evidence_ids
        )
        adjusted_origin = replace(origin, evidence_ids=adjusted_evidence_ids)
        persisted_origin = _resolve_source_origin(session, adjusted_origin)
        replacements[str(origin.origin_relation_id)] = str(persisted_origin.origin_relation_id)
        canonical_origin = replace(
            adjusted_origin,
            origin_relation_id=persisted_origin.origin_relation_id,
        )
        _link_source_origin_evidence(
            session,
            origin_relation_id=persisted_origin.origin_relation_id,
            evidence_ids=adjusted_evidence_ids,
        )
        canonical_origins.append(canonical_origin)

    canonical_revision = prepared.extraction_revision
    if canonical_revision is not None:
        if canonical_version is None:
            raise InvalidPreparedCollection("prepared extraction requires a source version")
        adjusted_evidence_ids = tuple(
            evidence_id_replacements.get(evidence_id, evidence_id)
            for evidence_id in canonical_revision.evidence_ids
        )
        adjusted_sections = tuple(
            _replace_posting_section_evidence_ids(
                section,
                evidence_id_replacements,
            )
            for section in canonical_revision.posting_sections
        )
        adjusted_revision = replace(
            canonical_revision,
            source_version_id=canonical_version.source_version_id,
            evidence_ids=adjusted_evidence_ids,
            posting_sections=adjusted_sections,
        )
        _validate_posting_sections(adjusted_revision)
        persisted_revision, revision_was_created = _resolve_extraction_revision(
            session,
            adjusted_revision,
        )
        replacements[str(canonical_revision.extraction_revision_id)] = str(
            persisted_revision.extraction_revision_id
        )
        canonical_revision = replace(
            adjusted_revision,
            extraction_revision_id=persisted_revision.extraction_revision_id,
        )
        _link_extraction_evidence(
            session,
            revision=canonical_revision,
            revision_was_created=revision_was_created,
        )
        _persist_posting_sections(
            session,
            revision=canonical_revision,
            revision_was_created=revision_was_created,
        )

    canonical_execution = prepared.parser_execution
    if canonical_execution is not None:
        canonical_execution = replace(
            canonical_execution,
            source_version_id=(
                canonical_version.source_version_id
                if canonical_execution.source_version_id is not None
                and canonical_version is not None
                else None
            ),
            extraction_revision_id=(
                canonical_revision.extraction_revision_id
                if canonical_execution.extraction_revision_id is not None
                and canonical_revision is not None
                else None
            ),
        )
        _resolve_parser_execution(session, canonical_execution)

    canonical_observation = prepared.observation
    if canonical_observation is not None:
        snapshot = canonical_observation.snapshot
        if snapshot.source_version_id is not None:
            if canonical_version is None:
                raise InvalidPreparedCollection("version observation requires a source version")
            snapshot = snapshot.model_copy(
                update={"source_version_id": canonical_version.source_version_id}
            )
        canonical_observation = replace(
            canonical_observation,
            snapshot=snapshot,
        )
        _resolve_observation(session, canonical_observation)

    canonical_result = _canonical_result(prepared.result, replacements)
    canonical_events = tuple(
        _canonical_event(session, event, replacements) for event in prepared.events
    )
    return replace(
        prepared,
        result=canonical_result,
        source_version=canonical_version,
        evidence=tuple(canonical_evidence),
        retained_body=canonical_retained_body,
        source_origins=tuple(canonical_origins),
        extraction_revision=canonical_revision,
        parser_execution=canonical_execution,
        observation=canonical_observation,
        events=canonical_events,
    )


def _lock_public_source(session: Session, command: CollectionCommand) -> Source:
    source = session.scalar(
        select(Source)
        .where(Source.source_id == command.source_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if source is None or source.company_id != command.company_id:
        raise InvalidPreparedCollection("source does not belong to the command company")
    return source


def persist_canonical_public_commit(
    session: Session,
    *,
    command: CollectionCommand,
    prepared: PreparedCollectionCommit,
) -> CanonicalPublicCommit:
    """Persist canonical public rows and authoritative Source event revisions."""

    source = _lock_public_source(session, command)
    _validate_prepared_collection(command, prepared)

    source_version_policy: SourcePolicyDecision | None = None
    if prepared.source_version is not None:
        if (
            source.source_type != prepared.source_version.source_type.value
            or source.canonical_url != prepared.source_version.canonical_url
        ):
            raise InvalidPreparedCollection(
                "prepared source version does not match source identity"
            )
        source_version_policy = _assert_policy_decision(
            session,
            policy_decision_id=prepared.source_version.policy_decision_id,
            command=command,
        )
        _assert_source_envelope_event_policy(prepared, source_version_policy)
    if prepared.observation is not None:
        policy_decision_id = prepared.observation.snapshot.policy_decision_id
        if policy_decision_id is not None:
            _assert_policy_decision(
                session,
                policy_decision_id=policy_decision_id,
                command=command,
            )
    if prepared.retained_body is not None:
        if source_version_policy is None:
            raise InvalidPreparedCollection(
                "retained body requires a prepared source version policy"
            )
        _assert_retained_body_policy(source_version_policy)

    origin_source_ids = sorted(
        {
            origin.origin_source_id
            for origin in prepared.source_origins
            if origin.origin_source_id is not None
        },
        key=str,
    )
    for origin_source_id in origin_source_ids:
        origin_source = session.scalar(
            select(Source)
            .where(Source.source_id == origin_source_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if origin_source is None:
            raise InvalidPreparedCollection("source origin target source does not exist")

    canonical = _persist_canonical_entities(session, prepared)
    next_revision = (
        session.scalar(
            select(func.max(OutboxEvent.aggregate_revision)).where(
                OutboxEvent.aggregate_id == command.source_id
            )
        )
        or 0
    )
    canonical_events: list[SourceEvent] = []
    for event in canonical.events:
        existing_event = session.get(OutboxEvent, event.event_id, populate_existing=True)
        if existing_event is not None:
            authoritative_revision = existing_event.aggregate_revision
        else:
            next_revision += 1
            authoritative_revision = next_revision
        authoritative_event = event.model_copy(
            update={"aggregate_revision": authoritative_revision}
        )
        _resolve_outbox_event(
            session,
            authoritative_event,
            aggregate_revision=authoritative_revision,
        )
        canonical_events.append(authoritative_event)

    canonical = replace(canonical, events=tuple(canonical_events))
    session.flush()
    return CanonicalPublicCommit(source=source, prepared=canonical)


def resolve_request_deduplication(
    session: Session,
    *,
    private_scope: PrivateWriteScope | None = None,
    owner_user_id: UUID,
    operation: str,
    idempotency_key: str,
    request_hash: str,
    accepted_resource_ref: str,
    input_version: int,
    created_at: datetime | None = None,
) -> RequestDeduplication:
    """Atomically create or replay one owner-scoped request idempotency record."""

    if not operation.strip() or not idempotency_key.strip() or not request_hash.strip():
        raise ValueError("operation, idempotency_key, and request_hash are required")
    if input_version <= 0:
        raise ValueError("input_version must be positive")
    timestamp = created_at or datetime.now(UTC)
    _require_aware(timestamp, field="created_at")
    scope_kind, project_id = bind_private_write_scope(
        private_scope,
        owner_user_id=owner_user_id,
        owner_deletion_epoch=(
            private_scope.owner_deletion_epoch if private_scope is not None else -1
        ),
    )
    from epick_engine.source_collection.private_scope import lock_private_write_scope

    assert private_scope is not None
    lock_private_write_scope(session, private_scope)
    row_id = uuid4()
    inserted_id = session.scalar(
        postgresql_insert(RequestDeduplication)
        .values(
            request_deduplication_id=row_id,
            owner_user_id=owner_user_id,
            private_scope_kind=scope_kind,
            project_id=project_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            accepted_resource_ref=accepted_resource_ref,
            input_version=input_version,
            created_at=timestamp,
        )
        .on_conflict_do_nothing(index_elements=["owner_user_id", "operation", "idempotency_key"])
        .returning(RequestDeduplication.request_deduplication_id)
    )
    if inserted_id is not None:
        created = session.get(RequestDeduplication, inserted_id)
        if created is None:
            raise RuntimeError("inserted request deduplication row was not found")
        return created
    existing = session.scalar(
        select(RequestDeduplication)
        .where(
            RequestDeduplication.owner_user_id == owner_user_id,
            RequestDeduplication.operation == operation,
            RequestDeduplication.idempotency_key == idempotency_key,
        )
        .with_for_update()
    )
    if existing is None:
        raise RuntimeError("request deduplication conflict row was not found")
    if existing.request_hash != request_hash:
        raise PersistenceConflict("idempotency key was reused with a different request")
    if existing.private_scope_kind != scope_kind or existing.project_id != project_id:
        raise PersistenceConflict("idempotency key private scope does not match")
    return existing


def _project_id(command: CollectionCommand) -> UUID | None:
    if command.project_ref is None:
        return None
    try:
        return UUID(command.project_ref)
    except ValueError as error:
        raise InvalidPreparedCollection("project_ref must be a UUID when present") from error


def _create_collection_attempt(
    session: Session,
    *,
    command: CollectionCommand,
    prepared: PreparedCollectionCommit,
    private_scope: PrivateWriteScope,
) -> CollectionAttempt:
    result_payload = prepared.result.model_dump(mode="json")
    created = CollectionAttempt(
        attempt_id=prepared.attempt_id,
        owner_user_id=command.authenticated_owner_ref,
        job_id=command.job_id,
        project_id=private_scope.project_id,
        private_scope_kind=private_scope.kind,
        command_id=command.command_id,
        input_version=command.input_version,
        target_ref=str(command.source_id),
        purpose_ref=str(command.purpose_ref),
        core_source_decision=command.model_dump(mode="json")["core_source_decision"],
        resume_stage=command.resume_stage.value,
        policy_revision=command.policy_revision,
        result_version=prepared.result.result_version,
        execution_fence=command.execution_fence,
        owner_deletion_epoch=command.owner_deletion_epoch,
        parser_execution_id=(
            prepared.parser_execution.parser_execution_id
            if prepared.parser_execution is not None
            else None
        ),
        checkpoint_ref=prepared.result.checkpoint_ref,
        result_refs=result_payload["successful_source_refs"],
        failures=result_payload["failures"],
        required_actions=result_payload["required_actions"],
        result_payload=result_payload,
        finalized_at=prepared.finalized_at,
    )
    session.add(created)
    session.flush()
    return created


def replay_committed_collection(
    session_factory: Callable[[], Session],
    *,
    command: CollectionCommand,
    attempt_id: UUID,
    lock_authority: ExecutionAuthorityLocker,
    private_scope: PrivateWriteScope | None = None,
) -> CollectionResult | None:
    """Return a finalized committed result after same-transaction authority validation."""

    bind_private_write_scope(
        private_scope,
        owner_user_id=command.authenticated_owner_ref,
        owner_deletion_epoch=command.owner_deletion_epoch,
        command_id=command.command_id,
        job_id=command.job_id,
        project_ref=command.project_ref,
        bind_project_ref=True,
    )
    assert private_scope is not None
    with session_scope(session_factory) as session:
        with session.begin():
            from epick_engine.source_collection.private_scope import lock_private_write_scope

            lock_private_write_scope(session, private_scope)
            grant = lock_authority(
                session,
                command=command,
                attempt_id=attempt_id,
            )
            _validate_authority(
                grant=grant,
                command=command,
                attempt_id=attempt_id,
                private_scope=private_scope,
            )
            return _replay_attempt(
                session,
                command=command,
                attempt_id=attempt_id,
                private_scope=private_scope,
                allow_deliver_resume_stage=True,
                allow_initial_policy_revision=True,
            )


def _validate_effective_collection_command(
    original: CollectionCommand,
    effective: CollectionCommand,
    *,
    effective_policy_revision: int,
) -> None:
    if not isinstance(effective, CollectionCommand):
        raise InvalidPreparedCollection("effective collection command is invalid")
    expected = original.model_copy(update={"policy_revision": effective_policy_revision})
    persist_expected = expected.model_copy(update={"resume_stage": CollectionStage.PERSIST})
    if effective not in (expected, persist_expected):
        raise InvalidPreparedCollection(
            "effective collection command does not match the reserved dispatch"
        )


def stage_private_result(
    session: Session,
    command: CollectionCommand,
    result: CollectionResult,
    *,
    message_id: UUID,
    occurred_at: datetime,
    stage_kind: Literal["PRIVATE_ONLY", "COLLECTION"] = "PRIVATE_ONLY",
    private_scope: PrivateWriteScope | None = None,
) -> StagedResultProposal:
    """Lazy compatibility seam for the circular private-store dependency."""

    from epick_engine.source_collection.commit_gate_store import (
        stage_private_result as store_private_result,
    )

    return store_private_result(
        session,
        command,
        result,
        message_id=message_id,
        occurred_at=occurred_at,
        stage_kind=stage_kind,
        private_scope=private_scope,
    )


def commit_collection_candidate(
    session_factory: Callable[[], Session],
    dispatch: W1Dispatch,
    effective_command: CollectionCommand,
    prepared: PreparedCollectionCommit,
    *,
    claim_token: UUID,
    staged_message_id: UUID,
    occurred_at: datetime,
    private_scope: PrivateWriteScope | None = None,
) -> StagedResultProposal:
    """Persist public evidence and its private COLLECTION stage atomically."""

    from epick_engine.source_collection.commit_gate_store import lock_private_command
    from epick_engine.source_collection.source_runtime_store import (
        CollectionRuntimeConflict,
        _assert_bound_attempt,
        _validated_dispatch,
        dispatch_digest,
    )

    validated_dispatch = _validated_dispatch(dispatch)
    original_command = validated_dispatch.payload
    digest = dispatch_digest(validated_dispatch)
    bind_private_write_scope(
        private_scope,
        owner_user_id=original_command.authenticated_owner_ref,
        owner_deletion_epoch=original_command.owner_deletion_epoch,
        command_id=original_command.command_id,
        job_id=original_command.job_id,
        project_ref=original_command.project_ref,
        bind_project_ref=True,
    )
    try:
        with session_scope(session_factory) as session:
            with session.begin():
                from epick_engine.source_collection.private_scope import lock_private_write_scope

                assert private_scope is not None
                lock_private_write_scope(session, private_scope)
                lock_private_command(session, original_command.command_id)
                attempt = session.scalar(
                    select(CollectionRuntimeAttempt)
                    .where(CollectionRuntimeAttempt.command_id == original_command.command_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if attempt is None:
                    raise CollectionRuntimeConflict("collection runtime attempt is unavailable")
                _assert_bound_attempt(
                    attempt,
                    validated_dispatch,
                    digest,
                    private_scope=private_scope,
                )
                if attempt.state != "RESERVED":
                    raise CollectionRuntimeConflict("collection runtime attempt is not reserved")
                if attempt.attempt_id != prepared.attempt_id:
                    raise CollectionRuntimeConflict("collection runtime attempt identity conflict")
                _validate_effective_collection_command(
                    original_command,
                    effective_command,
                    effective_policy_revision=attempt.effective_policy_revision,
                )

                database_now = session.scalar(select(func.clock_timestamp()))
                if not isinstance(database_now, datetime):
                    raise CollectionRuntimeConflict("collection runtime database clock unavailable")
                if (
                    attempt.claim_token != claim_token
                    or attempt.claim_expires_at is None
                    or attempt.claim_expires_at <= database_now
                ):
                    raise CollectionRuntimeConflict("collection runtime claim token is not active")

                source = _lock_public_source(session, effective_command)
                if source.pointer_update_mode != "FINALIZE_GATE":
                    raise CollectionRuntimeConflict(
                        "collection candidate commit requires FINALIZE_GATE mode"
                    )

                public = persist_canonical_public_commit(
                    session,
                    command=effective_command,
                    prepared=prepared,
                )
                canonical = public.prepared
                attempt.observation_id = (
                    canonical.observation.snapshot.observation_id
                    if canonical.observation is not None
                    else None
                )
                attempt.source_version_id = (
                    canonical.source_version.source_version_id
                    if canonical.source_version is not None
                    else None
                )
                attempt.claim_token = None
                attempt.claim_expires_at = None
                attempt.state = "PERSISTED"
                attempt.updated_at = database_now

                proposal = stage_private_result(
                    session,
                    original_command,
                    canonical.result,
                    message_id=staged_message_id,
                    occurred_at=occurred_at,
                    stage_kind="COLLECTION",
                    private_scope=private_scope,
                )
                session.flush()
                return proposal
    except IntegrityError as error:
        raise PersistenceConflict("atomic collection candidate commit conflicted") from error


def replay_staged_collection(
    session_factory: Callable[[], Session],
    dispatch: W1Dispatch,
    *,
    private_scope: PrivateWriteScope | None = None,
) -> StagedResultProposal:
    """Re-arm and return the exact durable private proposal under the command lock."""

    from epick_engine.source_collection.commit_gate_store import (
        CommitGateRejected,
        PrivateCommitGateReceipt,
        PrivateCommitStage,
        PrivateStagedOutbox,
        _bound_row,
        _hash,
        _stored_ack,
        lock_private_command,
    )
    from epick_engine.source_collection.source_runtime_store import (
        CollectionRuntimeConflict,
        _assert_bound_attempt,
        _validated_dispatch,
        dispatch_digest,
    )

    validated_dispatch = _validated_dispatch(dispatch)
    command = validated_dispatch.payload
    digest = dispatch_digest(validated_dispatch)
    bind_private_write_scope(
        private_scope,
        owner_user_id=command.authenticated_owner_ref,
        owner_deletion_epoch=command.owner_deletion_epoch,
        command_id=command.command_id,
        job_id=command.job_id,
        project_ref=command.project_ref,
        bind_project_ref=True,
    )
    with session_scope(session_factory) as session:
        with session.begin():
            from epick_engine.source_collection.private_scope import lock_private_write_scope

            assert private_scope is not None
            lock_private_write_scope(session, private_scope)
            lock_private_command(session, command.command_id)
            attempt = session.scalar(
                select(CollectionRuntimeAttempt)
                .where(CollectionRuntimeAttempt.command_id == command.command_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if attempt is None:
                raise CollectionRuntimeConflict("collection runtime attempt is unavailable")
            _assert_bound_attempt(
                attempt,
                validated_dispatch,
                digest,
                private_scope=private_scope,
            )
            if attempt.state not in {"PERSISTED", "FINALIZED"}:
                raise CollectionRuntimeConflict("collection runtime attempt is not replayable")
            if attempt.claim_token is not None or attempt.claim_expires_at is not None:
                raise CollectionRuntimeConflict(
                    "collection runtime persisted claim is inconsistent"
                )

            stage = session.scalar(
                select(PrivateCommitStage)
                .where(PrivateCommitStage.command_id == command.command_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if stage is None:
                raise CommitGateRejected("private staged-result command is unavailable")
            if stage.state not in {"STAGED", "PREPARED", "FINALIZED"}:
                raise CollectionRuntimeConflict("collection runtime command is terminal")

            receipt_rows = session.scalars(
                select(PrivateCommitGateReceipt)
                .where(PrivateCommitGateReceipt.command_id == command.command_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
            receipts: list[CommitGateAckProposal] = []
            for receipt in receipt_rows:
                ack = _stored_ack(session, receipt.ack_message_id, for_update=True)
                if (
                    ack.command_id != command.command_id
                    or ack.authenticated_owner_ref != command.authenticated_owner_ref
                    or ack.job_id != command.job_id
                    or str(ack.execution_fence) != command.execution_fence
                    or ack.owner_deletion_epoch != command.owner_deletion_epoch
                    or ack.result_digest != stage.result_digest
                    or ack.operation_id != receipt.operation_id
                    or str(ack.operation_revision) != receipt.operation_revision
                    or ack.outcome != "APPLIED"
                ):
                    raise CommitGateRejected("private commit-gate receipt is inconsistent")
                receipts.append(ack)
            if any(ack.action in {"ABORT", "PURGE"} for ack in receipts):
                raise CollectionRuntimeConflict("collection runtime command is terminal")
            if stage.state == "STAGED":
                if receipts or stage.operation_id is not None or stage.operation_revision != "0":
                    raise CommitGateRejected("private staged-result operation is inconsistent")
            else:
                if not receipts:
                    raise CommitGateRejected("private commit-gate receipt is unavailable")
                latest = max(receipts, key=lambda ack: ack.operation_revision)
                expected_action = "PREPARE" if stage.state == "PREPARED" else "FINALIZE"
                if (
                    latest.action != expected_action
                    or stage.operation_id != latest.operation_id
                    or stage.operation_revision != str(latest.operation_revision)
                ):
                    raise CommitGateRejected("private commit-gate stage is inconsistent")

            delivery = session.scalar(
                select(PrivateStagedOutbox)
                .where(PrivateStagedOutbox.command_id == command.command_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if delivery is None or delivery.payload is None:
                raise CommitGateRejected("private staged-result submission unavailable")
            try:
                proposal = StagedResultProposal.model_validate(delivery.payload)
            except ValueError:
                raise CommitGateRejected("invalid persisted private staged-result") from None
            raw = proposal.model_dump(mode="json")
            if (
                raw != delivery.payload
                or proposal.message_id != delivery.message_id
                or delivery.command_id != command.command_id
                or delivery.wire_hash != _hash(raw)
                or proposal.command != command
            ):
                raise CommitGateRejected("private staged-result submission is inconsistent")
            _bound_row(
                stage,
                owner=command.authenticated_owner_ref,
                job=command.job_id,
                fence=command.execution_fence,
                epoch=command.owner_deletion_epoch,
                digest=proposal.result_digest,
                stage_kind="COLLECTION",
                private_scope=private_scope,
            )
            if stage.result_payload != proposal.result.model_dump(mode="json"):
                raise CommitGateRejected("private staged-result payload is inconsistent")

            delivery.delivered_at = None
            session.flush()
            return proposal


def commit_prepared_collection(
    session_factory: Callable[[], Session],
    *,
    command: CollectionCommand,
    prepared: PreparedCollectionCommit,
    lock_authority: ExecutionAuthorityLocker,
    private_scope: PrivateWriteScope | None = None,
) -> CollectionResult:
    """Commit prepared W2 data after W1 authority is locked in the same transaction."""

    committed_result = prepared.result
    bind_private_write_scope(
        private_scope,
        owner_user_id=command.authenticated_owner_ref,
        owner_deletion_epoch=command.owner_deletion_epoch,
        command_id=command.command_id,
        job_id=command.job_id,
        project_ref=command.project_ref,
        bind_project_ref=True,
    )
    assert private_scope is not None
    try:
        with session_scope(session_factory) as session:
            with session.begin():
                from epick_engine.source_collection.private_scope import lock_private_write_scope

                lock_private_write_scope(session, private_scope)
                grant = lock_authority(
                    session,
                    command=command,
                    attempt_id=prepared.attempt_id,
                )
                _validate_authority(
                    grant=grant,
                    command=command,
                    attempt_id=prepared.attempt_id,
                    private_scope=private_scope,
                )

                replay = _replay_attempt(
                    session,
                    command=command,
                    attempt_id=prepared.attempt_id,
                    private_scope=private_scope,
                )
                if replay is not None:
                    return replay

                source = _lock_public_source(session, command)
                if source.pointer_update_mode != "LEGACY_SAME_DB":
                    raise InvalidPreparedCollection(
                        "legacy collection commit requires LEGACY_SAME_DB mode"
                    )

                replay = _replay_attempt(
                    session,
                    command=command,
                    attempt_id=prepared.attempt_id,
                    private_scope=private_scope,
                )
                if replay is not None:
                    return replay

                public = persist_canonical_public_commit(
                    session,
                    command=command,
                    prepared=prepared,
                )
                source = public.source
                canonical = public.prepared
                committed_result = canonical.result
                observation = (
                    canonical.observation.snapshot if canonical.observation is not None else None
                )
                if observation is not None and grant.pointer_eligible:
                    source.latest_observation_id = observation.observation_id
                    if (
                        observation.source_version_id is not None
                        and canonical.result.successful_source_refs
                    ):
                        source.current_source_version_id = observation.source_version_id
                        if source.first_collected_at is None:
                            source.first_collected_at = observation.observed_at
                        source.last_collected_at = observation.observed_at

                _create_collection_attempt(
                    session,
                    command=command,
                    prepared=canonical,
                    private_scope=private_scope,
                )
                session.flush()
        return committed_result
    except IntegrityError as error:
        raise PersistenceConflict("atomic collection commit conflicted") from error
