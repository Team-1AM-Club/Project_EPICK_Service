from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, text

BACKEND_ROOT = Path(__file__).parents[3]
RUNTIME_ROLE_TEMPLATE_SQL = BACKEND_ROOT / "infra" / "postgres" / "runtime_roles.sql"
RUNTIME_PRIVILEGES_SQL = BACKEND_ROOT / "infra" / "postgres" / "runtime_privileges.sql"


def _has_table_privilege(
    engine: Engine,
    role_name: str,
    table_name: str,
    privilege: str,
) -> bool:
    with engine.connect() as connection:
        return bool(
            connection.execute(
                text("SELECT has_table_privilege(:role_name, :table_name, :privilege)"),
                {
                    "role_name": role_name,
                    "table_name": f"public.{table_name}",
                    "privilege": privilege,
                },
            ).scalar_one()
        )


def _has_column_privilege(
    engine: Engine,
    role_name: str,
    table_name: str,
    column_name: str,
    privilege: str,
) -> bool:
    with engine.connect() as connection:
        return bool(
            connection.execute(
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
        )


def test_runtime_privilege_manifest_is_role_scoped_and_deny_by_default(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text(RUNTIME_ROLE_TEMPLATE_SQL.read_text(encoding="utf-8")))
        connection.execute(text(RUNTIME_PRIVILEGES_SQL.read_text(encoding="utf-8")))

        connection.execute(text("CREATE TABLE runtime_privilege_default_probe (id uuid)"))

    try:
        assert _has_table_privilege(migrated_engine, "epick_runtime", "users", "INSERT")
        assert _has_table_privilege(migrated_engine, "epick_runtime", "project_snapshots", "INSERT")
        assert _has_table_privilege(
            migrated_engine, "epick_runtime", "snapshot_episode_versions", "INSERT"
        )
        assert _has_table_privilege(
            migrated_engine, "epick_runtime", "recommendation_runs", "INSERT"
        )
        assert _has_table_privilege(
            migrated_engine, "epick_runtime", "material_selection_sets", "INSERT"
        )
        assert _has_table_privilege(
            migrated_engine, "epick_runtime", "material_selection_items", "INSERT"
        )
        assert _has_table_privilege(migrated_engine, "epick_runtime", "outbox_messages", "INSERT")
        assert _has_table_privilege(migrated_engine, "epick_runtime", "deletion_targets", "INSERT")
        assert _has_table_privilege(migrated_engine, "epick_runtime", "deletion_targets", "UPDATE")
        assert _has_table_privilege(migrated_engine, "epick_runtime", "sources", "SELECT")
        assert not _has_table_privilege(migrated_engine, "epick_runtime", "sources", "INSERT")

        assert _has_table_privilege(migrated_engine, "epick_worker", "jobs", "UPDATE")
        assert _has_table_privilege(migrated_engine, "epick_worker", "sources", "INSERT")
        assert _has_table_privilege(migrated_engine, "epick_worker", "users", "SELECT")
        assert _has_table_privilege(
            migrated_engine, "epick_worker", "job_core_decision_bindings", "SELECT"
        )
        assert _has_table_privilege(
            migrated_engine, "epick_worker", "job_core_decision_bindings", "INSERT"
        )
        assert not _has_table_privilege(
            migrated_engine, "epick_worker", "job_core_decision_bindings", "UPDATE"
        )
        assert not _has_table_privilege(
            migrated_engine, "epick_worker", "job_core_decision_bindings", "DELETE"
        )
        assert _has_column_privilege(
            migrated_engine,
            "epick_worker",
            "job_core_decision_bindings",
            "origin_producer",
            "INSERT",
        )
        assert not _has_column_privilege(
            migrated_engine,
            "epick_worker",
            "job_core_decision_bindings",
            "origin_decision_id",
            "UPDATE",
        )
        assert not _has_table_privilege(
            migrated_engine, "epick_worker", "inbox_receipts", "DELETE"
        )
        assert not _has_table_privilege(migrated_engine, "epick_worker", "users", "UPDATE")
        assert _has_column_privilege(
            migrated_engine, "epick_worker", "users", "updated_at", "UPDATE"
        )
        assert not _has_column_privilege(
            migrated_engine, "epick_worker", "users", "account_status", "UPDATE"
        )

        assert _has_column_privilege(
            migrated_engine, "epick_lookup", "job_commands", "payload", "SELECT"
        )
        assert _has_column_privilege(
            migrated_engine, "epick_lookup", "jobs", "execution_fence", "SELECT"
        )
        assert _has_column_privilege(
            migrated_engine, "epick_lookup", "jobs", "project_id", "SELECT"
        )
        assert _has_column_privilege(
            migrated_engine,
            "epick_lookup",
            "job_commands",
            "analysis_source_decision_id",
            "SELECT",
        )
        assert _has_column_privilege(
            migrated_engine,
            "epick_lookup",
            "application_projects",
            "current_version_id",
            "SELECT",
        )
        assert _has_column_privilege(
            migrated_engine,
            "epick_lookup",
            "application_project_versions",
            "company_id",
            "SELECT",
        )
        assert _has_column_privilege(
            migrated_engine,
            "epick_lookup",
            "project_questions",
            "current_version_id",
            "SELECT",
        )
        assert _has_column_privilege(
            migrated_engine,
            "epick_lookup",
            "question_versions",
            "question_id",
            "SELECT",
        )
        assert _has_column_privilege(
            migrated_engine,
            "epick_lookup",
            "job_source_links",
            "command_id",
            "SELECT",
        )
        assert _has_column_privilege(
            migrated_engine, "epick_lookup", "sources", "company_id", "SELECT"
        )
        assert _has_column_privilege(
            migrated_engine,
            "epick_lookup",
            "analysis_source_decisions",
            "decision_scope",
            "SELECT",
        )
        assert _has_column_privilege(
            migrated_engine,
            "epick_lookup",
            "job_core_decision_bindings",
            "origin_producer",
            "SELECT",
        )
        assert not _has_column_privilege(
            migrated_engine, "epick_lookup", "sources", "canonical_url", "SELECT"
        )
        assert not _has_table_privilege(migrated_engine, "epick_lookup", "jobs", "UPDATE")
        assert not _has_table_privilege(
            migrated_engine, "epick_lookup", "outbox_messages", "SELECT"
        )
        assert not _has_table_privilege(
            migrated_engine, "epick_lookup", "job_core_decision_bindings", "SELECT"
        )

        assert _has_table_privilege(migrated_engine, "epick_deleter", "deletion_requests", "UPDATE")
        assert _has_table_privilege(migrated_engine, "epick_deleter", "users", "DELETE")
        assert not _has_table_privilege(migrated_engine, "epick_deleter", "sources", "DELETE")

        assert not _has_table_privilege(
            migrated_engine,
            "epick_runtime",
            "runtime_privilege_default_probe",
            "SELECT",
        )
        assert not _has_table_privilege(
            migrated_engine,
            "epick_lookup",
            "runtime_privilege_default_probe",
            "SELECT",
        )
    finally:
        with migrated_engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS runtime_privilege_default_probe"))
