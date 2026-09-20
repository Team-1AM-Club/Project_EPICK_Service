"""Read-only verification for EPICK's explicit runtime DML manifest."""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, text

BACKEND_ROOT = Path(__file__).parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402

PRIVILEGE_EXPECTATIONS = (
    ("epick_runtime", "users", "INSERT", True),
    ("epick_runtime", "project_snapshots", "INSERT", True),
    ("epick_runtime", "snapshot_episode_versions", "INSERT", True),
    ("epick_runtime", "recommendation_runs", "INSERT", True),
    ("epick_runtime", "recommendation_execution_bindings", "INSERT", True),
    ("epick_runtime", "recommendation_execution_episodes", "INSERT", True),
    ("epick_runtime", "recommendation_publications", "INSERT", False),
    ("epick_runtime", "recommendation_source_dependencies", "INSERT", False),
    ("epick_runtime", "material_selection_sets", "INSERT", True),
    ("epick_runtime", "material_selection_items", "INSERT", True),
    ("epick_runtime", "outbox_messages", "INSERT", True),
    ("epick_runtime", "deletion_targets", "INSERT", True),
    ("epick_runtime", "deletion_targets", "UPDATE", True),
    ("epick_runtime", "sources", "INSERT", False),
    ("epick_worker", "jobs", "UPDATE", True),
    ("epick_worker", "sources", "INSERT", True),
    ("epick_worker", "users", "SELECT", True),
    ("epick_worker", "users", "UPDATE", False),
    ("epick_worker", "job_core_decision_bindings", "SELECT", True),
    ("epick_worker", "job_core_decision_bindings", "INSERT", True),
    ("epick_worker", "job_core_decision_bindings", "UPDATE", False),
    ("epick_worker", "job_core_decision_bindings", "DELETE", False),
    ("epick_runtime", "job_core_decision_bindings", "SELECT", True),
    ("epick_runtime", "job_core_decision_bindings", "UPDATE", False),
    ("epick_lookup", "job_core_decision_bindings", "SELECT", False),
    ("epick_worker", "w2_staged_results", "INSERT", True),
    ("epick_worker", "w2_staged_results", "UPDATE", True),
    ("epick_worker", "recommendation_execution_bindings", "UPDATE", True),
    ("epick_worker", "recommendation_publications", "INSERT", True),
    ("epick_worker", "recommendation_source_dependencies", "INSERT", True),
    ("epick_worker", "recommendation_candidates", "INSERT", True),
    ("epick_lookup", "jobs", "UPDATE", False),
    ("epick_lookup", "outbox_messages", "SELECT", False),
    ("epick_w3_authority", "users", "UPDATE", False),
    ("epick_w3_authority", "jobs", "UPDATE", False),
    ("epick_w3_authority", "outbox_messages", "SELECT", False),
    ("epick_deleter", "deletion_requests", "UPDATE", True),
    ("epick_deleter", "users", "DELETE", True),
    ("epick_deleter", "sources", "DELETE", False),
    ("epick_deleter", "w2_staged_results", "UPDATE", True),
    ("epick_runtime", "w2_staged_results", "UPDATE", False),
)

COLUMN_PRIVILEGE_EXPECTATIONS = (
    ("epick_worker", "users", "updated_at", "UPDATE", True),
    ("epick_worker", "users", "account_status", "UPDATE", False),
    ("epick_worker", "application_projects", "updated_at", "UPDATE", True),
    ("epick_worker", "application_projects", "current_version_id", "UPDATE", False),
    ("epick_worker", "application_project_versions", "created_at", "UPDATE", True),
    ("epick_worker", "application_project_versions", "company_id", "UPDATE", False),
    ("epick_worker", "project_questions", "updated_at", "UPDATE", True),
    ("epick_worker", "project_questions", "current_version_id", "UPDATE", False),
    ("epick_worker", "question_versions", "created_at", "UPDATE", True),
    ("epick_worker", "question_versions", "prompt", "UPDATE", False),
    ("epick_lookup", "users", "deletion_epoch", "SELECT", True),
    ("epick_lookup", "jobs", "execution_fence", "SELECT", True),
    ("epick_lookup", "jobs", "project_id", "SELECT", True),
    ("epick_lookup", "job_commands", "payload", "SELECT", True),
    ("epick_lookup", "job_commands", "analysis_source_decision_id", "SELECT", True),
    ("epick_lookup", "application_projects", "current_version_id", "SELECT", True),
    ("epick_lookup", "application_project_versions", "company_id", "SELECT", True),
    ("epick_lookup", "project_questions", "current_version_id", "SELECT", True),
    ("epick_lookup", "question_versions", "question_id", "SELECT", True),
    ("epick_lookup", "job_source_links", "command_id", "SELECT", True),
    ("epick_lookup", "sources", "company_id", "SELECT", True),
    ("epick_lookup", "analysis_source_decisions", "decision_scope", "SELECT", True),
    ("epick_lookup", "job_core_decision_bindings", "origin_producer", "SELECT", True),
    ("epick_lookup", "sources", "canonical_url", "SELECT", False),
    ("epick_w3_authority", "users", "id", "SELECT", True),
    ("epick_w3_authority", "users", "account_status", "SELECT", True),
    ("epick_w3_authority", "users", "deletion_epoch", "SELECT", True),
    ("epick_w3_authority", "users", "email", "SELECT", False),
    ("epick_w3_authority", "users", "display_name", "SELECT", False),
    ("epick_w3_authority", "jobs", "owner_user_id", "SELECT", True),
    ("epick_w3_authority", "jobs", "analysis_input_version", "SELECT", True),
    ("epick_w3_authority", "jobs", "safe_failure_message", "SELECT", False),
    ("epick_w3_authority", "job_source_links", "source_id", "SELECT", True),
    ("epick_w3_authority", "job_source_links", "command_id", "SELECT", False),
    ("epick_w3_authority", "sources", "company_id", "SELECT", True),
    ("epick_w3_authority", "sources", "canonical_url", "SELECT", False),
    ("epick_worker", "job_core_decision_bindings", "origin_producer", "INSERT", True),
    ("epick_worker", "job_core_decision_bindings", "origin_decision_id", "UPDATE", False),
)

