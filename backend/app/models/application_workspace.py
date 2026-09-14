from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
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

COMPANY_IDENTIFICATION_VALUES = "'PENDING', 'VERIFIED', 'AMBIGUOUS', 'REJECTED'"
PROJECT_STATUS_VALUES = (
    "'DRAFT', 'COLLECTING', 'READY', 'RECOMMENDING', 'MATERIALS_SELECTED', 'STALE', 'ARCHIVED'"
)
QUESTION_STATUS_VALUES = "'ACTIVE', 'ARCHIVED'"


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        CheckConstraint(
            f"identification_status IN ({COMPANY_IDENTIFICATION_VALUES})",
            name="identification_status_allowed",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    legal_name: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    official_domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    identification_status: Mapped[str] = mapped_column(String(32), server_default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApplicationProject(Base):
    __tablename__ = "application_projects"
    __table_args__ = (
        UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
        ForeignKeyConstraint(
            ["active_snapshot_id", "id", "owner_user_id"],
            [
                "project_snapshots.id",
                "project_snapshots.project_id",
                "project_snapshots.owner_user_id",
            ],
            name="active_snapshot_scope",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint(f"status IN ({PROJECT_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint("lock_version >= 1", name="lock_version_positive"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    current_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    active_snapshot_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), server_default="DRAFT")
    current_step: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lock_version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApplicationProjectVersion(Base):
    __tablename__ = "application_project_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "owner_user_id"],
            ["application_projects.id", "application_projects.owner_user_id"],
            name="project_owner_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("project_id", "version_no", name="project_id_version_no"),
        UniqueConstraint("id", "project_id", "owner_user_id", name="id_project_id_owner_user_id"),
        UniqueConstraint(
            "id", "project_id", "owner_user_id", "company_id", name="id_project_owner_company_id"
        ),
        CheckConstraint("version_no >= 1", name="version_no_positive"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    version_no: Mapped[int] = mapped_column(Integer)
    company_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT")
    )
    title: Mapped[str] = mapped_column(Text)
    season: Mapped[str | None] = mapped_column(String(64), nullable=True)
    organization_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_name: Mapped[str] = mapped_column(Text)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectQuestion(Base):
    __tablename__ = "project_questions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "owner_user_id"],
            ["application_projects.id", "application_projects.owner_user_id"],
            name="project_owner_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "project_id", "owner_user_id", name="id_project_id_owner_user_id"),
        UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
        UniqueConstraint("project_id", "display_order", name="project_id_display_order"),
        CheckConstraint(f"status IN ({QUESTION_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint("display_order >= 0", name="display_order_not_negative"),
        CheckConstraint("lock_version >= 1", name="lock_version_positive"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    project_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    current_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    display_order: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), server_default="ACTIVE")
    lock_version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuestionVersion(Base):
    __tablename__ = "question_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["question_id", "project_id", "owner_user_id"],
            [
                "project_questions.id",
                "project_questions.project_id",
                "project_questions.owner_user_id",
            ],
            name="question_project_owner_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("question_id", "version_no", name="question_id_version_no"),
        UniqueConstraint("id", "question_id", "owner_user_id", name="id_question_id_owner_user_id"),
        UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        CheckConstraint(
            "character_limit IS NULL OR character_limit > 0", name="character_limit_positive"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    question_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    project_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    version_no: Mapped[int] = mapped_column(Integer)
    prompt: Mapped[str] = mapped_column(Text)
    character_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
