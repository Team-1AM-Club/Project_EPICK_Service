from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

W2_COMMIT_OPERATION_STATE_VALUES = (
    "'PREPARE_PENDING', 'PREPARED', 'W1_COMMITTED', 'FINALIZE_PENDING', 'FINALIZED', "
    "'ABORT_PENDING', 'ABORTED', 'PURGE_PENDING', 'PURGED', 'FAILED_FINAL'"
)
W2_STAGED_RESULT_PAYLOAD_STATE_VALUES = "'ACTIVE', 'CONSUMED', 'CLEARED'"


class W2CommitOperation(Base):
    """W1's durable private visibility gate for a single W2 child command.

    ``owner_deletion_epoch`` is the immutable epoch that bound the original W2
    command and staged result.  A later account deletion is represented only by
    ``purge_owner_deletion_epoch`` so it cannot overwrite that original binding.
    """

    __tablename__ = "w2_commit_operations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["jobs.id", "jobs.owner_user_id"],
            name="job_owner_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["command_id", "job_id", "owner_user_id"],
            ["job_commands.id", "job_commands.job_id", "job_commands.owner_user_id"],
            name="command_job_owner_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("command_id", name="command_id_once"),
        CheckConstraint("execution_fence >= 1", name="execution_fence_positive"),
        CheckConstraint("owner_deletion_epoch >= 0", name="owner_deletion_epoch_not_negative"),
        CheckConstraint("operation_revision >= 1", name="operation_revision_positive"),
        CheckConstraint(
            f"state IN ({W2_COMMIT_OPERATION_STATE_VALUES})", name="state_allowed"
        ),
        CheckConstraint(
            "result_digest ~ '^sha256:[0-9a-f]{64}$'", name="result_digest_sha256"
        ),
        CheckConstraint(
            "purge_owner_deletion_epoch IS NULL "
            "OR purge_owner_deletion_epoch > owner_deletion_epoch",
            name="purge_epoch_advances_original",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    command_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    job_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    execution_fence: Mapped[int] = mapped_column(BigInteger)
    owner_deletion_epoch: Mapped[int] = mapped_column(BigInteger)
    purge_owner_deletion_epoch: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    result_digest: Mapped[str] = mapped_column(String(71))
    operation_revision: Mapped[int] = mapped_column(BigInteger, server_default="1")
    state: Mapped[str] = mapped_column(String(32), server_default="PREPARE_PENDING")
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    w1_committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    aborted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class W2StagedResult(Base):
    """Private W2 candidate retained only until W1's local gate completes.

    The validated W2 *result* is intentionally the only body retained here.
    Queue receipt handles, credentials, DSNs, authorization headers and W2
    store references do not belong in this model. The wire parser is the
    contract boundary that makes this a durable, bounded payload rather than a
    generic message archive.
    """

    __tablename__ = "w2_staged_results"
    __table_args__ = (
        ForeignKeyConstraint(
            ["operation_id"],
            ["w2_commit_operations.id"],
            name="operation_id",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["command_id"],
            ["job_commands.id"],
            name="command_id",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="owner_user_id",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("command_id", name="command_id_once"),
        UniqueConstraint("origin_message_id", name="origin_message_id_once"),
        CheckConstraint(
            "payload_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="payload_digest_sha256",
        ),
        CheckConstraint(
            "result_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="result_digest_sha256",
        ),
        CheckConstraint(
            f"payload_state IN ({W2_STAGED_RESULT_PAYLOAD_STATE_VALUES})",
            name="payload_state_allowed",
        ),
        CheckConstraint(
            "(payload_state = 'ACTIVE' "
            "AND result_payload IS NOT NULL "
            "AND consumed_at IS NULL "
            "AND cleared_at IS NULL) "
            "OR (payload_state = 'CONSUMED' "
            "AND result_payload IS NULL "
            "AND consumed_at IS NOT NULL "
            "AND cleared_at IS NOT NULL) "
            "OR (payload_state = 'CLEARED' "
            "AND result_payload IS NULL "
            "AND consumed_at IS NULL "
            "AND cleared_at IS NOT NULL)",
            name="payload_lifecycle_consistent",
        ),
    )

    operation_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    command_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    origin_message_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    schema_version: Mapped[str] = mapped_column(String(128))
    producer_name: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload_digest: Mapped[str] = mapped_column(String(71))
    result_digest: Mapped[str] = mapped_column(String(71))
    # A tombstone must be SQL NULL so the lifecycle constraint distinguishes it
    # from a retained JSON literal ``null``.  ``none_as_null`` also makes the
    # durable payload-clear operation unambiguous for replay safety.
    result_payload: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    payload_state: Mapped[str] = mapped_column(String(16), server_default="ACTIVE")
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
