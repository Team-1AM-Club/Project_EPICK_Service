from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PROJECTION_SYNC_STATUS_VALUES = "'PENDING', 'STALE', 'ERROR', 'SYNCED'"
EXCLUSION_SCOPE_VALUES = "'GLOBAL', 'COMPANY', 'ROLE', 'PROJECT'"


class ProjectionSyncState(Base):
    """PostgreSQL's generic record of derived-projection freshness.

    This table deliberately identifies an external projection resource only.  It is not a
    polymorphic business-relation table and it is not a W3 ACK ledger.
    """

    __tablename__ = "projection_sync_states"
    __table_args__ = (
        UniqueConstraint(
            "resource_type",
            "resource_id",
            "resource_version",
            "projection_type",
            name="resource_version_projection_type",
        ),
        CheckConstraint(
            f"status IN ({PROJECTION_SYNC_STATUS_VALUES})", name="status_allowed"
        ),
        CheckConstraint(
            "last_applied_revision IS NULL OR last_applied_revision >= 1",
            name="last_applied_revision_positive",
        ),
        CheckConstraint("length(btrim(resource_type)) > 0", name="resource_type_present"),
        CheckConstraint("length(btrim(projection_type)) > 0", name="projection_type_present"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    resource_version: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    projection_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), server_default="PENDING")
    last_event_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    last_applied_revision: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExperienceExclusion(Base):
    """One owner-scoped Activity or Episode exclusion, revocable but never rewritten."""

    __tablename__ = "experience_exclusions"
    __table_args__ = (
        UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
        CheckConstraint(
            "(activity_id IS NOT NULL)::integer + (episode_id IS NOT NULL)::integer = 1",
            name="exactly_one_experience_target",
        ),
        CheckConstraint(f"scope IN ({EXCLUSION_SCOPE_VALUES})", name="scope_allowed"),
        CheckConstraint(
            "(scope = 'GLOBAL' AND company_id IS NULL AND role_id IS NULL AND project_id IS NULL) "
            "OR (scope = 'COMPANY' AND company_id IS NOT NULL "
            "AND role_id IS NULL AND project_id IS NULL) "
            "OR (scope = 'ROLE' AND company_id IS NULL "
            "AND role_id IS NOT NULL AND project_id IS NULL) "
            "OR (scope = 'PROJECT' AND company_id IS NULL "
            "AND role_id IS NULL AND project_id IS NOT NULL)",
            name="scope_context_matches",
        ),
        CheckConstraint(
            "reason IS NULL OR octet_length(reason) <= 1024", name="reason_max_1kib"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    activity_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    episode_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    scope: Mapped[str] = mapped_column(String(16))
    company_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    role_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    project_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SnapshotExclusion(Base):
    """The active exclusion fact fixed into one immutable Snapshot."""

    __tablename__ = "snapshot_exclusions"

    snapshot_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    exclusion_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
