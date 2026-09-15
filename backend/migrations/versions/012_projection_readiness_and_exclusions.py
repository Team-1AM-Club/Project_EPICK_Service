"""Add generic projection readiness and immutable snapshot exclusions.

Revision ID: 012_projection_readiness
Revises: 011_pg3_immutability
Create Date: 2026-09-15

This revision intentionally does not define W3 acknowledgement data, a Graph/Vector DTO,
or a retrieval route.  It only records PostgreSQL-owned delivery/readiness facts.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "012_projection_readiness"
down_revision = "011_pg3_immutability"
branch_labels = None
depends_on = None

OUTBOX_STATUS_VALUES = (
    "'PENDING', 'PUBLISHING', 'PUBLISHED', 'FAILED_RETRYABLE', 'FAILED_FINAL', 'FAILED'"
)
PROJECTION_STATUS_VALUES = "'PENDING', 'STALE', 'ERROR', 'SYNCED'"
EXCLUSION_SCOPE_VALUES = "'GLOBAL', 'COMPANY', 'ROLE', 'PROJECT'"
PUBLIC_PAYLOAD_FORBIDDEN_KEYS = (
    "ARRAY['owner_id', 'owner_user_id', 'job_id', 'command_id', "
    "'authenticated_owner_ref', 'project_id', 'auth_subject', 'email', "
    "'checkpoint', 'prompt', 'response', 'secret', 'token']"
)


def upgrade() -> None:
    op.drop_constraint("ck_outbox_messages_status_allowed", "outbox_messages", type_="check")
    op.create_check_constraint(
        "ck_outbox_messages_status_allowed",
        "outbox_messages",
        f"status IN ({OUTBOX_STATUS_VALUES})",
    )
    op.drop_constraint(
        "ck_outbox_messages_visibility_reference_scope", "outbox_messages", type_="check"
    )
    op.create_check_constraint(
        "ck_outbox_messages_visibility_reference_scope",
        "outbox_messages",
        "(visibility_scope = 'PUBLIC' AND owner_user_id IS NULL AND job_id IS NULL "
        "AND command_id IS NULL AND execution_fence IS NULL AND owner_deletion_epoch IS NULL "
        "AND jsonb_typeof(payload) = 'object' "
        f"AND NOT (payload ?| {PUBLIC_PAYLOAD_FORBIDDEN_KEYS})) "
        "OR (visibility_scope = 'PRIVATE' AND owner_user_id IS NOT NULL AND job_id IS NOT NULL "
        "AND command_id IS NOT NULL AND execution_fence IS NOT NULL "
        "AND owner_deletion_epoch IS NOT NULL AND jsonb_typeof(payload) = 'object')",
    )
    op.create_check_constraint(
        "ck_outbox_messages_payload_max_16kib",
        "outbox_messages",
        "octet_length(payload::text) <= 16384",
    )

    op.create_table(
        "projection_sync_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_version", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("projection_type", sa.String(length=64), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'PENDING'"), nullable=False
        ),
        sa.Column("last_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_applied_revision", sa.BigInteger(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"status IN ({PROJECTION_STATUS_VALUES})",
            name="ck_projection_sync_states_status_allowed",
        ),
        sa.CheckConstraint(
            "last_applied_revision IS NULL OR last_applied_revision >= 1",
            name="ck_projection_sync_states_last_applied_revision_positive",
        ),
        sa.CheckConstraint(
            "length(btrim(resource_type)) > 0",
            name="ck_projection_sync_states_resource_type_present",
        ),
        sa.CheckConstraint(
            "length(btrim(projection_type)) > 0",
            name="ck_projection_sync_states_projection_type_present",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_projection_sync_states"),
        sa.UniqueConstraint(
            "resource_type",
            "resource_id",
            "resource_version",
            "projection_type",
            name="uq_projection_sync_states_resource_version_projection_type",
        ),
    )
    op.create_index(
        "ix_projection_sync_states_status_updated",
        "projection_sync_states",
        ["status", sa.text("updated_at ASC")],
    )

    op.create_table(
        "experience_exclusions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("activity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("episode_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(activity_id IS NOT NULL)::integer + (episode_id IS NOT NULL)::integer = 1",
            name="ck_experience_exclusions_exactly_one_experience_target",
        ),
        sa.CheckConstraint(
            f"scope IN ({EXCLUSION_SCOPE_VALUES})",
            name="ck_experience_exclusions_scope_allowed",
        ),
        sa.CheckConstraint(
            "(scope = 'GLOBAL' AND company_id IS NULL AND role_id IS NULL AND project_id IS NULL) "
            "OR (scope = 'COMPANY' AND company_id IS NOT NULL "
            "AND role_id IS NULL AND project_id IS NULL) "
            "OR (scope = 'ROLE' AND company_id IS NULL "
            "AND role_id IS NOT NULL AND project_id IS NULL) "
            "OR (scope = 'PROJECT' AND company_id IS NULL "
            "AND role_id IS NULL AND project_id IS NOT NULL)",
            name="ck_experience_exclusions_scope_context_matches",
        ),
        sa.CheckConstraint(
            "reason IS NULL OR octet_length(reason) <= 1024",
            name="ck_experience_exclusions_reason_max_1kib",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_experience_exclusions_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["activity_id", "owner_user_id"],
            ["activities.id", "activities.owner_user_id"],
            name="fk_experience_exclusions_activity_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["episode_id", "owner_user_id"],
            ["episodes.id", "episodes.owner_user_id"],
            name="fk_experience_exclusions_episode_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_experience_exclusions_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["roles.id"], name="fk_experience_exclusions_role", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "owner_user_id"],
            ["application_projects.id", "application_projects.owner_user_id"],
            name="fk_experience_exclusions_project_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_experience_exclusions"),
        sa.UniqueConstraint(
            "id", "owner_user_id", name="uq_experience_exclusions_id_owner_user_id"
        ),
    )
    op.create_index(
        "uq_experience_exclusions_active_target_scope",
        "experience_exclusions",
        [
            "owner_user_id",
            "activity_id",
            "episode_id",
            "scope",
            "company_id",
            "role_id",
            "project_id",
        ],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "ix_experience_exclusions_owner_active",
        "experience_exclusions",
        ["owner_user_id", "created_at"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    op.create_table(
        "snapshot_exclusions",
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("exclusion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "owner_user_id"],
            ["project_snapshots.id", "project_snapshots.owner_user_id"],
            name="fk_snapshot_exclusions_snapshot_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["exclusion_id", "owner_user_id"],
            ["experience_exclusions.id", "experience_exclusions.owner_user_id"],
            name="fk_snapshot_exclusions_exclusion_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "exclusion_id", name="pk_snapshot_exclusions"),
    )
    op.execute(
        """
        CREATE FUNCTION validate_snapshot_exclusion_active()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            exclusion_revoked_at timestamptz;
            exclusion_scope text;
            exclusion_company_id uuid;
            exclusion_role_id uuid;
            exclusion_project_id uuid;
            snapshot_project_id uuid;
            snapshot_company_id uuid;
            snapshot_role_id uuid;
        BEGIN
            SELECT
                exclusion.revoked_at,
                exclusion.scope,
                exclusion.company_id,
                exclusion.role_id,
                exclusion.project_id,
                snapshot.project_id,
                project_version.company_id,
                role_version.role_id
            INTO
                exclusion_revoked_at,
                exclusion_scope,
                exclusion_company_id,
                exclusion_role_id,
                exclusion_project_id,
                snapshot_project_id,
                snapshot_company_id,
                snapshot_role_id
            FROM experience_exclusions AS exclusion
            JOIN project_snapshots AS snapshot
              ON snapshot.id = NEW.snapshot_id
             AND snapshot.owner_user_id = NEW.owner_user_id
            JOIN application_project_versions AS project_version
              ON project_version.id = snapshot.project_version_id
             AND project_version.project_id = snapshot.project_id
             AND project_version.owner_user_id = snapshot.owner_user_id
            LEFT JOIN role_versions AS role_version
              ON role_version.id = project_version.role_version_id
            WHERE exclusion.id = NEW.exclusion_id
              AND exclusion.owner_user_id = NEW.owner_user_id;
            IF NOT FOUND OR exclusion_revoked_at IS NOT NULL THEN
                RAISE EXCEPTION 'snapshot exclusion must reference an active owner exclusion'
                    USING ERRCODE = '23503';
            END IF;
            IF NOT (
                exclusion_scope = 'GLOBAL'
                OR (exclusion_scope = 'COMPANY' AND exclusion_company_id = snapshot_company_id)
                OR (exclusion_scope = 'ROLE' AND exclusion_role_id = snapshot_role_id)
                OR (exclusion_scope = 'PROJECT' AND exclusion_project_id = snapshot_project_id)
            ) THEN
                RAISE EXCEPTION 'snapshot exclusion scope does not match its snapshot context'
                    USING ERRCODE = '23503';
            END IF;
            RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_snapshot_exclusions_active_only
        BEFORE INSERT OR UPDATE ON snapshot_exclusions
        FOR EACH ROW EXECUTE FUNCTION validate_snapshot_exclusion_active();
        """
    )
    for table_name in ("experience_exclusions", "snapshot_exclusions"):
        _enable_owner_rls(table_name, "owner_user_id")


def downgrade() -> None:
    for table_name in ("snapshot_exclusions", "experience_exclusions"):
        op.execute(f"DROP POLICY IF EXISTS {table_name}_owner_policy ON {table_name}")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TRIGGER IF EXISTS trg_snapshot_exclusions_active_only ON snapshot_exclusions")
    op.execute("DROP FUNCTION IF EXISTS validate_snapshot_exclusion_active()")
    op.drop_table("snapshot_exclusions")
    op.drop_index("ix_experience_exclusions_owner_active", table_name="experience_exclusions")
    op.drop_index(
        "uq_experience_exclusions_active_target_scope", table_name="experience_exclusions"
    )
    op.drop_table("experience_exclusions")
    op.drop_index("ix_projection_sync_states_status_updated", table_name="projection_sync_states")
    op.drop_table("projection_sync_states")
    op.drop_constraint(
        "ck_outbox_messages_payload_max_16kib", "outbox_messages", type_="check"
    )
    op.drop_constraint(
        "ck_outbox_messages_visibility_reference_scope", "outbox_messages", type_="check"
    )
    op.create_check_constraint(
        "ck_outbox_messages_visibility_reference_scope",
        "outbox_messages",
        "(visibility_scope = 'PUBLIC' AND owner_user_id IS NULL AND job_id IS NULL "
        "AND command_id IS NULL AND execution_fence IS NULL AND owner_deletion_epoch IS NULL) "
        "OR (visibility_scope = 'PRIVATE' AND owner_user_id IS NOT NULL AND job_id IS NOT NULL "
        "AND command_id IS NOT NULL AND execution_fence IS NOT NULL "
        "AND owner_deletion_epoch IS NOT NULL)",
    )
    op.drop_constraint("ck_outbox_messages_status_allowed", "outbox_messages", type_="check")
    op.create_check_constraint(
        "ck_outbox_messages_status_allowed",
        "outbox_messages",
        "status IN ('PENDING', 'PUBLISHED', 'FAILED')",
    )


def _enable_owner_rls(table_name: str, owner_column: str) -> None:
    expression = f"{owner_column} = NULLIF(current_setting('app.current_user_id', true), '')::uuid"
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table_name}_owner_policy ON {table_name} "
        f"USING ({expression}) WITH CHECK ({expression})"
    )
