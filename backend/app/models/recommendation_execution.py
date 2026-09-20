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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RecommendationExecutionBinding(Base):
    """W1-owned immutable input fence and short-lived W4 execution lease."""

    __tablename__ = "recommendation_execution_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "owner_user_id"],
            ["recommendation_runs.id", "recommendation_runs.owner_user_id"],
            name="reb_run_owner_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint("run_id", name="reb_run_id"),
        UniqueConstraint("id", "owner_user_id", name="reb_id_owner_user_id"),
        CheckConstraint(
            "execution_status IN ('PENDING','RUNNING','PUBLISHED','FAILED','CANCELLED')",
            name="reb_execution_status_allowed",
        ),
        CheckConstraint("attempt_no >= 0", name="reb_attempt_no_not_negative"),
        CheckConstraint("owner_deletion_epoch >= 0", name="reb_owner_deletion_epoch_not_negative"),
        CheckConstraint(
            "context_sha256 ~ '^sha256:[0-9a-f]{64}$'", name="reb_context_sha256_format"
        ),
        CheckConstraint(
            "(lease_token IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="reb_lease_complete",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    project_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    question_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    question_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    snapshot_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    execution_status: Mapped[str] = mapped_column(String(16), server_default="PENDING")
    lease_token: Mapped[UUID | None] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer, server_default="0")
    owner_deletion_epoch: Mapped[int] = mapped_column(BigInteger)
    context_sha256: Mapped[str] = mapped_column(String(71))
    contract_version: Mapped[str] = mapped_column(String(64))
    engine_source_revision: Mapped[str] = mapped_column(String(64))
    request_body: Mapped[dict[str, object]] = mapped_column(JSONB)
    context_body: Mapped[dict[str, object]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RecommendationExecutionEpisode(Base):
    """Immutable W4 episode identity to W1 episode-version mapping."""

    __tablename__ = "recommendation_execution_episodes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["binding_id", "owner_user_id"],
            [
                "recommendation_execution_bindings.id",
                "recommendation_execution_bindings.owner_user_id",
            ],
            name="ree_binding_owner_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["episode_version_id", "owner_user_id"],
            ["episode_versions.id", "episode_versions.owner_user_id"],
            name="ree_episode_version_owner_scope",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "binding_id", "episode_id", "episode_version", name="ree_binding_episode_version"
        ),
        UniqueConstraint("binding_id", "episode_version_id", name="ree_binding_episode_version_id"),
        CheckConstraint("episode_version >= 1", name="ree_episode_version_positive"),
    )

    binding_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    episode_version_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    episode_id: Mapped[str] = mapped_column(String(200))
    episode_version: Mapped[int] = mapped_column(Integer)


class RecommendationPublication(Base):
    """Validated W4 result retained privately beside the public candidate projection."""

    __tablename__ = "recommendation_publications"
    __table_args__ = (
        ForeignKeyConstraint(
            ["binding_id", "owner_user_id"],
            [
                "recommendation_execution_bindings.id",
                "recommendation_execution_bindings.owner_user_id",
            ],
            name="rp_binding_owner_scope",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["run_id", "owner_user_id"],
            ["recommendation_runs.id", "recommendation_runs.owner_user_id"],
            name="rp_run_owner_scope",
            ondelete="CASCADE",
        ),
        UniqueConstraint("binding_id", name="rp_binding_id"),
        UniqueConstraint("run_id", name="rp_run_id"),
        CheckConstraint("input_data_kind = 'SYNTHETIC'", name="rp_synthetic_only"),
        CheckConstraint(
            "content_sha256 ~ '^sha256:[0-9a-f]{64}$'", name="rp_content_sha256_format"
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    binding_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    run_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    owner_user_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    lease_token: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True))
    result_version: Mapped[str] = mapped_column(String(128))
    schema_version: Mapped[str] = mapped_column(String(64))
    engine_source_revision: Mapped[str] = mapped_column(String(64))
    input_data_kind: Mapped[str] = mapped_column(String(16))
    processing_status: Mapped[str] = mapped_column(String(64))
    limited_analysis: Mapped[bool] = mapped_column(Boolean, server_default="true")
    limitations: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    full_result: Mapped[dict[str, object]] = mapped_column(JSONB)
    content_sha256: Mapped[str] = mapped_column(String(71))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RecommendationSourceDependency(Base):
    """Source-generation pins rechecked on publication and later result use."""

    __tablename__ = "recommendation_source_dependencies"
    __table_args__ = (
        UniqueConstraint("publication_id", "dependency_digest", name="rsd_publication_dependency"),
        CheckConstraint(
            "dependency_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="rsd_dependency_digest_format",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    publication_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("recommendation_publications.id", ondelete="CASCADE"),
    )
    source_id: Mapped[str] = mapped_column(String(200))
    source_version_id: Mapped[str] = mapped_column(String(200))
    extraction_revision_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    representation: Mapped[str | None] = mapped_column(String(200), nullable=True)
    normalization_version: Mapped[str | None] = mapped_column(String(200), nullable=True)
    knowledge_generation: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    restriction_revision: Mapped[str | None] = mapped_column(String(200), nullable=True)
    dependency_digest: Mapped[str] = mapped_column(String(71))