RLS_POLICY_EXPECTATIONS = tuple(
    (table_name, f"{table_name}_worker_question_core_read_policy", "SELECT")
    for table_name in (
        "application_projects",
        "application_project_versions",
        "project_questions",
        "question_versions",
    )
) + tuple(
    (table_name, f"{table_name}_worker_question_core_lock_policy", "UPDATE")
    for table_name in (
        "application_projects",
        "application_project_versions",
        "project_questions",
        "question_versions",
    )
)

W3_AUTHORITY_RLS_POLICY_EXPECTATIONS = tuple(
    (table_name, f"{table_name}_w3_authority_read_policy", "SELECT")
    for table_name in ("users", "jobs", "job_source_links", "sources")
)


def main() -> None:
    migration_url = settings.migration_database_url
    if not migration_url:
        raise SystemExit("MIGRATION_DATABASE_URL must be set for the migration principal")

    engine = create_engine(migration_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            for role_name, table_name, privilege, expected in PRIVILEGE_EXPECTATIONS:
                actual = connection.execute(
                    text("SELECT has_table_privilege(:role_name, :table_name, :privilege)"),
                    {
                        "role_name": role_name,
                        "table_name": f"public.{table_name}",
                        "privilege": privilege,
                    },
                ).scalar_one()
                if actual is not expected:
                    expectation = "present" if expected else "absent"
                    raise SystemExit(
                        f"runtime privilege mismatch: {role_name} {privilege} "
                        f"on {table_name} must be {expectation}"
                    )
            for (
                role_name,
                table_name,
                column_name,
                privilege,
                expected,
            ) in COLUMN_PRIVILEGE_EXPECTATIONS:
                actual = connection.execute(
                    text(
                        "SELECT has_column_privilege("
                        ":role_name, :table_name, :column_name, :privilege)"
                    ),
                    {
                        "role_name": role_name,
                        "table_name": f"public.{table_name}",
                        "column_name": column_name,
                        "privilege": privilege,
                    },
                ).scalar_one()
                if actual is not expected:
                    expectation = "present" if expected else "absent"
                    raise SystemExit(
                        f"runtime column privilege mismatch: {role_name} {privilege} "
                        f"on {table_name}.{column_name} must be {expectation}"
                    )
            for table_name, policy_name, command in RLS_POLICY_EXPECTATIONS:
                present = connection.execute(
                    text(
                        "SELECT EXISTS ("
                        "SELECT 1 FROM pg_policies "
                        "WHERE schemaname = 'public' "
                        "AND tablename = :table_name "
                        "AND policyname = :policy_name "
                        "AND cmd = :command "
                        "AND :role_name = ANY(roles))"
                    ),
                    {
                        "table_name": table_name,
                        "policy_name": policy_name,
                        "command": command,
                        "role_name": "epick_worker",
                    },
                ).scalar_one()
                if not present:
                    raise SystemExit(
                        "runtime RLS policy mismatch: "
                        f"{policy_name} must grant epick_worker {command} visibility "
                        f"on {table_name}"
                    )
            for table_name, policy_name, command in W3_AUTHORITY_RLS_POLICY_EXPECTATIONS:
                present = connection.execute(
                    text(
                        "SELECT EXISTS ("
                        "SELECT 1 FROM pg_policies "
                        "WHERE schemaname = 'public' "
                        "AND tablename = :table_name "
                        "AND policyname = :policy_name "
                        "AND cmd = :command "
                        "AND :role_name = ANY(roles))"
                    ),
                    {
                        "table_name": table_name,
                        "policy_name": policy_name,
                        "command": command,
                        "role_name": "epick_w3_authority",
                    },
                ).scalar_one()
                if not present:
                    raise SystemExit(
                        "runtime RLS policy mismatch: "
                        f"{policy_name} must grant epick_w3_authority {command} visibility "
                        f"on {table_name}"
                    )
    finally:
        engine.dispose()

    print("PostgreSQL runtime privilege preflight passed")


if __name__ == "__main__":
    main()
