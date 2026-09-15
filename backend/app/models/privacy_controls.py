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

CONSENT_TYPE_ANALYTICS = "ANALYTICS"
DECISION_HELPFULNESS_VALUES = "'HELPFUL', 'NOT_HELPFUL', 'NOT_SURE'"
SENSITIVITY_ASSESSMENT_STATUS_VALUES = "'PENDING', 'REVIEW_REQUIRED', 'DECIDED', 'FAILED'"
SENSITIVITY_DECISION_VALUES = "'KEEP', 'REDACT_BEFORE_EXTERNAL', 'EXCLUDE_FROM_EXTERNAL', 'DELETE'"


class UserSettings(Base):
    __tablename__ = "user_settings"
    __table_args__ = (
        CheckConstraint("lock_version >= 1", name="lock_version_positive"),
        CheckConstraint("jsonb_typeof(display_options) = 'object'", name="display_options_object"),
        CheckConstraint(
            "octet_length(display_options::text) <= 4096", name="display_options_max_4kib"
        ),
    )

    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    locale: Mapped[str] = mapped_column(String(32))
    timezone: Mapped[str] = mapped_column(String(64))
    display_options: Mapped[dict[str, object]] = mapped_column(JSONB)
    lock_version: Mapped[int] = mapped_column(Integer, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RecommendationPreference(Base):
    __tablename__ = "recommendation_preferences"
    __table_args__ = (
        CheckConstraint("default_candidate_limit > 0", name="default_candidate_limit_positive"),
        CheckConstraint("lock_version >= 1", name="lock_version_positive"),
    )

    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    default_candidate_limit: Mapped[int] = mapped_column(Integer)
    question_display_mode: Mapped[str] = mapped_column(String(64))
    evidence_display_mode: Mapped[str] = mapped_column(String(64))
    show_information_completeness: Mapped[bool] = mapped_column(Boolean)
    lock_version: Mapped[int] = mapped_column(Integer, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectRecommendationPreference(Base):
    __tablename__ = "project_recommendation_preferences"
    __table_args__ = (
        CheckConstraint(
            "candidate_limit IS NULL OR candidate_limit > 0", name="candidate_limit_positive"
        ),
        CheckConstraint("lock_version >= 1", name="lock_version_positive"),
    )

    project_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    candidate_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    question_display_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_display_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    show_information_completeness: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    lock_version: Mapped[int] = mapped_column(Integer, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SnapshotRecommendationPreference(Base):
    __tablename__ = "snapshot_recommendation_preferences"
    __table_args__ = (
        CheckConstraint("candidate_limit > 0", name="candidate_limit_positive"),
        CheckConstraint(
            "user_preference_lock_version >= 1", name="user_preference_lock_version_positive"
        ),
        CheckConstraint(
            "project_preference_lock_version IS NULL OR project_preference_lock_version >= 1",
            name="project_preference_lock_version_positive",
        ),
    )

    snapshot_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    candidate_limit: Mapped[int] = mapped_column(Integer)
    question_display_mode: Mapped[str] = mapped_column(String(64))
    evidence_display_mode: Mapped[str] = mapped_column(String(64))
    show_information_completeness: Mapped[bool] = mapped_column(Boolean)
    user_preference_lock_version: Mapped[int] = mapped_column(Integer)
    project_preference_lock_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Consent(Base):
    __tablename__ = "consents"
    __table_args__ = (
        CheckConstraint("length(btrim(consent_type)) > 0", name="consent_type_present"),
        CheckConstraint("length(btrim(policy_version)) > 0", name="policy_version_present"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    consent_type: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(64))
    granted: Mapped[bool] = mapped_column(Boolean)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RetentionPreference(Base):
    __tablename__ = "retention_preferences"
    __table_args__ = (
        CheckConstraint("length(btrim(option_id)) > 0", name="option_id_present"),
        CheckConstraint("length(btrim(policy_version)) > 0", name="policy_version_present"),
        CheckConstraint("lock_version >= 1", name="lock_version_positive"),
    )

    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    option_id: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(64))
    lock_version: Mapped[int] = mapped_column(Integer, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (
        CheckConstraint(
            "decision_helpfulness IS NULL OR "
            f"decision_helpfulness IN ({DECISION_HELPFULNESS_VALUES})",
            name="decision_helpfulness_allowed",
        ),
        CheckConstraint("length(btrim(category_l1)) > 0", name="category_l1_present"),
        CheckConstraint(
            "(missing_activity_id IS NOT NULL)::integer + "
            "(missing_episode_id IS NOT NULL)::integer <= 1",
            name="at_most_one_missing_resource",
        ),
        CheckConstraint(
            "other_text IS NULL OR octet_length(other_text) <= 4096", name="other_text_max_4kib"
        ),
        CheckConstraint(
            "candidate_id IS NULL OR run_id IS NOT NULL", name="candidate_requires_run"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    run_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    snapshot_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    candidate_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    decision_helpfulness: Mapped[str | None] = mapped_column(String(16), nullable=True)
    category_l1: Mapped[str] = mapped_column(String(64))
    category_l2: Mapped[str | None] = mapped_column(String(64), nullable=True)
    other_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    missing_activity_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    missing_episode_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalyticsEvent(Base):
    __tablename__ = "analytics_events"
    __table_args__ = (
        UniqueConstraint("pseudonymous_subject_id", "event_key", name="subject_event_key"),
        CheckConstraint(
            "length(btrim(pseudonymous_subject_id)) > 0", name="pseudonymous_subject_present"
        ),
        CheckConstraint("length(btrim(event_key)) > 0", name="event_key_present"),
        CheckConstraint("length(btrim(event_type)) > 0", name="event_type_present"),
        CheckConstraint(
            "jsonb_typeof(allowed_properties) = 'object'", name="allowed_properties_object"
        ),
        CheckConstraint(
            "octet_length(allowed_properties::text) <= 4096", name="allowed_properties_max_4kib"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    event_key: Mapped[str] = mapped_column(String(128))
    pseudonymous_subject_id: Mapped[str] = mapped_column(String(128))
    owner_user_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64))
    project_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    question_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    run_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    consent_policy_version: Mapped[str] = mapped_column(String(64))
    allowed_properties: Mapped[dict[str, object]] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SensitivityAssessment(Base):
    __tablename__ = "sensitivity_assessments"
    __table_args__ = (
        UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
        CheckConstraint(
            "(activity_version_id IS NOT NULL)::integer + "
            "(episode_version_id IS NOT NULL)::integer = 1",
            name="exactly_one_version",
        ),
        CheckConstraint("length(btrim(detector_version)) > 0", name="detector_version_present"),
        CheckConstraint(
            f"status IN ({SENSITIVITY_ASSESSMENT_STATUS_VALUES})", name="status_allowed"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    activity_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    episode_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    detector_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), server_default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SensitivityFinding(Base):
    __tablename__ = "sensitivity_findings"
    __table_args__ = (
        CheckConstraint("length(btrim(category)) > 0", name="category_present"),
        CheckConstraint("length(btrim(field_name)) > 0", name="field_name_present"),
        CheckConstraint("length(btrim(severity)) > 0", name="severity_present"),
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
    assessment_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    category: Mapped[str] = mapped_column(String(64))
    field_name: Mapped[str] = mapped_column(String(128))
    source_span_start: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_span_end: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    severity: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SensitivityDecision(Base):
    __tablename__ = "sensitivity_decisions"
    __table_args__ = (
        UniqueConstraint("assessment_id", "decision_no", name="assessment_id_decision_no"),
        CheckConstraint("decision_no >= 1", name="decision_no_positive"),
        CheckConstraint(f"decision IN ({SENSITIVITY_DECISION_VALUES})", name="decision_allowed"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    assessment_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    decision_no: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(32))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
