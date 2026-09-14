"""add immutable snapshots, recommendation results, and material selections

Revision ID: 005_snapshot_recommendation
Revises: 004_app_workspace_jobs
Create Date: 2026-09-14
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "005_snapshot_recommendation"
down_revision = "004_app_workspace_jobs"
branch_labels = None
depends_on = None

SNAPSHOT_STATUS_VALUES = "'CREATING', 'READY', 'STALE', 'FAILED'"
RUN_STATUS_VALUES = "'PENDING', 'RUNNING', 'SUCCEEDED', 'LIMITED', 'FAILED', 'CANCELLED'"
RUN_RESULT_STATUS_VALUES = "'PENDING', 'READY', 'LIMITED', 'FAILED'"
CANDIDATE_MATCH_STATUS_VALUES = (
    "'DIRECT_MATCH', 'PARTIAL_RELEVANCE', 'NEEDS_VERIFICATION', 'NO_RELEVANT_EVIDENCE'"
)
CANDIDATE_VALIDATION_STATUS_VALUES = "'PENDING', 'PASSED', 'LIMITED', 'FAILED'"


def upgrade() -> None:
    op.add_column(
        "application_projects",
        sa.Column("active_snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "job_input_refs",
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.drop_constraint("ck_job_input_refs_exactly_one_input", "job_input_refs", type_="check")
    op.create_check_constraint(
        "ck_job_input_refs_exactly_one_input",
        "job_input_refs",
        "(project_version_id IS NOT NULL)::integer + "
        "(question_version_id IS NOT NULL)::integer + "
        "(episode_version_id IS NOT NULL)::integer + "
        "(snapshot_id IS NOT NULL)::integer + "
        "((policy_name IS NOT NULL AND policy_version IS NOT NULL)::integer) = 1 "
        "AND (policy_name IS NULL) = (policy_version IS NULL)",
    )
    op.create_index(
        "uq_job_input_refs_job_snapshot",
        "job_input_refs",
        ["job_id", "snapshot_id"],
        unique=True,
        postgresql_where=sa.text("snapshot_id IS NOT NULL"),
    )
    op.create_table(
        "project_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_no", sa.Integer(), nullable=False),
        sa.Column("recommendation_policy_version", sa.String(length=64), nullable=True),
        sa.Column(
            "status", sa.String(length=32), server_default=sa.text("'CREATING'"), nullable=False
        ),
        sa.Column(
            "limitations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("snapshot_no >= 1", name="ck_project_snapshots_snapshot_no_positive"),
        sa.CheckConstraint(
            f"status IN ({SNAPSHOT_STATUS_VALUES})",
            name="ck_project_snapshots_status_allowed",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_project_snapshots_owner_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "owner_user_id"],
            ["application_projects.id", "application_projects.owner_user_id"],
            name="fk_project_snapshots_project_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_version_id", "project_id", "owner_user_id"],
            [
                "application_project_versions.id",
                "application_project_versions.project_id",
                "application_project_versions.owner_user_id",
            ],
            name="fk_project_snapshots_project_version_project_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_project_snapshots"),
        sa.UniqueConstraint(
            "project_id", "snapshot_no", name="uq_project_snapshots_project_id_snapshot_no"
        ),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_project_snapshots_id_owner_user_id"),
        sa.UniqueConstraint(
            "id",
            "project_id",
            "owner_user_id",
            name="uq_project_snapshots_id_project_id_owner_user_id",
        ),
    )
    op.create_table(
        "snapshot_episode_versions",
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("episode_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "owner_user_id"],
            ["project_snapshots.id", "project_snapshots.owner_user_id"],
            name="fk_snapshot_episode_versions_snapshot_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["episode_version_id", "owner_user_id"],
            ["episode_versions.id", "episode_versions.owner_user_id"],
            name="fk_snapshot_episode_versions_episode_version_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "snapshot_id", "episode_version_id", name="pk_snapshot_episode_versions"
        ),
        sa.UniqueConstraint(
            "snapshot_id",
            "episode_version_id",
            "owner_user_id",
            name="uq_snapshot_episode_versions_snapshot_episode_owner",
        ),
    )
    op.create_table(
        "recommendation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("analysis_policy_version", sa.String(length=64), nullable=False),
        sa.Column("analysis_input_version", sa.String(length=64), nullable=True),
        sa.Column(
            "result_status",
            sa.String(length=32),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("restriction_reason", sa.String(length=64), nullable=True),
        sa.Column(
            "status", sa.String(length=32), server_default=sa.text("'PENDING'"), nullable=False
        ),
        sa.Column("requested_candidate_limit", sa.Integer(), nullable=False),
        sa.Column(
            "limited_analysis", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "limitations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"result_status IN ({RUN_RESULT_STATUS_VALUES})",
            name="ck_recommendation_runs_result_status_allowed",
        ),
        sa.CheckConstraint(
            f"status IN ({RUN_STATUS_VALUES})", name="ck_recommendation_runs_status_allowed"
        ),
        sa.CheckConstraint(
            "requested_candidate_limit > 0",
            name="ck_recommendation_runs_requested_candidate_limit_positive",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_recommendation_runs_owner_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "owner_user_id"],
            ["application_projects.id", "application_projects.owner_user_id"],
            name="fk_recommendation_runs_project_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_id", "project_id", "owner_user_id"],
            [
                "project_questions.id",
                "project_questions.project_id",
                "project_questions.owner_user_id",
            ],
            name="fk_recommendation_runs_question_project_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["question_version_id", "question_id", "owner_user_id"],
            [
                "question_versions.id",
                "question_versions.question_id",
                "question_versions.owner_user_id",
            ],
            name="fk_recommendation_runs_question_version_question_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "project_id", "owner_user_id"],
            [
                "project_snapshots.id",
                "project_snapshots.project_id",
                "project_snapshots.owner_user_id",
            ],
            name="fk_recommendation_runs_snapshot_project_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "owner_user_id"],
            ["jobs.id", "jobs.owner_user_id"],
            name="fk_recommendation_runs_job_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recommendation_runs"),
        sa.UniqueConstraint(
            "id",
            "question_id",
            "snapshot_id",
            "owner_user_id",
            name="uq_recommendation_runs_id_question_snapshot_owner",
        ),
        sa.UniqueConstraint(
            "id", "question_id", "owner_user_id", name="uq_recommendation_runs_id_question_owner"
        ),
    )
    op.create_table(
        "recommendation_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_no", sa.Integer(), nullable=False),
        sa.Column("episode_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("match_status", sa.String(length=32), nullable=False),
        sa.Column("short_reason", sa.Text(), nullable=False),
        sa.Column("strength_summary", sa.Text(), nullable=True),
        sa.Column("limitation_summary", sa.Text(), nullable=True),
        sa.Column("internal_rank", sa.Integer(), nullable=True),
        sa.Column(
            "validation_status",
            sa.String(length=32),
            server_default=sa.text("'PENDING'"),
            nullable=False,
        ),
        sa.Column("result_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "candidate_no >= 1", name="ck_recommendation_candidates_candidate_no_positive"
        ),
        sa.CheckConstraint(
            f"match_status IN ({CANDIDATE_MATCH_STATUS_VALUES})",
            name="ck_recommendation_candidates_match_status_allowed",
        ),
        sa.CheckConstraint(
            f"validation_status IN ({CANDIDATE_VALIDATION_STATUS_VALUES})",
            name="ck_recommendation_candidates_validation_status_allowed",
        ),
        sa.CheckConstraint(
            "internal_rank IS NULL OR internal_rank >= 1",
            name="ck_recommendation_candidates_internal_rank_positive",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "question_id", "snapshot_id", "owner_user_id"],
            [
                "recommendation_runs.id",
                "recommendation_runs.question_id",
                "recommendation_runs.snapshot_id",
                "recommendation_runs.owner_user_id",
            ],
            name="fk_recommendation_candidates_run_question_snapshot_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "episode_version_id", "owner_user_id"],
            [
                "snapshot_episode_versions.snapshot_id",
                "snapshot_episode_versions.episode_version_id",
                "snapshot_episode_versions.owner_user_id",
            ],
            name="fk_recommendation_candidates_snapshot_episode_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recommendation_candidates"),
        sa.UniqueConstraint(
            "run_id", "candidate_no", name="uq_recommendation_candidates_run_id_candidate_no"
        ),
        sa.UniqueConstraint(
            "id",
            "question_id",
            "owner_user_id",
            name="uq_recommendation_candidates_id_question_owner",
        ),
        sa.UniqueConstraint(
            "id",
            "run_id",
            "question_id",
            "owner_user_id",
            name="uq_recommendation_candidates_id_run_question_owner",
        ),
    )
    op.create_table(
        "material_selection_sets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "selected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_material_selection_sets_owner_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "question_id", "owner_user_id"],
            [
                "recommendation_runs.id",
                "recommendation_runs.question_id",
                "recommendation_runs.owner_user_id",
            ],
            name="fk_material_selection_sets_run_question_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_material_selection_sets"),
        sa.UniqueConstraint(
            "id",
            "question_id",
            "run_id",
            "owner_user_id",
            name="uq_material_selection_sets_id_question_run_owner",
        ),
    )
    op.create_index(
        "uq_material_selection_sets_current_question",
        "material_selection_sets",
        ["question_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_table(
        "material_selection_items",
        sa.Column("selection_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("selection_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "selection_order >= 1", name="ck_material_selection_items_selection_order_positive"
        ),
        sa.ForeignKeyConstraint(
            ["selection_set_id", "question_id", "run_id", "owner_user_id"],
            [
                "material_selection_sets.id",
                "material_selection_sets.question_id",
                "material_selection_sets.run_id",
                "material_selection_sets.owner_user_id",
            ],
            name="fk_material_selection_items_set_question_run_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id", "run_id", "question_id", "owner_user_id"],
            [
                "recommendation_candidates.id",
                "recommendation_candidates.run_id",
                "recommendation_candidates.question_id",
                "recommendation_candidates.owner_user_id",
            ],
            name="fk_material_selection_items_candidate_run_question_owner_scope",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "selection_set_id", "candidate_id", name="pk_material_selection_items"
        ),
        sa.UniqueConstraint(
            "selection_set_id",
            "selection_order",
            name="uq_material_selection_items_set_selection_order",
        ),
    )
    op.create_foreign_key(
        "fk_application_projects_active_snapshot_scope",
        "application_projects",
        "project_snapshots",
        ["active_snapshot_id", "id", "owner_user_id"],
        ["id", "project_id", "owner_user_id"],
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_job_input_refs_snapshot_owner_scope",
        "job_input_refs",
        "project_snapshots",
        ["snapshot_id", "owner_user_id"],
        ["id", "owner_user_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_recommendation_candidates_run_internal_rank",
        "recommendation_candidates",
        ["run_id", sa.text("internal_rank ASC NULLS LAST")],
    )
    for table_name in (
        "project_snapshots",
        "snapshot_episode_versions",
        "recommendation_runs",
        "recommendation_candidates",
        "material_selection_sets",
        "material_selection_items",
    ):
        _enable_owner_rls(table_name, "owner_user_id")


def downgrade() -> None:
    op.drop_constraint(
        "fk_job_input_refs_snapshot_owner_scope", "job_input_refs", type_="foreignkey"
    )
    op.drop_constraint("ck_job_input_refs_exactly_one_input", "job_input_refs", type_="check")
    op.drop_index("uq_job_input_refs_job_snapshot", table_name="job_input_refs")
    op.drop_column("job_input_refs", "snapshot_id")
    op.create_check_constraint(
        "ck_job_input_refs_exactly_one_input",
        "job_input_refs",
        "(project_version_id IS NOT NULL)::integer + "
        "(question_version_id IS NOT NULL)::integer + "
        "(episode_version_id IS NOT NULL)::integer + "
        "((policy_name IS NOT NULL AND policy_version IS NOT NULL)::integer) = 1 "
        "AND (policy_name IS NULL) = (policy_version IS NULL)",
    )
    op.drop_constraint(
        "fk_application_projects_active_snapshot_scope",
        "application_projects",
        type_="foreignkey",
    )
    op.drop_column("application_projects", "active_snapshot_id")
    op.drop_table("material_selection_items")
    op.drop_index(
        "uq_material_selection_sets_current_question", table_name="material_selection_sets"
    )
    op.drop_table("material_selection_sets")
    op.drop_index(
        "ix_recommendation_candidates_run_internal_rank", table_name="recommendation_candidates"
    )
    op.drop_table("recommendation_candidates")
    op.drop_table("recommendation_runs")
    op.drop_table("snapshot_episode_versions")
    op.drop_table("project_snapshots")


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
