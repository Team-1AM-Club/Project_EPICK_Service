from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

JOB_POSTING_STATUS_VALUES = "'OPEN', 'CLOSED', 'UNKNOWN', 'ARCHIVED'"
JOB_POSTING_ANALYSIS_STATUS_VALUES = "'PENDING', 'SUCCEEDED', 'LIMITED', 'FAILED'"
REQUIREMENT_GROUP_OPERATOR_VALUES = "'AND', 'OR'"
REQUIREMENT_NECESSITY_VALUES = "'REQUIRED', 'PREFERRED', 'GENERAL'"


class CanonicalSkill(Base):
    __tablename__ = "canonical_skills"
    __table_args__ = (UniqueConstraint("canonical_name", name="canonical_name"),)

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    canonical_name: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SkillAlias(Base):
    __tablename__ = "skill_aliases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["canonical_skill_id"], ["canonical_skills.id"], ondelete="RESTRICT"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    canonical_skill_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    alias: Mapped[str] = mapped_column(Text)
    normalized_alias: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobPosting(Base):
    __tablename__ = "job_postings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_id", "company_id"],
            ["sources.id", "sources.company_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "company_id", name="id_company_id"),
        UniqueConstraint("id", "source_id", "company_id", name="id_source_id_company_id"),
        CheckConstraint(f"status IN ({JOB_POSTING_STATUS_VALUES})", name="status_allowed"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    source_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    # Circular FK to job_posting_versions is added by migration 007 via a deferred
    # ALTER TABLE; deliberately not declared here (see Source.current_version_id).
    current_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), server_default="UNKNOWN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobPostingVersion(Base):
    __tablename__ = "job_posting_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["posting_id", "source_id", "company_id"],
            ["job_postings.id", "job_postings.source_id", "job_postings.company_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_version_id", "source_id", "company_id"],
            [
                "source_versions.id",
                "source_versions.source_id",
                "source_versions.company_id",
            ],
            ondelete="RESTRICT",
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
        UniqueConstraint("posting_id", "version_no", name="posting_id_version_no"),
        UniqueConstraint("id", "company_id", name="id_company_id"),
        UniqueConstraint("id", "posting_id", "company_id", name="id_posting_id_company_id"),
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        CheckConstraint(
            "closes_at IS NULL OR published_at IS NULL OR closes_at >= published_at",
            name="closing_after_publication",
        ),
        CheckConstraint(
            f"analysis_status IN ({JOB_POSTING_ANALYSIS_STATUS_VALUES})",
            name="analysis_status_allowed",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    posting_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    source_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    version_no: Mapped[int] = mapped_column(Integer)
    source_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    title: Mapped[str] = mapped_column(Text)
    organization_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closes_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    analysis_status: Mapped[str] = mapped_column(String(32), server_default="PENDING")
    org_unit_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    role_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RequirementGroup(Base):
    __tablename__ = "requirement_groups"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_posting_version_id"], ["job_posting_versions.id"], ondelete="RESTRICT"
        ),
        UniqueConstraint(
            "job_posting_version_id", "group_order", name="job_posting_version_id_group_order"
        ),
        CheckConstraint("group_order >= 0", name="group_order_not_negative"),
        CheckConstraint(
            f"operator IN ({REQUIREMENT_GROUP_OPERATOR_VALUES})", name="operator_allowed"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_posting_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    group_order: Mapped[int] = mapped_column(Integer)
    operator: Mapped[str] = mapped_column(String(8))
    same_experience_required: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Requirement(Base):
    __tablename__ = "requirements"
    __table_args__ = (
        ForeignKeyConstraint(["group_id"], ["requirement_groups.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["evidence_span_id"], ["evidence_spans.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["org_unit_version_id"], ["org_unit_versions.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(["role_version_id"], ["role_versions.id"], ondelete="RESTRICT"),
        CheckConstraint(f"necessity IN ({REQUIREMENT_NECESSITY_VALUES})", name="necessity_allowed"),
        CheckConstraint(
            "(org_unit_version_id IS NOT NULL)::integer + "
            "(role_version_id IS NOT NULL)::integer <= 1",
            name="at_most_one_org_scope",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    group_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    evidence_span_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    category: Mapped[str] = mapped_column(String(32))
    necessity: Mapped[str] = mapped_column(String(32))
    source_text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    comparison_operator: Mapped[str | None] = mapped_column(String(32), nullable=True)
    comparison_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    org_unit_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    role_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RequirementSkill(Base):
    __tablename__ = "requirement_skills"
    __table_args__ = (
        ForeignKeyConstraint(["requirement_id"], ["requirements.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["canonical_skill_id"], ["canonical_skills.id"], ondelete="RESTRICT"
        ),
    )

    requirement_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    canonical_skill_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    raw_term: Mapped[str] = mapped_column(Text, primary_key=True)
    relation_type: Mapped[str] = mapped_column(String(32))
