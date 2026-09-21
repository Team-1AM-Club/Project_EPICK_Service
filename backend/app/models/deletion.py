from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

DELETION_REQUEST_STATUS_VALUES = (
    "'REQUESTED', 'CONFIRMED', 'RUNNING', 'COMPLETED', "
    "'PARTIALLY_COMPLETED', 'FAILED_RETRYABLE', 'EXPIRED'"
)
DELETION_TARGET_STATUS_VALUES = "'QUEUED', 'DISPATCHED', 'ACKNOWLEDGED', 'FAILED_RETRYABLE'"
DeletionStoreType = Literal[
    "POSTGRESQL",
    "NEO4J",
    "VECTOR",
    "CACHE",
    "CHECKPOINT",
    "W3_CORE_RUNTIME",
]
DELETION_STORE_TYPES: tuple[DeletionStoreType, ...] = (
    "POSTGRESQL",
    "NEO4J",
    "VECTOR",
    "CACHE",
    "CHECKPOINT",
    "W3_CORE_RUNTIME",
)
DELETION_STORE_TYPE_VALUES = ", ".join(f"'{value}'" for value in DELETION_STORE_TYPES)


class DeletionRequest(Base):
    """A private-data deletion workflow; public company knowledge is intentionally out of scope."""

    __tablename__ = "deletion_requests"
    __table_args__ = (
        CheckConstraint(
            "owner_user_id IS NOT NULL OR subject_tombstone_hash IS NOT NULL",
            name="subject_reference_present",
        ),
        CheckConstraint("length(btrim(target_type)) > 0", name="target_type_present"),
        CheckConstraint("length(btrim(scope)) > 0", name="scope_present"),
        CheckConstraint(f"status IN ({DELETION_REQUEST_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint(
            "owner_deletion_epoch IS NULL OR owner_deletion_epoch >= 1",
            name="owner_deletion_epoch_positive",
        ),
        CheckConstraint(
            "(status IN ('RUNNING', 'COMPLETED', 'PARTIALLY_COMPLETED', 'FAILED_RETRYABLE') "
            "AND owner_deletion_epoch IS NOT NULL) OR "
            "(status IN ('REQUESTED', 'CONFIRMED', 'EXPIRED'))",
            name="epoch_required_after_start",
        ),
        CheckConstraint(
            "(status IN ('CONFIRMED', 'RUNNING', 'COMPLETED', 'PARTIALLY_COMPLETED', "
            "'FAILED_RETRYABLE') "
            "AND confirmed_at IS NOT NULL AND token_consumed_at IS NOT NULL) OR "
            "status IN ('REQUESTED', 'EXPIRED')",
            name="confirmation_required_after_confirm",
        ),
        UniqueConstraint("preview_token_hash", name="preview_token_hash"),
        UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    subject_tombstone_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    owner_deletion_epoch: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    scope: Mapped[str] = mapped_column(String(64))
    preview_token_hash: Mapped[str] = mapped_column(String(128))
    preview_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), server_default="REQUESTED")
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    token_consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeletionTarget(Base):
    """One mandatory private-store acknowledgement for a deletion request."""

    __tablename__ = "deletion_targets"
    __table_args__ = (
        CheckConstraint(f"store_type IN ({DELETION_STORE_TYPE_VALUES})", name="store_type_allowed"),
        CheckConstraint(f"status IN ({DELETION_TARGET_STATUS_VALUES})", name="status_allowed"),
        CheckConstraint("length(btrim(resource_type)) > 0", name="resource_type_present"),
        CheckConstraint("attempts >= 0", name="attempts_not_negative"),
        CheckConstraint(
            "(status = 'ACKNOWLEDGED' AND ack_epoch IS NOT NULL AND ack_event_id IS NOT NULL "
            "AND completed_at IS NOT NULL) OR status <> 'ACKNOWLEDGED'",
            name="ack_fields_required",
        ),
        ForeignKeyConstraint(
            ["deletion_request_id"],
            ["deletion_requests.id"],
            name="request_id",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "deletion_request_id",
            "store_type",
            "resource_type",
            "resource_id",
            name="request_store_resource",
        ),
        UniqueConstraint("ack_event_id", name="ack_event_id"),
        UniqueConstraint("id", "deletion_request_id", name="id_request_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    deletion_request_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    store_type: Mapped[DeletionStoreType] = mapped_column(String(32))
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    ack_epoch: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(32), server_default="QUEUED")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ack_event_id: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
