"""Namespace immutable Core bindings for W4 question decisions.

Revision ID: 028_w4_question_core_inbound
Revises: 027_w2_staged_result_adoption
Create Date: 2026-09-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "028_w4_question_core_inbound"
down_revision = "027_w2_staged_result_adoption"
branch_labels = None
depends_on = None

_BINDING_TABLE = "job_core_decision_bindings"
_W3_SCOPE = "COMPANY_KNOWLEDGE"
_W4_SCOPE = "QUESTION_MATCHING"


def upgrade() -> None:
    _require_worker_role()

    op.add_column(
        _BINDING_TABLE,
        sa.Column("origin_producer", sa.String(length=32), nullable=True),
    )
    op.add_column(
        _BINDING_TABLE,
        sa.Column("decision_scope", sa.String(length=32), nullable=True),
    )
    op.add_column(
        _BINDING_TABLE,
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        _BINDING_TABLE,
        sa.Column("origin_decision_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_jcdb_question_version",
        _BINDING_TABLE,
        "question_versions",
        ["question_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # Revision 026 created this table solely for W3. Refuse to relabel any
    # unexpected historical row rather than silently treating it as W3.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM job_core_decision_bindings AS binding
                LEFT JOIN analysis_source_decisions AS decision
                  ON decision.id = binding.analysis_source_decision_id
                WHERE decision.id IS NULL
                   OR decision.decision_scope <> 'COMPANY_KNOWLEDGE'
                   OR decision.decision_owner <> 'W3'
                   OR decision.question_version_id IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'cannot backfill non-W3 job_core_decision_bindings as W3';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        UPDATE job_core_decision_bindings
        SET origin_producer = 'w3', decision_scope = 'COMPANY_KNOWLEDGE'
        WHERE origin_producer IS NULL AND decision_scope IS NULL
        """
    )
    op.alter_column(_BINDING_TABLE, "origin_producer", nullable=False)
    op.alter_column(_BINDING_TABLE, "decision_scope", nullable=False)

    op.drop_constraint(
        "uq_job_core_decision_bindings_origin_message_id",
        _BINDING_TABLE,
        type_="unique",
    )
    op.drop_constraint(
        "uq_job_core_decision_bindings_job_source_decision_version",
        _BINDING_TABLE,
        type_="unique",
    )
    op.drop_index("ix_job_core_decision_bindings_current", table_name=_BINDING_TABLE)

    op.create_unique_constraint(
        "uq_job_core_decision_bindings_origin_producer_origin_message_id",
        _BINDING_TABLE,
        ["origin_producer", "origin_message_id"],
    )
    op.create_check_constraint(
        "ck_job_core_decision_bindings_producer_scope_compatibility",
        _BINDING_TABLE,
        "(origin_producer = 'w3' "
        "AND decision_scope = 'COMPANY_KNOWLEDGE' "
        "AND question_version_id IS NULL "
        "AND origin_decision_id IS NULL) "
        "OR (origin_producer = 'w4' "
        "AND decision_scope = 'QUESTION_MATCHING' "
        "AND question_version_id IS NOT NULL "
        "AND origin_decision_id IS NOT NULL)",
    )
    op.create_index(
        "uq_job_core_decision_bindings_origin_producer_decision",
        _BINDING_TABLE,
        ["origin_producer", "origin_decision_id"],
        unique=True,
        postgresql_where=sa.text("origin_decision_id IS NOT NULL"),
    )
    op.create_index(
        "uq_job_core_decision_bindings_company_revision",
        _BINDING_TABLE,
        ["origin_producer", "job_id", "source_id", "decision_version"],
        unique=True,
        postgresql_where=sa.text(f"decision_scope = '{_W3_SCOPE}'"),
    )
    op.create_index(
        "uq_job_core_decision_bindings_question_revision",
        _BINDING_TABLE,
        [
            "origin_producer",
            "job_id",
            "question_version_id",
            "source_id",
            "decision_version",
        ],
        unique=True,
        postgresql_where=sa.text(f"decision_scope = '{_W4_SCOPE}'"),
    )
    op.create_index(
        "ix_job_core_decision_bindings_current",
        _BINDING_TABLE,
        [
            "origin_producer",
            "decision_scope",
            "job_id",
            "source_id",
            "analysis_input_version",
            "decision_version",
        ],
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM job_core_decision_bindings WHERE origin_producer <> 'w3'
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 028 while W4 Core Decision bindings exist';
            END IF;
        END
        $$;
        """
    )
    op.drop_index("ix_job_core_decision_bindings_current", table_name=_BINDING_TABLE)
    op.drop_index(
        "uq_job_core_decision_bindings_question_revision", table_name=_BINDING_TABLE
    )
    op.drop_index(
        "uq_job_core_decision_bindings_company_revision", table_name=_BINDING_TABLE
    )
    op.drop_index(
        "uq_job_core_decision_bindings_origin_producer_decision",
        table_name=_BINDING_TABLE,
    )
    op.drop_constraint(
        "ck_job_core_decision_bindings_producer_scope_compatibility",
        _BINDING_TABLE,
        type_="check",
    )
    op.drop_constraint(
        "uq_job_core_decision_bindings_origin_producer_origin_message_id",
        _BINDING_TABLE,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_job_core_decision_bindings_origin_message_id",
        _BINDING_TABLE,
        ["origin_message_id"],
    )
    op.create_unique_constraint(
        "uq_job_core_decision_bindings_job_source_decision_version",
        _BINDING_TABLE,
        ["job_id", "source_id", "decision_version"],
    )
    op.create_index(
        "ix_job_core_decision_bindings_current",
        _BINDING_TABLE,
        ["job_id", "source_id", "analysis_input_version", "decision_version"],
    )
    op.drop_constraint("fk_jcdb_question_version", _BINDING_TABLE, type_="foreignkey")
    op.drop_column(_BINDING_TABLE, "origin_decision_id")
    op.drop_column(_BINDING_TABLE, "question_version_id")
    op.drop_column(_BINDING_TABLE, "decision_scope")
    op.drop_column(_BINDING_TABLE, "origin_producer")


def _require_worker_role() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'epick_worker') THEN
                RAISE EXCEPTION 'epick_worker role must be provisioned before revision 028';
            END IF;
        END
        $$;
        """
    )
