from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

JOB_STATUS_VALUES = (
    "'QUEUED', 'RUNNING', 'WAITING_USER', 'PAUSED_RATE_LIMIT', 'SUCCEEDED', "
    "'FAILED_RETRYABLE', 'FAILED_FINAL', 'CANCEL_REQUESTED', 'CANCELLED'"
)
JOB_COMPLETENESS_VALUES = "'none', 'partial', 'complete'"
DISPATCH_STATUS_VALUES = "'OUTBOX_PENDING', 'ENQUEUED', 'CLAIMED', 'BLOCKED', 'INVALIDATED'"
COMMAND_STATUS_VALUES = "'PENDING', 'ENQUEUED', 'CLAIMED', 'CONSUMED', 'INVALIDATED', 'FAILED'"
REQUIRED_ACTION_STATUS_VALUES = "'OPEN', 'RESOLVED', 'DISMISSED'"
# ``FAILED`` remains readable for rows written before PG-4.  New relay code uses the
# explicit retry/final states and never needs to infer whether a failure is retryable.
OUTBOX_STATUS_VALUES = (
    "'PENDING', 'PUBLISHING', 'PUBLISHED', 'FAILED_RETRYABLE', 'FAILED_FINAL', 'FAILED'"
)
VISIBILITY_SCOPE_VALUES = "'PUBLIC', 'PRIVATE'"


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "owner_user_id"],
            ["application_projects.id", "application_projects.owner_user_id"],
            name="project_owner_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["idempotency_record_id", "owner_user_id"],
            ["idempotency_records.id", "idempotency_records.owner_user_id"],
            name="idempotency_record_owner_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
        UniqueConstraint("idempotency_record_id", name="idempotency_record_id"),
        CheckConstraint(f"status IN ({JOB_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint(
            f"completeness IN ({JOB_COMPLETENESS_VALUES})", name="completeness_allowed"
        ),
        CheckConstraint(
            f"dispatch_status IN ({DISPATCH_STATUS_VALUES})", name="dispatch_status_allowed"
        ),
        CheckConstraint("execution_fence >= 1", name="execution_fence_positive"),
        CheckConstraint("owner_deletion_epoch >= 0", name="owner_deletion_epoch_not_negative"),
        CheckConstraint("completed_units >= 0", name="completed_units_not_negative"),
        CheckConstraint("total_units IS NULL OR total_units >= 0", name="total_units_not_negative"),
        CheckConstraint(
            "total_units IS NULL OR completed_units <= total_units",
            name="completed_units_at_most_total",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    project_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    job_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), server_default="QUEUED")
    completeness: Mapped[str] = mapped_column(String(16), server_default="none")
    dispatch_status: Mapped[str] = mapped_column(String(32), server_default="OUTBOX_PENDING")
    execution_fence: Mapped[int] = mapped_column(BigInteger, server_default="1")
    owner_deletion_epoch: Mapped[int] = mapped_column(BigInteger)
    analysis_input_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_record_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    completed_units: Mapped[int] = mapped_column(Integer, server_default="0")
    total_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    safe_failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool] = mapped_column(default=False)
    retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_lease_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobInputRef(Base):
    __tablename__ = "job_input_refs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["jobs.id", "jobs.owner_user_id"],
            name="job_owner_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_version_id", "owner_user_id"],
            ["application_project_versions.id", "application_project_versions.owner_user_id"],
            name="project_version_owner_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["question_version_id", "owner_user_id"],
            ["question_versions.id", "question_versions.owner_user_id"],
            name="question_version_owner_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["episode_version_id", "owner_user_id"],
            ["episode_versions.id", "episode_versions.owner_user_id"],
            name="episode_version_owner_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "owner_user_id"],
            ["project_snapshots.id", "project_snapshots.owner_user_id"],
            name="snapshot_owner_scope",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(project_version_id IS NOT NULL)::integer + "
            "(question_version_id IS NOT NULL)::integer + "
            "(episode_version_id IS NOT NULL)::integer + "
            "(snapshot_id IS NOT NULL)::integer + "
            "((policy_name IS NOT NULL AND policy_version IS NOT NULL)::integer) = 1 "
            "AND (policy_name IS NULL) = (policy_version IS NULL)",
            name="exactly_one_input",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    project_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    question_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    episode_version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    snapshot_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    policy_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobRequiredAction(Base):
    __tablename__ = "job_required_actions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["jobs.id", "jobs.owner_user_id"],
            name="job_owner_scope",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"action_status IN ({REQUIRED_ACTION_STATUS_VALUES})", name="action_status_allowed"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    action_code: Mapped[str] = mapped_column(String(64))
    action_status: Mapped[str] = mapped_column(String(32), server_default="OPEN")
    context_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class JobCommand(Base):
    __tablename__ = "job_commands"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["jobs.id", "jobs.owner_user_id"],
            name="job_owner_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("job_id", "command_sequence", name="job_id_command_sequence"),
        UniqueConstraint("id", "job_id", "owner_user_id", name="id_job_id_owner_user_id"),
        CheckConstraint("command_sequence >= 1", name="command_sequence_positive"),
        CheckConstraint("execution_fence >= 1", name="execution_fence_positive"),
        CheckConstraint("owner_deletion_epoch >= 0", name="owner_deletion_epoch_not_negative"),
        CheckConstraint(f"status IN ({COMMAND_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint("octet_length(payload::text) <= 16384", name="payload_max_16kib"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    command_type: Mapped[str] = mapped_column(String(64))
    command_schema_version: Mapped[str] = mapped_column(String(32))
    command_sequence: Mapped[int] = mapped_column(Integer)
    execution_fence: Mapped[int] = mapped_column(BigInteger)
    owner_deletion_epoch: Mapped[int] = mapped_column(BigInteger)
    analysis_input_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), server_default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["command_id", "job_id", "owner_user_id"],
            ["job_commands.id", "job_commands.job_id", "job_commands.owner_user_id"],
            name="command_job_owner_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["jobs.id", "jobs.owner_user_id"],
            name="job_owner_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["deletion_request_id"],
            ["deletion_requests.id"],
            name="deletion_request_id",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["deletion_target_id"],
            ["deletion_targets.id"],
            name="deletion_target_id",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"visibility_scope IN ({VISIBILITY_SCOPE_VALUES})", name="visibility_scope_allowed"
        ),
        CheckConstraint(f"status IN ({OUTBOX_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint("aggregate_revision >= 1", name="aggregate_revision_positive"),
        CheckConstraint("attempts >= 0", name="attempts_not_negative"),
        CheckConstraint(
            "(visibility_scope = 'PUBLIC' AND owner_user_id IS NULL AND job_id IS NULL "
            "AND command_id IS NULL AND execution_fence IS NULL AND owner_deletion_epoch IS NULL "
            "AND deletion_request_id IS NULL AND deletion_target_id IS NULL) "
            "OR (visibility_scope = 'PRIVATE' AND owner_user_id IS NOT NULL AND job_id IS NOT NULL "
            "AND command_id IS NOT NULL AND execution_fence IS NOT NULL "
            "AND owner_deletion_epoch IS NOT NULL AND deletion_request_id IS NULL "
            "AND deletion_target_id IS NULL) "
            "OR (visibility_scope = 'PRIVATE' AND owner_user_id IS NOT NULL AND job_id IS NULL "
            "AND command_id IS NULL AND execution_fence IS NULL "
            "AND owner_deletion_epoch IS NOT NULL "
            "AND deletion_request_id IS NOT NULL AND deletion_target_id IS NOT NULL)",
            name="visibility_reference_scope",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    message_type: Mapped[str] = mapped_column(String(128))
    schema_version: Mapped[str] = mapped_column(String(32))
    visibility_scope: Mapped[str] = mapped_column(String(16))
    aggregate_type: Mapped[str] = mapped_column(String(64))
    aggregate_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    aggregate_revision: Mapped[int] = mapped_column(BigInteger)
    command_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    job_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    deletion_request_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    deletion_target_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), nullable=True
    )
    owner_user_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    execution_fence: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    owner_deletion_epoch: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), server_default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InboxReceipt(Base):
    __tablename__ = "inbox_receipts"

    consumer_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    outcome_code: Mapped[str] = mapped_column(String(64))


class OwnerExecutionSlot(Base):
    __tablename__ = "owner_execution_slots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["jobs.id", "jobs.owner_user_id"],
            name="job_owner_scope",
            ondelete="RESTRICT",
        ),
        CheckConstraint("slot_no BETWEEN 1 AND 3", name="slot_no_in_range"),
    )

    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    slot_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    lease_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobExecutionLease(Base):
    __tablename__ = "job_execution_leases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["jobs.id", "jobs.owner_user_id"],
            name="job_owner_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_user_id", "slot_no"],
            ["owner_execution_slots.owner_user_id", "owner_execution_slots.slot_no"],
            name="slot_scope",
            ondelete="RESTRICT",
        ),
        CheckConstraint("execution_fence >= 1", name="execution_fence_positive"),
        CheckConstraint("owner_deletion_epoch >= 0", name="owner_deletion_epoch_not_negative"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    slot_no: Mapped[int] = mapped_column(Integer)
    execution_fence: Mapped[int] = mapped_column(BigInteger)
    owner_deletion_epoch: Mapped[int] = mapped_column(BigInteger)
    worker_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    release_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
