from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

INFERENCE_SUGGESTION_STATUS_VALUES = "'PENDING_DECISION', 'DECIDED', 'SUPERSEDED'"
INFERENCE_DECISION_VALUES = "'APPROVED', 'MODIFIED', 'REJECTED'"
DUPLICATE_SUGGESTION_STATUS_VALUES = "'PENDING_DECISION', 'DECIDED', 'SUPERSEDED'"
DUPLICATE_DECISION_VALUES = "'MERGE', 'KEEP_SEPARATE', 'DISMISSED'"
NOTIFICATION_SEVERITY_VALUES = "'INFO', 'WARNING', 'ERROR'"


class InferenceSuggestion(Base):
    __tablename__ = "inference_suggestions"
    __table_args__ = (
        UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
        CheckConstraint(f"status IN ({INFERENCE_SUGGESTION_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint("length(btrim(suggestion_type)) > 0", name="suggestion_type_present"),
        CheckConstraint("jsonb_typeof(proposed_value) = 'object'", name="proposed_value_object"),
        CheckConstraint(
            "octet_length(proposed_value::text) <= 16384", name="proposed_value_max_16kib"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    episode_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    episode_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    suggestion_type: Mapped[str] = mapped_column(String(64))
    proposed_value: Mapped[dict[str, object]] = mapped_column(JSONB)
    model_policy_version: Mapped[str] = mapped_column(String(64))
    model_execution_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), server_default="PENDING_DECISION")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InferenceSuggestionSource(Base):
    __tablename__ = "inference_suggestion_sources"
    __table_args__ = (
        CheckConstraint(
            "(episode_version_id IS NOT NULL)::integer + (source_version_id IS NOT NULL)::integer "
            "+ (evidence_span_id IS NOT NULL)::integer = 1",
            name="exactly_one_evidence",
        ),
        CheckConstraint(
            "source_span_start IS NULL OR source_span_start >= 0",
            name="source_span_start_not_negative",
        ),
        CheckConstraint(
            "source_span_end IS NULL OR source_span_end >= 0", name="source_span_end_not_negative"
        ),
        CheckConstraint(
            "source_span_start IS NULL OR source_span_end IS NULL "
            "OR source_span_end >= source_span_start",
            name="source_span_ordered",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    suggestion_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    episode_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    source_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    evidence_span_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    field_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_span_start: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_span_end: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InferenceDecision(Base):
    __tablename__ = "inference_decisions"
    __table_args__ = (
        UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
        UniqueConstraint("suggestion_id", "decision_no", name="suggestion_id_decision_no"),
        CheckConstraint("decision_no >= 1", name="decision_no_positive"),
        CheckConstraint(f"decision IN ({INFERENCE_DECISION_VALUES})", name="decision_allowed"),
        CheckConstraint(
            "modified_value IS NULL OR jsonb_typeof(modified_value) = 'object'",
            name="modified_value_object",
        ),
        CheckConstraint(
            "modified_value IS NULL OR octet_length(modified_value::text) <= 16384",
            name="modified_value_max_16kib",
        ),
        CheckConstraint(
            "(decision = 'MODIFIED' AND modified_value IS NOT NULL) OR "
            "(decision <> 'MODIFIED' AND modified_value IS NULL)",
            name="modified_value_matches_decision",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    suggestion_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    decision_no: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(16))
    modified_value: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExperienceDuplicateSuggestion(Base):
    __tablename__ = "experience_duplicate_suggestions"
    __table_args__ = (
        UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
        UniqueConstraint(
            "owner_user_id",
            "left_episode_id",
            "left_episode_version_id",
            "right_episode_id",
            "right_episode_version_id",
            name="owner_episode_version_pair",
        ),
        CheckConstraint("left_episode_id < right_episode_id", name="episode_pair_canonical_order"),
        CheckConstraint(
            "left_episode_version_id <> right_episode_version_id", name="version_pair_distinct"
        ),
        CheckConstraint("left_episode_id <> right_episode_id", name="episode_pair_distinct"),
        CheckConstraint(f"status IN ({DUPLICATE_SUGGESTION_STATUS_VALUES})", name="status_allowed"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    left_episode_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    left_episode_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    right_episode_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    right_episode_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    reason: Mapped[str] = mapped_column(Text)
    model_execution_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), server_default="PENDING_DECISION")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExperienceDuplicateDecision(Base):
    __tablename__ = "experience_duplicate_decisions"
    __table_args__ = (
        UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
        UniqueConstraint("suggestion_id", "decision_no", name="suggestion_id_decision_no"),
        CheckConstraint("decision_no >= 1", name="decision_no_positive"),
        CheckConstraint(f"decision IN ({DUPLICATE_DECISION_VALUES})", name="decision_allowed"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    suggestion_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    decision_no: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExperienceMergeRecord(Base):
    __tablename__ = "experience_merge_records"
    __table_args__ = (UniqueConstraint("decision_id", name="decision_id_once"),)

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    decision_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    source_episode_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    source_episode_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    target_episode_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    target_episode_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    result_episode_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobCheckpoint(Base):
    __tablename__ = "job_checkpoints"
    __table_args__ = (
        UniqueConstraint("job_id", "checkpoint_revision", name="job_id_checkpoint_revision"),
        CheckConstraint("checkpoint_revision >= 1", name="checkpoint_revision_positive"),
        CheckConstraint("execution_fence >= 1", name="execution_fence_positive"),
        CheckConstraint("owner_deletion_epoch >= 0", name="owner_deletion_epoch_not_negative"),
        CheckConstraint(
            "state_ref IS NULL OR length(btrim(state_ref)) > 0", name="state_ref_present"
        ),
        CheckConstraint("jsonb_typeof(resume_payload) = 'object'", name="resume_payload_object"),
        CheckConstraint(
            "octet_length(resume_payload::text) <= 16384", name="resume_payload_max_16kib"
        ),
        CheckConstraint(
            "resume_payload - ARRAY['cursor', 'next_page', 'snapshot_id', 'source_version_id'] "
            "= '{}'::jsonb",
            name="resume_payload_allowlist",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    checkpoint_revision: Mapped[int] = mapped_column(Integer)
    checkpoint_schema_version: Mapped[str] = mapped_column(String(32), server_default="1.0")
    analysis_input_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_fence: Mapped[int] = mapped_column(BigInteger)
    owner_deletion_epoch: Mapped[int] = mapped_column(BigInteger)
    resume_stage: Mapped[str] = mapped_column(String(64))
    # The local checkpoint ``id`` is the W1 UUID reference.  ``state_ref`` keeps a
    # bounded opaque worker reference such as W2's ``fetch:checkpoint-0001``.
    state_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    resume_payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    resumable: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint("length(btrim(notification_type)) > 0", name="notification_type_present"),
        CheckConstraint(f"severity IN ({NOTIFICATION_SEVERITY_VALUES})", name="severity_allowed"),
        CheckConstraint("octet_length(title) <= 512", name="title_max_512bytes"),
        CheckConstraint("octet_length(safe_message) <= 4096", name="safe_message_max_4kib"),
        CheckConstraint(
            "action_url IS NULL OR (octet_length(action_url) <= 2048 "
            "AND action_url LIKE '/%' AND position('?' IN action_url) = 0 "
            "AND position('#' IN action_url) = 0)",
            name="action_url_safe_relative_path",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    project_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    notification_type: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16), server_default="INFO")
    title: Mapped[str] = mapped_column(Text)
    safe_message: Mapped[str] = mapped_column(Text)
    action_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
