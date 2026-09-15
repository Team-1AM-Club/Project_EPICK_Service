from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
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

ORG_RESOURCE_STATUS_VALUES = "'ACTIVE', 'INACTIVE'"


class CompanyRelation(Base):
    __tablename__ = "company_relations"
    __table_args__ = (
        ForeignKeyConstraint(["from_company_id"], ["companies.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["to_company_id"], ["companies.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["evidence_span_id"], ["evidence_spans.id"], ondelete="RESTRICT"),
        UniqueConstraint(
            "from_company_id",
            "to_company_id",
            "relation_type",
            name="from_company_id_to_company_id_relation_type",
        ),
        CheckConstraint("from_company_id <> to_company_id", name="different_companies"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="valid_range_ordered",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    from_company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    to_company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    relation_type: Mapped[str] = mapped_column(String(32))
    evidence_span_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrgUnit(Base):
    __tablename__ = "org_units"
    __table_args__ = (
        ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        UniqueConstraint("id", "company_id", name="id_company_id"),
        CheckConstraint(f"status IN ({ORG_RESOURCE_STATUS_VALUES})", name="status_allowed"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    # Circular FK to org_unit_versions is added by migration 008 via a deferred
    # ALTER TABLE; deliberately not declared here (see Source.current_version_id).
    current_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OrgUnitVersion(Base):
    __tablename__ = "org_unit_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_unit_id", "company_id"],
            ["org_units.id", "org_units.company_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["parent_org_unit_version_id", "company_id"],
            ["org_unit_versions.id", "org_unit_versions.company_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(["evidence_span_id"], ["evidence_spans.id"], ondelete="RESTRICT"),
        UniqueConstraint("org_unit_id", "version_no", name="org_unit_id_version_no"),
        UniqueConstraint("id", "company_id", name="id_company_id"),
        UniqueConstraint("id", "org_unit_id", "company_id", name="id_org_unit_id_company_id"),
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        CheckConstraint(
            "parent_org_unit_version_id IS NULL OR parent_org_unit_version_id <> id",
            name="parent_is_not_self",
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="valid_range_ordered",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    org_unit_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    version_no: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(Text)
    normalized_name: Mapped[str] = mapped_column(Text)
    parent_org_unit_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    evidence_span_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="RESTRICT"),
        UniqueConstraint("id", "company_id", name="id_company_id"),
        CheckConstraint(f"status IN ({ORG_RESOURCE_STATUS_VALUES})", name="status_allowed"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    # Circular FK to role_versions is added by migration 008 via a deferred
    # ALTER TABLE; deliberately not declared here (see Source.current_version_id).
    current_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RoleVersion(Base):
    __tablename__ = "role_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["role_id", "company_id"], ["roles.id", "roles.company_id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["org_unit_version_id", "company_id"],
            ["org_unit_versions.id", "org_unit_versions.company_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(["evidence_span_id"], ["evidence_spans.id"], ondelete="RESTRICT"),
        UniqueConstraint("role_id", "version_no", name="role_id_version_no"),
        UniqueConstraint("id", "company_id", name="id_company_id"),
        UniqueConstraint("id", "role_id", "company_id", name="id_role_id_company_id"),
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="valid_range_ordered",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    role_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    version_no: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(Text)
    normalized_name: Mapped[str] = mapped_column(Text)
    org_unit_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    employment_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    evidence_span_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
