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

ANALYSIS_STATUS_VALUES = "'SUCCEEDED', 'PARTIAL', 'FAILED', 'BLOCKED'"
MODEL_EXECUTION_STATUS_VALUES = "'SUCCEEDED', 'PARTIAL', 'FAILED', 'BLOCKED'"


class QuestionIntentType(Base):
    """Versioned taxonomy: migration 018 replaced the surrogate ``id`` primary
    key with ``PRIMARY KEY(code, taxonomy_version)`` and added ``is_active``,
    matching EPICK_DB_AGENT_v1.3.md section 7.6."""

    __tablename__ = "question_intent_types"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    taxonomy_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuestionAnalysis(Base):
    __tablename__ = "question_analyses"
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
            ["question_id", "project_id", "owner_user_id"],
            [
                "project_questions.id",
                "project_questions.project_id",
                "project_questions.owner_user_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["question_version_id", "question_id", "project_id", "owner_user_id"],
            [
                "question_versions.id",
                "question_versions.question_id",
                "question_versions.project_id",
                "question_versions.owner_user_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["job_posting_version_id", "company_id"],
            ["job_posting_versions.id", "job_posting_versions.company_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "question_version_id",
            "analysis_input_version",
            "analysis_revision",
            name="question_version_id_analysis_input_version_analysis_revision",
        ),
        UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
        UniqueConstraint(
            "id",
            "question_id",
            "question_version_id",
            "owner_user_id",
            name="id_question_version_owner",
        ),
        UniqueConstraint(
            "id",
            "project_id",
            "project_version_id",
            "owner_user_id",
            name="id_project_version_owner",
        ),
        CheckConstraint("analysis_revision >= 1", name="analysis_revision_positive"),
        CheckConstraint(f"result_status IN ({ANALYSIS_STATUS_VALUES})", name="result_status_allowed"),
        CheckConstraint(
            "safe_failure_message IS NULL OR octet_length(safe_failure_message) <= 1024",
            name="safe_failure_message_bounded",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    project_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    project_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    question_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    question_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    job_posting_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    analysis_input_version: Mapped[str] = mapped_column(String(64))
    analysis_revision: Mapped[int] = mapped_column(Integer)
    result_status: Mapped[str] = mapped_column(String(16))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    safe_failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuestionAnalysisIntent(Base):
    """``intent_code``/``intent_taxonomy_version`` replaced the surrogate
    ``question_intent_type_id`` FK in migration 018, matching the versioned
    taxonomy key on :class:`QuestionIntentType`."""

    __tablename__ = "question_analysis_intents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["question_analysis_id", "owner_user_id"],
            ["question_analyses.id", "question_analyses.owner_user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["intent_code", "intent_taxonomy_version"],
            ["question_intent_types.code", "question_intent_types.taxonomy_version"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("rank >= 1", name="rank_positive"),
    )

    question_analysis_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    intent_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    intent_taxonomy_version: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    rank: Mapped[int] = mapped_column(Integer)
    is_primary: Mapped[bool] = mapped_column(Boolean, server_default="false")


class QuestionAnalysisRequirementGroup(Base):
    __tablename__ = "question_analysis_requirement_groups"
    __table_args__ = (
        ForeignKeyConstraint(
            ["question_analysis_id", "owner_user_id"],
            ["question_analyses.id", "question_analyses.owner_user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["requirement_group_id"], ["requirement_groups.id"], ondelete="RESTRICT"
        ),
    )

    question_analysis_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    requirement_group_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )


class QuestionAnalysisRequirement(Base):
    __tablename__ = "question_analysis_requirements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["question_analysis_id", "owner_user_id"],
            ["question_analyses.id", "question_analyses.owner_user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(["requirement_id"], ["requirements.id"], ondelete="RESTRICT"),
    )

    question_analysis_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    requirement_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)


class SnapshotActivityVersion(Base):
    __tablename__ = "snapshot_activity_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "owner_user_id"],
            ["project_snapshots.id", "project_snapshots.owner_user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["activity_version_id", "owner_user_id"],
            ["activity_versions.id", "activity_versions.owner_user_id"],
            ondelete="RESTRICT",
        ),
    )

    snapshot_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    activity_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )


class SnapshotSourceVersion(Base):
    __tablename__ = "snapshot_source_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "owner_user_id"],
            ["project_snapshots.id", "project_snapshots.owner_user_id"],
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
    )

    snapshot_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    source_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    source_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )
    company_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))


class SnapshotQuestionAnalysis(Base):
    __tablename__ = "snapshot_question_analyses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "project_id", "project_version_id", "owner_user_id"],
            [
                "project_snapshots.id",
                "project_snapshots.project_id",
                "project_snapshots.project_version_id",
                "project_snapshots.owner_user_id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["question_analysis_id", "project_id", "project_version_id", "owner_user_id"],
            [
                "question_analyses.id",
                "question_analyses.project_id",
                "question_analyses.project_version_id",
                "question_analyses.owner_user_id",
            ],
            ondelete="RESTRICT",
        ),
    )

    snapshot_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    project_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    project_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    question_analysis_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True
    )


class ModelExecution(Base):
    __tablename__ = "model_executions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["question_analysis_id", "owner_user_id"],
            ["question_analyses.id", "question_analyses.owner_user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["recommendation_run_id", "owner_user_id"],
            ["recommendation_runs.id", "recommendation_runs.owner_user_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(question_analysis_id IS NOT NULL)::integer + "
            "(recommendation_run_id IS NOT NULL)::integer = 1",
            name="exactly_one_parent",
        ),
        CheckConstraint(
            "(execution_kind = 'QUESTION_ANALYSIS' AND question_analysis_id IS NOT NULL) OR "
            "(execution_kind = 'RECOMMENDATION' AND recommendation_run_id IS NOT NULL)",
            name="execution_kind_parent_matches",
        ),
        CheckConstraint(
            f"result_status IN ({MODEL_EXECUTION_STATUS_VALUES})", name="result_status_allowed"
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at", name="completed_after_started"
        ),
        CheckConstraint(
            "safe_failure_message IS NULL OR octet_length(safe_failure_message) <= 1024",
            name="safe_failure_message_bounded",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    question_analysis_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    recommendation_run_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    execution_kind: Mapped[str] = mapped_column(String(32))
    model_provider: Mapped[str] = mapped_column(String(64))
    model_identifier: Mapped[str] = mapped_column(String(128))
    input_fingerprint: Mapped[str] = mapped_column(String(128))
    result_status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    safe_failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
