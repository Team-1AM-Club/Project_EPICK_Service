from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
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


class FieldAvailability(str, Enum):
    PROVIDED = "PROVIDED"
    SKIPPED = "SKIPPED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_REMEMBERED = "NOT_REMEMBERED"
    NOT_PROVIDED = "NOT_PROVIDED"


AVAILABILITY_VALUES = "'PROVIDED', 'SKIPPED', 'NOT_APPLICABLE', 'NOT_REMEMBERED', 'NOT_PROVIDED'"
ACTIVITY_REGISTRATION_VALUES = "'DRAFT', 'COMPLETED'"
DELETION_VALUES = "'ACTIVE', 'DELETE_REQUESTED', 'DELETING', 'DELETED'"
OUTCOME_VALUES = "'SUCCEEDED', 'PARTIALLY_ACHIEVED', 'FAILED', 'IN_PROGRESS', 'NO_CLEAR_OUTCOME'"


def _availability_pair_constraints(*pairs: tuple[str, str]) -> tuple[CheckConstraint, ...]:
    constraints: list[CheckConstraint] = []
    for availability_column, value_column in pairs:
        constraints.extend(
            (
                CheckConstraint(
                    f"{availability_column} IN ({AVAILABILITY_VALUES})",
                    name=f"{availability_column}_allowed",
                ),
                CheckConstraint(
                    f"({availability_column} = 'PROVIDED') = ({value_column} IS NOT NULL)",
                    name=f"{value_column}_matches_availability",
                ),
            )
        )
    return tuple(constraints)


class _SafeExperienceRepr:
    """Keep user-authored Experience text out of diagnostic representations."""

    def __repr__(self) -> str:
        parts = [f"id={getattr(self, 'id', None)!s}"]
        owner_user_id = getattr(self, "owner_user_id", None)
        if owner_user_id is not None:
            parts.append(f"owner_user_id={owner_user_id!s}")
        version_no = getattr(self, "version_no", None)
        if version_no is not None:
            parts.append(f"version_no={version_no!s}")
        return f"{type(self).__name__}({', '.join(parts)})"


