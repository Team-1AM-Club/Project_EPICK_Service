from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
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

SNAPSHOT_STATUS_VALUES = "'CREATING', 'READY', 'STALE', 'FAILED'"
RECOMMENDATION_RUN_STATUS_VALUES = (
    "'PENDING', 'RUNNING', 'SUCCEEDED', 'LIMITED', 'FAILED', 'CANCELLED'"
)
RECOMMENDATION_RESULT_STATUS_VALUES = "'PENDING', 'READY', 'LIMITED', 'FAILED'"
CANDIDATE_MATCH_STATUS_VALUES = (
    "'DIRECT_MATCH', 'PARTIAL_RELEVANCE', 'NEEDS_VERIFICATION', 'NO_RELEVANT_EVIDENCE'"
)
CANDIDATE_VALIDATION_STATUS_VALUES = "'PENDING', 'PASSED', 'LIMITED', 'FAILED'"


class ProjectSnapshot(Base):
    __tablename__ = "project_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "owner_user_id"],
            ["application_projects.id", "application_projects.owner_user_id"],
            name="project_owner_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_version_id", "project_id", "owner_user_id"],
            [
                "application_project_versions.id",
                "application_project_versions.project_id",
                "application_project_versions.owner_user_id",
            ],
            name="project_version_project_owner_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("project_id", "snapshot_no", name="project_id_snapshot_no"),
        UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
        UniqueConstraint("id", "project_id", "owner_user_id", name="id_project_id_owner_user_id"),
        UniqueConstraint(
            "id",
            "project_id",
            "project_version_id",
            "owner_user_id",
            name="id_project_id_project_version_id_owner_user_id",
        ),
        CheckConstraint("snapshot_no >= 1", name="snapshot_no_positive"),
        CheckConstraint(f"status IN ({SNAPSHOT_STATUS_VALUES})", name="status_allowed"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    project_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    project_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    snapshot_no: Mapped[int] = mapped_column(Integer)
    recommendation_policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Added by migration 010. The relationship is deliberately not declared here
    # because job_posting_versions is SQL-owned in the job-postings persistence
    # slice (see Source.current_version_id for the same convention).
    job_posting_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), server_default="CREATING")
    limitations: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SnapshotEpisodeVersion(Base):
    __tablename__ = "snapshot_episode_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "owner_user_id"],
            ["project_snapshots.id", "project_snapshots.owner_user_id"],
            name="snapshot_owner_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["episode_version_id", "owner_user_id"],
            ["episode_versions.id", "episode_versions.owner_user_id"],
            name="episode_version_owner_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "snapshot_id",
            "episode_version_id",
            "owner_user_id",
            name="snapshot_id_episode_version_id_owner_user_id",
        ),
    )

    snapshot_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    episode_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))


