from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

CLAIM_STATUS_VALUES = "'ACTIVE', 'RETRACTED', 'SUPERSEDED'"
CLAIM_VERIFICATION_STATUS_VALUES = "'UNVERIFIED', 'SUPPORTED', 'CONFLICTING', 'RETRACTED'"
EVIDENCE_STANCE_VALUES = "'SUPPORTS', 'CONTRADICTS', 'CONTEXT'"
CLAIM_RELATION_VALUES = "'REFINES', 'CONTRADICTS', 'SUPERSEDES', 'RELATED'"
INTERPRETATION_STATUS_VALUES = "'ACTIVE', 'RETRACTED', 'SUPERSEDED'"
INTERPRETATION_CONFIDENCE_VALUES = "'UNVERIFIED', 'SUPPORTED', 'CONFLICTING'"
USER_DECISION_VALUES = "'ACCEPTED', 'DISMISSED', 'QUESTIONED'"
PROJECT_DECISION_VALUES = "'ACCEPTED', 'DISMISSED', 'REVISED'"


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (
        ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        UniqueConstraint("id", "company_id", name="id_company_id"),
        CheckConstraint(f"status IN ({CLAIM_STATUS_VALUES})", name="status_allowed"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    # Circular FK to claim_versions is added by migration 009 via a deferred
    # ALTER TABLE; deliberately not declared here (see Source.current_version_id).
    current_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClaimVersion(Base):
    """Subject/predicate/object triple. Field set completed by migration 018:
    predicate, scope FKs (org_unit/role/job_posting_version), numeric_value/unit,
    period_from/period_to, validity_precision, comparison_basis, extractor_version
    and extracted_at were added on top of the original 009 shape.
    """

    __tablename__ = "claim_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["claim_id", "company_id"], ["claims.id", "claims.company_id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["org_unit_version_id", "company_id"],
            ["org_unit_versions.id", "org_unit_versions.company_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["role_version_id", "company_id"],
            ["role_versions.id", "role_versions.company_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["job_posting_version_id", "company_id"],
            ["job_posting_versions.id", "job_posting_versions.company_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("claim_id", "version_no", name="claim_id_version_no"),
        UniqueConstraint("id", "claim_id", "company_id", name="id_claim_id_company_id"),
        UniqueConstraint("id", "company_id", name="id_company_id"),
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        CheckConstraint(
            f"verification_status IN ({CLAIM_VERIFICATION_STATUS_VALUES})",
            name="verification_status_allowed",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="valid_range_ordered",
        ),
        CheckConstraint(
            "period_to IS NULL OR period_from IS NULL OR period_to >= period_from",
            name="period_ordered",
        ),
        CheckConstraint(
            "(org_unit_version_id IS NOT NULL)::integer + "
            "(role_version_id IS NOT NULL)::integer + "
            "(job_posting_version_id IS NOT NULL)::integer <= 1",
            name="at_most_one_scope",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    claim_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    version_no: Mapped[int] = mapped_column(Integer)
    claim_type: Mapped[str] = mapped_column(String(64))
    subject_text: Mapped[str] = mapped_column(Text)
    predicate: Mapped[str] = mapped_column(Text)
    object_text: Mapped[str] = mapped_column(Text)
    org_unit_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    role_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    job_posting_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    numeric_value: Mapped[Decimal | None] = mapped_column(Numeric, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    period_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    validity_precision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    comparison_basis: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_status: Mapped[str] = mapped_column(String(16), server_default="UNVERIFIED")
    extractor_version: Mapped[str] = mapped_column(String(64))
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClaimEvidenceLink(Base):
    __tablename__ = "claim_evidence_links"
    __table_args__ = (
        ForeignKeyConstraint(["claim_version_id"], ["claim_versions.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["evidence_span_id"], ["evidence_spans.id"], ondelete="RESTRICT"),
        CheckConstraint(
            f"relation_type IN ({EVIDENCE_STANCE_VALUES})", name="relation_type_allowed"
        ),
    )

    claim_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    evidence_span_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    relation_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClaimRelation(Base):
    __tablename__ = "claim_relations"
    __table_args__ = (
        ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["from_claim_version_id", "company_id"],
            ["claim_versions.id", "claim_versions.company_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["to_claim_version_id", "company_id"],
            ["claim_versions.id", "claim_versions.company_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "from_claim_version_id",
            "to_claim_version_id",
            "relation_type",
            name="from_claim_version_id_to_claim_version_id_relation_type",
        ),
        CheckConstraint(
            "from_claim_version_id <> to_claim_version_id", name="different_claim_versions"
        ),
        CheckConstraint(
            f"relation_type IN ({CLAIM_RELATION_VALUES})", name="relation_type_allowed"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    from_claim_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    to_claim_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    relation_type: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Interpretation(Base):
    __tablename__ = "interpretations"
    __table_args__ = (
        ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        UniqueConstraint("id", "company_id", name="id_company_id"),
        CheckConstraint(f"status IN ({INTERPRETATION_STATUS_VALUES})", name="status_allowed"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    # Circular FK to interpretation_versions is added by migration 009 via a
    # deferred ALTER TABLE; deliberately not declared here.
    current_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InterpretationVersion(Base):
    __tablename__ = "interpretation_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["interpretation_id", "company_id"],
            ["interpretations.id", "interpretations.company_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("interpretation_id", "version_no", name="interpretation_id_version_no"),
        UniqueConstraint(
            "id", "interpretation_id", "company_id", name="id_interpretation_id_company_id"
        ),
        UniqueConstraint("id", "company_id", name="id_company_id"),
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        CheckConstraint(
            f"confidence IN ({INTERPRETATION_CONFIDENCE_VALUES})", name="confidence_allowed"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    interpretation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    version_no: Mapped[int] = mapped_column(Integer)
    interpretation_type: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(16), server_default="UNVERIFIED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InterpretationEvidenceLink(Base):
    """Surrogate `id` PK, nullable `evidence_span_id`, and `claim_version_id`
    were added by migration 018 so an interpretation can cite either an
    EvidenceSpan or a ClaimVersion (exactly one). The original ``relation_type``
    column was renamed to ``stance`` to match the product vocabulary."""

    __tablename__ = "interpretation_evidence_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["interpretation_version_id"], ["interpretation_versions.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(["evidence_span_id"], ["evidence_spans.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["claim_version_id"], ["claim_versions.id"], ondelete="RESTRICT"),
        UniqueConstraint(
            "interpretation_version_id",
            "evidence_span_id",
            name="interpretation_version_id_evidence_span_id",
        ),
        CheckConstraint(f"stance IN ({EVIDENCE_STANCE_VALUES})", name="stance_allowed"),
        CheckConstraint(
            "(evidence_span_id IS NOT NULL)::integer + (claim_version_id IS NOT NULL)::integer = 1",
            name="exactly_one_reference",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    interpretation_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    evidence_span_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    claim_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    stance: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserInterpretationDecision(Base):
    __tablename__ = "user_interpretation_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "owner_user_id"],
            ["application_projects.id", "application_projects.owner_user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_version_id", "project_id", "owner_user_id", "company_id"],
            [
                "application_project_versions.id",
                "application_project_versions.project_id",
                "application_project_versions.owner_user_id",
                "application_project_versions.company_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["interpretation_version_id", "company_id"],
            ["interpretation_versions.id", "interpretation_versions.company_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "owner_user_id",
            "project_version_id",
            "interpretation_version_id",
            name="owner_user_id_project_version_id_interpretation_version_id",
        ),
        CheckConstraint(f"decision_code IN ({USER_DECISION_VALUES})", name="decision_code_allowed"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    project_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    project_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    interpretation_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    decision_code: Mapped[str] = mapped_column(String(16))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectInterpretation(Base):
    __tablename__ = "project_interpretations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "owner_user_id"],
            ["application_projects.id", "application_projects.owner_user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        UniqueConstraint(
            "id",
            "owner_user_id",
            "project_id",
            "company_id",
            name="id_owner_user_id_project_id_company_id",
        ),
        CheckConstraint(f"status IN ({INTERPRETATION_STATUS_VALUES})", name="status_allowed"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    project_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    # Circular FK to project_interpretation_versions is added by migration 009
    # via a deferred ALTER TABLE; deliberately not declared here.
    current_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectInterpretationVersion(Base):
    __tablename__ = "project_interpretation_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_interpretation_id", "owner_user_id", "project_id", "company_id"],
            [
                "project_interpretations.id",
                "project_interpretations.owner_user_id",
                "project_interpretations.project_id",
                "project_interpretations.company_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_version_id", "project_id", "owner_user_id", "company_id"],
            [
                "application_project_versions.id",
                "application_project_versions.project_id",
                "application_project_versions.owner_user_id",
                "application_project_versions.company_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["public_interpretation_version_id", "company_id"],
            ["interpretation_versions.id", "interpretation_versions.company_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "project_interpretation_id",
            "version_no",
            name="project_interpretation_id_version_no",
        ),
        UniqueConstraint(
            "id",
            "project_interpretation_id",
            "owner_user_id",
            "project_id",
            "company_id",
            name="id_interpretation_scope",
        ),
        UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        CheckConstraint(
            f"confidence IN ({INTERPRETATION_CONFIDENCE_VALUES})", name="confidence_allowed"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_interpretation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    project_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    project_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    public_interpretation_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    version_no: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(16), server_default="UNVERIFIED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectInterpretationEvidenceLink(Base):
    __tablename__ = "project_interpretation_evidence_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_interpretation_version_id", "owner_user_id"],
            [
                "project_interpretation_versions.id",
                "project_interpretation_versions.owner_user_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(["evidence_span_id"], ["evidence_spans.id"], ondelete="RESTRICT"),
        CheckConstraint(
            f"relation_type IN ({EVIDENCE_STANCE_VALUES})", name="relation_type_allowed"
        ),
    )

    project_interpretation_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    evidence_span_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    relation_type: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectInterpretationDecision(Base):
    __tablename__ = "project_interpretation_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_interpretation_version_id", "owner_user_id"],
            [
                "project_interpretation_versions.id",
                "project_interpretation_versions.owner_user_id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"decision_code IN ({PROJECT_DECISION_VALUES})", name="decision_code_allowed"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_interpretation_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    decision_code: Mapped[str] = mapped_column(String(16))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