class Activity(_SafeExperienceRepr, Base):
    __tablename__ = "activities"
    __table_args__ = (
        UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
        CheckConstraint(
            f"registration_status IN ({ACTIVITY_REGISTRATION_VALUES})",
            name="registration_status_allowed",
        ),
        CheckConstraint(f"deletion_status IN ({DELETION_VALUES})", name="deletion_status_allowed"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    current_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    registration_status: Mapped[str] = mapped_column(String(32), server_default="DRAFT")
    usage_enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    lock_version: Mapped[int] = mapped_column(Integer, server_default="1")
    deletion_status: Mapped[str] = mapped_column(String(32), server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Episode(_SafeExperienceRepr, Base):
    __tablename__ = "episodes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["activity_id", "owner_user_id"],
            ["activities.id", "activities.owner_user_id"],
            name="activity_id_owner_user_id_activities",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
        UniqueConstraint("id", "activity_id", "owner_user_id", name="id_activity_id_owner_user_id"),
        CheckConstraint(
            f"registration_status IN ({ACTIVITY_REGISTRATION_VALUES})",
            name="registration_status_allowed",
        ),
        CheckConstraint(f"deletion_status IN ({DELETION_VALUES})", name="deletion_status_allowed"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    activity_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    current_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    registration_status: Mapped[str] = mapped_column(String(32), server_default="DRAFT")
    usage_enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    lock_version: Mapped[int] = mapped_column(Integer, server_default="1")
    deletion_status: Mapped[str] = mapped_column(String(32), server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ActivityVersion(_SafeExperienceRepr, Base):
    __tablename__ = "activity_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["activity_id", "owner_user_id"],
            ["activities.id", "activities.owner_user_id"],
            name="activity_id_owner_user_id_activities",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("activity_id", "version_no", name="activity_id_version_no"),
        UniqueConstraint("activity_id", "id", name="activity_id_id"),
        UniqueConstraint("id", "activity_id", "owner_user_id", name="id_activity_id_owner_user_id"),
        UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
        CheckConstraint(
            "outcome_status IS NULL OR outcome_status IN (" + OUTCOME_VALUES + ")",
            name="outcome_status_allowed",
        ),
        *(
            _availability_pair_constraints(
                ("organization_availability", "organization_text"),
                ("activity_type_availability", "activity_type"),
                ("role_availability", "role_text"),
            )
        ),
        CheckConstraint(
            f"period_availability IN ({AVAILABILITY_VALUES})", name="period_availability_allowed"
        ),
        CheckConstraint(
            f"outcome_availability IN ({AVAILABILITY_VALUES})", name="outcome_availability_allowed"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    activity_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    version_no: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    organization_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    organization_availability: Mapped[FieldAvailability] = mapped_column(String(32))
    activity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    activity_type_availability: Mapped[FieldAvailability] = mapped_column(String(32))
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_precision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    period_availability: Mapped[FieldAvailability] = mapped_column(String(32))
    role_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_availability: Mapped[FieldAvailability] = mapped_column(String(32))
    outcome_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    outcome_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome_availability: Mapped[FieldAvailability] = mapped_column(String(32))
    original_narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EpisodeVersion(_SafeExperienceRepr, Base):
    __tablename__ = "episode_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["episode_id", "activity_id", "owner_user_id"],
            ["episodes.id", "episodes.activity_id", "episodes.owner_user_id"],
            name="episode_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["activity_version_id", "activity_id", "owner_user_id"],
            [
                "activity_versions.id",
                "activity_versions.activity_id",
                "activity_versions.owner_user_id",
            ],
            name="activity_version_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("episode_id", "version_no", name="episode_id_version_no"),
        UniqueConstraint("episode_id", "id", name="episode_id_id"),
        UniqueConstraint(
            "id",
            "episode_id",
            "activity_id",
            "owner_user_id",
            name="id_episode_id_activity_id_owner_user_id",
        ),
        UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
        *(
            _availability_pair_constraints(
                ("situation_availability", "situation_text"),
                ("problem_availability", "problem_text"),
                ("goal_availability", "goal_text"),
                ("actions_availability", "actions_text"),
                ("decisions_availability", "decisions_text"),
                ("decision_reasons_availability", "decision_reasons_text"),
                ("result_availability", "result_text"),
                ("learning_availability", "learning_text"),
            )
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    episode_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    version_no: Mapped[int] = mapped_column(Integer)
    activity_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    activity_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    title: Mapped[str] = mapped_column(Text)
    situation_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    situation_availability: Mapped[FieldAvailability] = mapped_column(String(32))
    problem_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    problem_availability: Mapped[FieldAvailability] = mapped_column(String(32))
    goal_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    goal_availability: Mapped[FieldAvailability] = mapped_column(String(32))
    actions_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    actions_availability: Mapped[FieldAvailability] = mapped_column(String(32))
    decisions_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    decisions_availability: Mapped[FieldAvailability] = mapped_column(String(32))
    decision_reasons_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision_reasons_availability: Mapped[FieldAvailability] = mapped_column(String(32))
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_availability: Mapped[FieldAvailability] = mapped_column(String(32))
    learning_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    learning_availability: Mapped[FieldAvailability] = mapped_column(String(32))
    original_narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EpisodeVersionSkill(_SafeExperienceRepr, Base):
    __tablename__ = "episode_version_skills"
    __table_args__ = (
        ForeignKeyConstraint(
            ["episode_version_id", "owner_user_id"],
            ["episode_versions.id", "episode_versions.owner_user_id"],
            name="version_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "episode_version_id", "raw_name", "origin", name="episode_version_id_raw_name_origin"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    episode_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    raw_name: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExperienceFieldProvenance(_SafeExperienceRepr, Base):
    __tablename__ = "experience_field_provenance"
    __table_args__ = (
        ForeignKeyConstraint(
            ["activity_version_id", "owner_user_id"],
            ["activity_versions.id", "activity_versions.owner_user_id"],
            name="activity_version_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["episode_version_id", "owner_user_id"],
            ["episode_versions.id", "episode_versions.owner_user_id"],
            name="episode_version_scope",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(activity_version_id IS NOT NULL)::integer "
            "+ (episode_version_id IS NOT NULL)::integer = 1",
            name="exactly_one_version",
        ),
        CheckConstraint("origin = 'USER_INPUT'", name="origin_user_input_only"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    activity_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    episode_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    field_name: Mapped[str] = mapped_column(String(128))
    origin: Mapped[str] = mapped_column(String(32), server_default="USER_INPUT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
