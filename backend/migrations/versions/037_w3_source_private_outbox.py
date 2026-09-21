"""Allow only the adopted W3 Source-retirement shape as a private outbox row.

Revision ID: 037_w3_source_private_outbox
Revises: 036_w3_deletion_completion
Create Date: 2026-09-20
"""

from __future__ import annotations

from alembic import op

revision = "037_w3_source_private_outbox"
down_revision = "036_w3_deletion_completion"
branch_labels = None
depends_on = None


_VISIBILITY_REFERENCE_SCOPE = (
    "(visibility_scope = 'PUBLIC' AND owner_user_id IS NULL AND job_id IS NULL "
    "AND command_id IS NULL AND execution_fence IS NULL AND owner_deletion_epoch IS NULL "
    "AND deletion_request_id IS NULL AND deletion_target_id IS NULL "
    "AND recommendation_run_id IS NULL AND jsonb_typeof(payload) = 'object' "
    "AND NOT (payload ?| ARRAY['owner_id', 'owner_user_id', 'job_id', 'command_id', "
    "'authenticated_owner_ref', 'project_id', 'auth_subject', 'email', "
    "'checkpoint', 'prompt', 'response', 'secret', 'token'])) "
    "OR (visibility_scope = 'PRIVATE' AND owner_user_id IS NOT NULL AND job_id IS NOT NULL "
    "AND command_id IS NOT NULL AND execution_fence IS NOT NULL "
    "AND owner_deletion_epoch IS NOT NULL AND deletion_request_id IS NULL "
    "AND deletion_target_id IS NULL AND recommendation_run_id IS NULL "
    "AND jsonb_typeof(payload) = 'object') "
    "OR (visibility_scope = 'PRIVATE' AND owner_user_id IS NOT NULL AND job_id IS NULL "
    "AND command_id IS NULL AND execution_fence IS NULL "
    "AND owner_deletion_epoch IS NOT NULL "
    "AND deletion_request_id IS NOT NULL AND deletion_target_id IS NOT NULL "
    "AND recommendation_run_id IS NULL AND jsonb_typeof(payload) = 'object') "
    "OR (visibility_scope = 'PRIVATE' AND owner_user_id IS NOT NULL "
    "AND recommendation_run_id IS NOT NULL AND job_id IS NULL AND command_id IS NULL "
    "AND execution_fence IS NULL AND owner_deletion_epoch IS NOT NULL "
    "AND deletion_request_id IS NULL AND deletion_target_id IS NULL "
    "AND jsonb_typeof(payload) = 'object') "
    "OR (visibility_scope = 'PRIVATE' "
    "AND message_type = 'w1.private.w3.source-retirement.v1' "
    "AND schema_version = 'w1.w3.source-retirement/1' "
    "AND aggregate_type = 'W3_SOURCE_RETIREMENT' "
    "AND owner_user_id IS NULL AND job_id IS NULL AND command_id IS NULL "
    "AND execution_fence IS NULL AND owner_deletion_epoch IS NULL "
    "AND deletion_request_id IS NULL AND deletion_target_id IS NULL "
    "AND recommendation_run_id IS NULL AND jsonb_typeof(payload) = 'object')"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_outbox_messages_visibility_reference_scope",
        "outbox_messages",
        type_="check",
    )
    op.create_check_constraint(
        "ck_outbox_messages_visibility_reference_scope",
        "outbox_messages",
        _VISIBILITY_REFERENCE_SCOPE,
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 037_w3_source_private_outbox is forward-only: reverting would "
        "invalidate adopted private Source-retirement rows"
    )