class RecommendationRun(Base):
    __tablename__ = "recommendation_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "owner_user_id"],
            ["application_projects.id", "application_projects.owner_user_id"],
            name="project_owner_scope",
            ondelete="RESTRICT",
        ),
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
        ForeignKeyConstraint(
            ["question_version_id", "question_id", "owner_user_id"],
            [
                "question_versions.id",
                "question_versions.question_id",
                "question_versions.owner_user_id",
            ],
            name="question_version_question_owner_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "project_id", "owner_user_id"],
            [
                "project_snapshots.id",
                "project_snapshots.project_id",
                "project_snapshots.owner_user_id",
            ],
            name="snapshot_project_owner_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["jobs.id", "jobs.owner_user_id"],
            name="job_owner_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id",
            "question_id",
            "snapshot_id",
            "owner_user_id",
            name="id_question_id_snapshot_id_owner_user_id",
        ),
        UniqueConstraint("id", "question_id", "owner_user_id", name="id_question_id_owner_user_id"),
        UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
        CheckConstraint(
            f"result_status IN ({RECOMMENDATION_RESULT_STATUS_VALUES})",
            name="result_status_allowed",
        ),
        CheckConstraint(f"status IN ({RECOMMENDATION_RUN_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint("requested_candidate_limit > 0", name="requested_candidate_limit_positive"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    project_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    question_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    question_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    snapshot_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    job_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    # Added by migration 010. The relationship is deliberately not declared here
    # because question_analyses is SQL-owned in the question-analysis
    # persistence slice (see Source.current_version_id for the same convention).
    question_analysis_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    analysis_policy_version: Mapped[str] = mapped_column(String(64))
    analysis_input_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_status: Mapped[str] = mapped_column(String(32), server_default="PENDING")
    restriction_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), server_default="PENDING")
    requested_candidate_limit: Mapped[int] = mapped_column(Integer)
    limited_analysis: Mapped[bool] = mapped_column(Boolean, server_default="false")
    limitations: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RecommendationCandidate(Base):
    __tablename__ = "recommendation_candidates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "question_id", "snapshot_id", "owner_user_id"],
            [
                "recommendation_runs.id",
                "recommendation_runs.question_id",
                "recommendation_runs.snapshot_id",
                "recommendation_runs.owner_user_id",
            ],
            name="run_question_snapshot_owner_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "episode_version_id", "owner_user_id"],
            [
                "snapshot_episode_versions.snapshot_id",
                "snapshot_episode_versions.episode_version_id",
                "snapshot_episode_versions.owner_user_id",
            ],
            name="snapshot_episode_version_owner_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("run_id", "candidate_no", name="run_id_candidate_no"),
        UniqueConstraint("id", "question_id", "owner_user_id", name="id_question_id_owner_user_id"),
        UniqueConstraint(
            "id",
            "run_id",
            "question_id",
            "owner_user_id",
            name="id_run_id_question_id_owner_user_id",
        ),
        CheckConstraint("candidate_no >= 1", name="candidate_no_positive"),
        CheckConstraint(
            f"match_status IN ({CANDIDATE_MATCH_STATUS_VALUES})", name="match_status_allowed"
        ),
        CheckConstraint(
            f"validation_status IN ({CANDIDATE_VALIDATION_STATUS_VALUES})",
            name="validation_status_allowed",
        ),
        CheckConstraint(
            "internal_rank IS NULL OR internal_rank >= 1", name="internal_rank_positive"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    question_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    snapshot_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    candidate_no: Mapped[int] = mapped_column(Integer)
    episode_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    match_status: Mapped[str] = mapped_column(String(32))
    short_reason: Mapped[str] = mapped_column(Text)
    strength_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    limitation_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    internal_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_status: Mapped[str] = mapped_column(String(32), server_default="PENDING")
    result_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MaterialSelectionSet(Base):
    __tablename__ = "material_selection_sets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "question_id", "owner_user_id"],
            [
                "recommendation_runs.id",
                "recommendation_runs.question_id",
                "recommendation_runs.owner_user_id",
            ],
            name="run_question_owner_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "id",
            "question_id",
            "run_id",
            "owner_user_id",
            name="id_question_id_run_id_owner_user_id",
        ),
        Index(
            "current_question",
            "question_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    question_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    is_current: Mapped[bool] = mapped_column(Boolean, server_default="false")
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    selected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MaterialSelectionItem(Base):
    __tablename__ = "material_selection_items"
    __table_args__ = (
        ForeignKeyConstraint(
            ["selection_set_id", "question_id", "run_id", "owner_user_id"],
            [
                "material_selection_sets.id",
                "material_selection_sets.question_id",
                "material_selection_sets.run_id",
                "material_selection_sets.owner_user_id",
            ],
            name="selection_set_question_run_owner_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["candidate_id", "run_id", "question_id", "owner_user_id"],
            [
                "recommendation_candidates.id",
                "recommendation_candidates.run_id",
                "recommendation_candidates.question_id",
                "recommendation_candidates.owner_user_id",
            ],
            name="candidate_run_question_owner_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "selection_set_id", "selection_order", name="selection_set_id_selection_order"
        ),
        CheckConstraint("selection_order >= 1", name="selection_order_positive"),
    )

    selection_set_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    candidate_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    question_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    selection_order: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
