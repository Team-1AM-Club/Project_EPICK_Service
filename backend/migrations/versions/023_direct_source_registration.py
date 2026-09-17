"""Add the W1-owned direct Source registration decision scope.

Revision ID: 023_direct_source_registration
Revises: 022_w1_runtime_relay_access
Create Date: 2026-09-17

``DIRECT_SOURCE_REGISTRATION`` is intentionally separate from W3/W4's core
analysis scopes.  It records only a W1-created non-core registration decision;
the database rejects attempts to represent it as a core decision or to attach a
question target.
"""

from alembic import op

revision = "023_direct_source_registration"
down_revision = "022_w1_runtime_relay_access"
branch_labels = None
depends_on = None


_SCOPE_ALLOWED = (
    "decision_scope IN "
    "('COMPANY_KNOWLEDGE', 'QUESTION_MATCHING', 'DIRECT_SOURCE_REGISTRATION')"
)
_SCOPE_OWNER_REFERENCE = (
    "(decision_scope = 'COMPANY_KNOWLEDGE' AND company_id IS NOT NULL "
    "AND question_version_id IS NULL AND decision_owner = 'W3') OR "
    "(decision_scope = 'QUESTION_MATCHING' AND company_id IS NULL "
    "AND question_version_id IS NOT NULL AND decision_owner = 'W4') OR "
    "(decision_scope = 'DIRECT_SOURCE_REGISTRATION' AND company_id IS NOT NULL "
    "AND question_version_id IS NULL AND decision_owner = 'W1' "
    "AND decision_code = 'NON_CORE_OPTIONAL' AND reason_code IS NOT NULL)"
)


def upgrade() -> None:
    op.drop_constraint(
        "decision_scope_allowed",
        "analysis_source_decisions",
        type_="check",
    )
    op.drop_constraint(
        "scope_owner_reference_matches",
        "analysis_source_decisions",
        type_="check",
    )
    op.create_check_constraint(
        "decision_scope_allowed",
        "analysis_source_decisions",
        _SCOPE_ALLOWED,
    )
    op.create_check_constraint(
        "scope_owner_reference_matches",
        "analysis_source_decisions",
        _SCOPE_OWNER_REFERENCE,
    )


def downgrade() -> None:
    op.drop_constraint(
        "scope_owner_reference_matches",
        "analysis_source_decisions",
        type_="check",
    )
    op.drop_constraint(
        "decision_scope_allowed",
        "analysis_source_decisions",
        type_="check",
    )
    op.create_check_constraint(
        "decision_scope_allowed",
        "analysis_source_decisions",
        "decision_scope IN ('COMPANY_KNOWLEDGE', 'QUESTION_MATCHING')",
    )
    op.create_check_constraint(
        "scope_owner_reference_matches",
        "analysis_source_decisions",
        "(decision_scope = 'COMPANY_KNOWLEDGE' AND company_id IS NOT NULL "
        "AND question_version_id IS NULL AND decision_owner = 'W3') OR "
        "(decision_scope = 'QUESTION_MATCHING' AND company_id IS NULL "
        "AND question_version_id IS NOT NULL AND decision_owner = 'W4')",
    )
