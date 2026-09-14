"""Create immutable, owner-scoped Experience repository tables.

Revision ID: 002_create_experience_repository
Revises: 001_create_identity_tables
Create Date: 2026-09-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "002_create_experience_repository"
down_revision = "001_create_identity_tables"
branch_labels = None
depends_on = None

AVAILABILITY_VALUES = "'PROVIDED', 'SKIPPED', 'NOT_APPLICABLE', 'NOT_REMEMBERED', 'NOT_PROVIDED'"
REGISTRATION_VALUES = "'DRAFT', 'COMPLETED'"
DELETION_VALUES = "'ACTIVE', 'DELETE_REQUESTED', 'DELETING', 'DELETED'"
OUTCOME_VALUES = "'SUCCEEDED', 'PARTIALLY_ACHIEVED', 'FAILED', 'IN_PROGRESS', 'NO_CLEAR_OUTCOME'"


def upgrade() -> None:
    op.create_table(
        "activities",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "registration_status",
            sa.String(length=32),
            server_default=sa.text("'DRAFT'"),
            nullable=False,
        ),
        sa.Column("usage_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "deletion_status",
            sa.String(length=32),
            server_default=sa.text("'ACTIVE'"),
            nullable=False,
        ),
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
            f"registration_status IN ({REGISTRATION_VALUES})",
            name="ck_activities_registration_status_allowed",
        ),
        sa.CheckConstraint(
            f"deletion_status IN ({DELETION_VALUES})",
            name="ck_activities_deletion_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_activities_owner_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_activities"),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_activities_id_owner_user_id"),
    )
    op.create_table(
        "episodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("activity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "registration_status",
            sa.String(length=32),
            server_default=sa.text("'DRAFT'"),
            nullable=False,
        ),
        sa.Column("usage_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "deletion_status",
            sa.String(length=32),
            server_default=sa.text("'ACTIVE'"),
            nullable=False,
        ),
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
            f"registration_status IN ({REGISTRATION_VALUES})",
            name="ck_episodes_registration_status_allowed",
        ),
        sa.CheckConstraint(
            f"deletion_status IN ({DELETION_VALUES})",
            name="ck_episodes_deletion_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["activity_id", "owner_user_id"],
            ["activities.id", "activities.owner_user_id"],
            name="fk_episodes_activity_id_owner_user_id_activities",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_episodes"),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_episodes_id_owner_user_id"),
        sa.UniqueConstraint(
            "id", "activity_id", "owner_user_id", name="uq_episodes_id_activity_id_owner_user_id"
        ),
    )
    op.create_table(
        "activity_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("activity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("organization_text", sa.Text(), nullable=True),
        sa.Column("organization_availability", sa.String(length=32), nullable=False),
        sa.Column("activity_type", sa.String(length=32), nullable=True),
        sa.Column("activity_type_availability", sa.String(length=32), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("period_precision", sa.String(length=32), nullable=True),
        sa.Column("period_availability", sa.String(length=32), nullable=False),
        sa.Column("role_text", sa.Text(), nullable=True),
        sa.Column("role_availability", sa.String(length=32), nullable=False),
        sa.Column("outcome_status", sa.String(length=32), nullable=True),
        sa.Column("outcome_text", sa.Text(), nullable=True),
        sa.Column("outcome_availability", sa.String(length=32), nullable=False),
        sa.Column("original_narrative", sa.Text(), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(organization_availability = 'PROVIDED') = (organization_text IS NOT NULL)",
            name="ck_activity_versions_organization_value_matches_availability",
        ),
        sa.CheckConstraint(
            "(activity_type_availability = 'PROVIDED') = (activity_type IS NOT NULL)",
            name="ck_activity_versions_activity_type_value_matches_availability",
        ),
        sa.CheckConstraint(
            "(role_availability = 'PROVIDED') = (role_text IS NOT NULL)",
            name="ck_activity_versions_role_value_matches_availability",
        ),
        sa.CheckConstraint(
            f"organization_availability IN ({AVAILABILITY_VALUES})",
            name="ck_activity_versions_organization_availability_allowed",
        ),
        sa.CheckConstraint(
            f"activity_type_availability IN ({AVAILABILITY_VALUES})",
            name="ck_activity_versions_activity_type_availability_allowed",
        ),
        sa.CheckConstraint(
            f"period_availability IN ({AVAILABILITY_VALUES})",
            name="ck_activity_versions_period_availability_allowed",
        ),
        sa.CheckConstraint(
            f"role_availability IN ({AVAILABILITY_VALUES})",
            name="ck_activity_versions_role_availability_allowed",
        ),
        sa.CheckConstraint(
            f"outcome_availability IN ({AVAILABILITY_VALUES})",
            name="ck_activity_versions_outcome_availability_allowed",
        ),
        sa.CheckConstraint(
            f"outcome_status IS NULL OR outcome_status IN ({OUTCOME_VALUES})",
            name="ck_activity_versions_outcome_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["activity_id", "owner_user_id"],
            ["activities.id", "activities.owner_user_id"],
            name="fk_activity_versions_activity_id_owner_user_id_activities",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_activity_versions"),
        sa.UniqueConstraint(
            "activity_id", "version_no", name="uq_activity_versions_activity_id_version_no"
        ),
        sa.UniqueConstraint("activity_id", "id", name="uq_activity_versions_activity_id_id"),
        sa.UniqueConstraint(
            "id",
            "activity_id",
            "owner_user_id",
            name="uq_activity_versions_id_activity_id_owner_user_id",
        ),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_activity_versions_id_owner_user_id"),
    )
    op.create_table(
        "episode_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("episode_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("activity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("activity_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("situation_text", sa.Text(), nullable=True),
        sa.Column("situation_availability", sa.String(length=32), nullable=False),
        sa.Column("problem_text", sa.Text(), nullable=True),
        sa.Column("problem_availability", sa.String(length=32), nullable=False),
        sa.Column("goal_text", sa.Text(), nullable=True),
        sa.Column("goal_availability", sa.String(length=32), nullable=False),
        sa.Column("actions_text", sa.Text(), nullable=True),
        sa.Column("actions_availability", sa.String(length=32), nullable=False),
        sa.Column("decisions_text", sa.Text(), nullable=True),
        sa.Column("decisions_availability", sa.String(length=32), nullable=False),
        sa.Column("decision_reasons_text", sa.Text(), nullable=True),
        sa.Column("decision_reasons_availability", sa.String(length=32), nullable=False),
        sa.Column("result_text", sa.Text(), nullable=True),
        sa.Column("result_availability", sa.String(length=32), nullable=False),
        sa.Column("learning_text", sa.Text(), nullable=True),
        sa.Column("learning_availability", sa.String(length=32), nullable=False),
        sa.Column("original_narrative", sa.Text(), nullable=True),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        *_episode_version_availability_constraints(),
        sa.ForeignKeyConstraint(
            ["episode_id", "activity_id", "owner_user_id"],
            ["episodes.id", "episodes.activity_id", "episodes.owner_user_id"],
            name="fk_episode_versions_episode_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["activity_version_id", "activity_id", "owner_user_id"],
            [
                "activity_versions.id",
                "activity_versions.activity_id",
                "activity_versions.owner_user_id",
            ],
            name="fk_episode_versions_activity_version_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_episode_versions"),
        sa.UniqueConstraint(
            "episode_id", "version_no", name="uq_episode_versions_episode_id_version_no"
        ),
        sa.UniqueConstraint("episode_id", "id", name="uq_episode_versions_episode_id_id"),
        sa.UniqueConstraint(
            "id",
            "episode_id",
            "activity_id",
            "owner_user_id",
            name="uq_episode_versions_id_episode_id_activity_id_owner_user_id",
        ),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_episode_versions_id_owner_user_id"),
    )
    op.create_table(
        "episode_version_skills",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("episode_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_name", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["episode_version_id", "owner_user_id"],
            ["episode_versions.id", "episode_versions.owner_user_id"],
            name="fk_episode_version_skills_version_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_episode_version_skills"),
        sa.UniqueConstraint(
            "episode_version_id",
            "raw_name",
            "origin",
            name="uq_episode_version_skills_episode_version_id_raw_name_origin",
        ),
    )
    op.create_table(
        "experience_field_provenance",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("activity_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("episode_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("field_name", sa.String(length=128), nullable=False),
        sa.Column(
            "origin", sa.String(length=32), server_default=sa.text("'USER_INPUT'"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(activity_version_id IS NOT NULL)::integer "
            "+ (episode_version_id IS NOT NULL)::integer = 1",
            name="ck_experience_field_provenance_exactly_one_version",
        ),
        sa.CheckConstraint(
            "origin = 'USER_INPUT'", name="ck_experience_field_provenance_origin_user_input_only"
        ),
        sa.ForeignKeyConstraint(
            ["activity_version_id", "owner_user_id"],
            ["activity_versions.id", "activity_versions.owner_user_id"],
            name="fk_experience_field_provenance_activity_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["episode_version_id", "owner_user_id"],
            ["episode_versions.id", "episode_versions.owner_user_id"],
            name="fk_experience_field_provenance_episode_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_experience_field_provenance"),
    )
    op.create_index(
        "uq_experience_field_provenance_activity_version_id_field_name",
        "experience_field_provenance",
        ["activity_version_id", "field_name"],
        unique=True,
        postgresql_where=sa.text("activity_version_id IS NOT NULL"),
    )
    op.create_index(
        "uq_experience_field_provenance_episode_version_id_field_name",
        "experience_field_provenance",
        ["episode_version_id", "field_name"],
        unique=True,
        postgresql_where=sa.text("episode_version_id IS NOT NULL"),
    )
    op.create_foreign_key(
        "fk_activities_current_version_activity_versions",
        "activities",
        "activity_versions",
        ["id", "current_version_id"],
        ["activity_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_episodes_current_version_episode_versions",
        "episodes",
        "episode_versions",
        ["id", "current_version_id"],
        ["episode_id", "id"],
        ondelete="RESTRICT",
    )
    for table_name in (
        "activities",
        "episodes",
        "activity_versions",
        "episode_versions",
        "episode_version_skills",
        "experience_field_provenance",
    ):
        _enable_owner_rls(table_name, "owner_user_id")


def downgrade() -> None:
    op.drop_constraint(
        "fk_episodes_current_version_episode_versions", "episodes", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_activities_current_version_activity_versions", "activities", type_="foreignkey"
    )
    op.drop_index(
        "uq_experience_field_provenance_episode_version_id_field_name",
        table_name="experience_field_provenance",
    )
    op.drop_index(
        "uq_experience_field_provenance_activity_version_id_field_name",
        table_name="experience_field_provenance",
    )
    for table_name in (
        "experience_field_provenance",
        "episode_version_skills",
        "episode_versions",
        "activity_versions",
        "episodes",
        "activities",
    ):
        op.drop_table(table_name)


def _episode_version_availability_constraints() -> tuple[sa.CheckConstraint, ...]:
    constraints: list[sa.CheckConstraint] = []
    for name in (
        "situation",
        "problem",
        "goal",
        "actions",
        "decisions",
        "decision_reasons",
        "result",
        "learning",
    ):
        constraints.extend(
            (
                sa.CheckConstraint(
                    f"{name}_availability IN ({AVAILABILITY_VALUES})",
                    name=f"ck_episode_versions_{name}_availability_allowed",
                ),
                sa.CheckConstraint(
                    f"({name}_availability = 'PROVIDED') = ({name}_text IS NOT NULL)",
                    name=f"ck_episode_versions_{name}_value_matches_availability",
                ),
            )
        )
    return tuple(constraints)


def _enable_owner_rls(table_name: str, owner_column: str) -> None:
    policy_name = f"{table_name}_owner_policy"
    expression = f"{owner_column} = NULLIF(current_setting('app.current_user_id', true), '')::uuid"
    policy_statement = (
        f"CREATE POLICY {policy_name} ON {table_name} "
        f"USING ({expression}) WITH CHECK ({expression})"
    )
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(policy_statement)
