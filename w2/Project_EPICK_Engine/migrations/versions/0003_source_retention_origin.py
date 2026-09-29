"""Add retained source bodies and explicit source-origin provenance.

Revision ID: 0003_source_retention_origin
Revises: 0002_job_posting
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_source_retention_origin"
down_revision: str | None = "0002_job_posting"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "retained_bodies",
        sa.Column("body_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("normalization_version", sa.String(length=128), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("necessity_reason", sa.Text(), nullable=False),
        sa.Column("policy_decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("retained_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_policy_version", sa.String(length=128), nullable=False),
        sa.Column("retention_limit_bytes", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "length(btrim(normalization_version)) > 0",
            name="nonempty_normalization_version",
        ),
        sa.CheckConstraint(
            "length(btrim(body_text)) > 0",
            name="nonempty_body_text",
        ),
        sa.CheckConstraint(
            "length(btrim(necessity_reason)) > 0",
            name="nonempty_necessity_reason",
        ),
        sa.CheckConstraint(
            "length(btrim(retention_policy_version)) > 0",
            name="nonempty_retention_policy_version",
        ),
        sa.CheckConstraint(
            "retention_limit_bytes > 0",
            name="positive_retention_limit_bytes",
        ),
        sa.CheckConstraint(
            "octet_length(body_text) BETWEEN 1 AND retention_limit_bytes",
            name="body_within_retention_limit",
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id", "source_id"],
            ["source_versions.source_version_id", "source_versions.source_id"],
            name="fk_retained_bodies_version_same_source",
        ),
        sa.ForeignKeyConstraint(
            ["policy_decision_id", "source_id"],
            [
                "source_policy_decisions.policy_decision_id",
                "source_policy_decisions.source_id",
            ],
            name="fk_retained_bodies_policy_same_source",
        ),
        sa.PrimaryKeyConstraint("body_id", name="pk_retained_bodies"),
        sa.UniqueConstraint(
            "source_version_id",
            "normalization_version",
            name="uq_retained_bodies_version_normalization",
        ),
    )

    op.create_table(
        "source_origins",
        sa.Column("origin_relation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("origin_source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("origin_url", sa.Text(), nullable=True),
        sa.Column("relationship_kind", sa.String(length=64), nullable=False),
        sa.Column("verification_status", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "origin_source_id IS NOT NULL OR origin_url IS NOT NULL",
            name="has_target",
        ),
        sa.CheckConstraint(
            "origin_source_id IS NULL OR origin_source_id <> source_id",
            name="not_self_origin",
        ),
        sa.CheckConstraint(
            "origin_url IS NULL OR length(btrim(origin_url)) > 0",
            name="nonempty_origin_url",
        ),
        sa.CheckConstraint(
            "length(btrim(relationship_kind)) > 0",
            name="nonempty_relationship_kind",
        ),
        sa.CheckConstraint(
            "length(btrim(verification_status)) > 0",
            name="nonempty_verification_status",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.source_id"],
            name="fk_source_origins_source_id",
        ),
        sa.ForeignKeyConstraint(
            ["origin_source_id"],
            ["sources.source_id"],
            name="fk_source_origins_origin_source_id",
        ),
        sa.PrimaryKeyConstraint("origin_relation_id", name="pk_source_origins"),
    )
    op.create_index(
        "uq_source_origins_source_kind_origin_source",
        "source_origins",
        ["source_id", "relationship_kind", "origin_source_id"],
        unique=True,
        postgresql_where=sa.text("origin_source_id IS NOT NULL"),
    )
    op.create_index(
        "uq_source_origins_source_kind_origin_url",
        "source_origins",
        ["source_id", "relationship_kind", "origin_url"],
        unique=True,
        postgresql_where=sa.text("origin_url IS NOT NULL"),
    )

    op.create_table(
        "source_origin_evidence",
        sa.Column("origin_relation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["origin_relation_id"],
            ["source_origins.origin_relation_id"],
            name="fk_source_origin_evidence_origin_relation_id",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["evidence.evidence_id"],
            name="fk_source_origin_evidence_evidence_id",
        ),
        sa.PrimaryKeyConstraint(
            "origin_relation_id",
            "evidence_id",
            name="pk_source_origin_evidence",
        ),
    )


def downgrade() -> None:
    raise RuntimeError(
        "Destructive downgrade is intentionally unsupported; use a forward corrective migration."
    )
