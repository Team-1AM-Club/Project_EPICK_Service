from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "account_status IN ('ACTIVE', 'SUSPENDED', 'DELETION_PENDING', 'DELETED')",
            name="account_status_allowed",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    display_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_status: Mapped[str] = mapped_column(String(32), server_default="ACTIVE")
    locale: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)
    deletion_epoch: Mapped[int] = mapped_column(BigInteger, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    auth_identities: Mapped[list[AuthIdentity]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    auth_sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class AuthIdentity(Base):
    __tablename__ = "auth_identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subject", name="provider_subject"),
        UniqueConstraint("id", "user_id", name="id_user_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    provider: Mapped[str] = mapped_column(String(32))
    provider_subject: Mapped[str] = mapped_column(Text)
    provider_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_email_verified: Mapped[bool] = mapped_column(Boolean, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="auth_identities")


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        UniqueConstraint("refresh_token_hash", name="refresh_token_hash"),
        UniqueConstraint("id", "user_id", name="id_user_id"),
        ForeignKeyConstraint(
            ["auth_identity_id", "user_id"],
            ["auth_identities.id", "auth_identities.user_id"],
            name="auth_identity_user_id",
            ondelete="CASCADE",
        ),
        CheckConstraint("expires_at > issued_at", name="expires_after_issued"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    auth_identity_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    refresh_token_hash: Mapped[str] = mapped_column(String(128))
    token_family_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    replaced_by_session_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("auth_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_ip_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_agent_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[User] = relationship(back_populates="auth_sessions")
    auth_identity: Mapped[AuthIdentity] = relationship(
        primaryjoin="AuthSession.auth_identity_id == AuthIdentity.id",
        foreign_keys="AuthSession.auth_identity_id",
    )


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("id", "owner_user_id", name="id_owner_user_id"),
        UniqueConstraint(
            "owner_user_id", "method", "path_scope", "idempotency_key", name="owner_method_path_key"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    owner_user_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    method: Mapped[str] = mapped_column(String(16))
    path_scope: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    request_hash: Mapped[str] = mapped_column(String(128))
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
