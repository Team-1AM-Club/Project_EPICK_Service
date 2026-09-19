from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class W4QuestionCoreContext(Base):
    """An opaque, W1-issued handle for one synthetic W4 Question Core decision.

    W4 is given only ``context_key``.  The private context adapter resolves the
    rest of the binding from this row and re-reads current W1 state on every
    request.  The row deliberately contains no prompt, URL, company, or user
    profile content.
    """

    __tablename__ = "w4_question_core_contexts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["jobs.id", "jobs.owner_user_id"],
            name="job_owner_scope",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "data_kind IN ('SYNTHETIC', 'REAL')",
            name="data_kind_allowed",
        ),
        CheckConstraint("execution_fence >= 1", name="execution_fence_positive"),
        CheckConstraint("owner_deletion_epoch >= 0", name="owner_deletion_epoch_not_negative"),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
        Index("ix_w4_question_core_contexts_job", "job_id", "expires_at"),
    )

    context_key: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4
    )
    job_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    question_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    analysis_input_version: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_fence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_deletion_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    data_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
