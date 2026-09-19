from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

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


def _has_rls_policy(
    engine: Engine,
    *,
    table_name: str,
    policy_name: str,
    command: str,
) -> bool:
    with engine.connect() as connection:
        return bool(
            connection.execute(
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
        )


def test_runtime_privilege_manifest_is_role_scoped_and_deny_by_default(
    migrated_engine: Engine,
) -> None:
    owner_id = uuid4()
    company_id = uuid4()
    project_id = uuid4()
    project_version_id = uuid4()
    question_id = uuid4()
    question_version_id = uuid4()
    with migrated_engine.begin() as connection:
        connection.execute(text(RUNTIME_ROLE_TEMPLATE_SQL.read_text(encoding="utf-8")))
        connection.execute(text(RUNTIME_PRIVILEGES_SQL.read_text(encoding="utf-8")))

        connection.execute(text("CREATE TABLE runtime_privilege_default_probe (id uuid)"))
        connection.execute(
            text(
                "INSERT INTO users (id, display_name, locale, timezone) "
                "VALUES (:id, 'W4 RLS probe', 'ko-KR', 'Asia/Seoul')"
            ),
            {"id": owner_id},
        )
        connection.execute(
            text(
                "INSERT INTO companies (id, legal_name, display_name) "
                "VALUES (:id, 'W4 RLS probe', 'W4 RLS probe')"
            ),
            {"id": company_id},
        )
        connection.execute(
            text(
                "INSERT INTO application_projects (id, owner_user_id) "
                "VALUES (:id, :owner_id)"
            ),
            {"id": project_id, "owner_id": owner_id},
        )
        connection.execute(
            text(
                "INSERT INTO application_project_versions "
                "(id, project_id, owner_user_id, version_no, company_id, title, role_name) "
                "VALUES (:id, :project_id, :owner_id, 1, :company_id, "
                "'W4 RLS probe', 'Synthetic')"
            ),
            {
                "id": project_version_id,
                "project_id": project_id,
                "owner_id": owner_id,
                "company_id": company_id,
            },
        )
        connection.execute(
            text(
                "UPDATE application_projects SET current_version_id = :version_id "
                "WHERE id = :project_id"
            ),
            {"version_id": project_version_id, "project_id": project_id},
        )
        connection.execute(
            text(
                "INSERT INTO project_questions "
                "(id, owner_user_id, project_id, display_order) "
                "VALUES (:id, :owner_id, :project_id, 0)"
            ),
            {"id": question_id, "owner_id": owner_id, "project_id": project_id},
        )
        connection.execute(
            text(
                "INSERT INTO question_versions "
                "(id, question_id, project_id, owner_user_id, version_no, prompt, source) "
                "VALUES (:id, :question_id, :project_id, :owner_id, 1, "
                "'W4 RLS probe', 'USER')"
            ),
            {
                "id": question_version_id,
                "question_id": question_id,
                "project_id": project_id,
                "owner_id": owner_id,
            },
        )
        connection.execute(
            text(
                "UPDATE project_questions SET current_version_id = :version_id "
                "WHERE id = :question_id"
            ),
            {"version_id": question_version_id, "question_id": question_id},
        )

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
        for table_name, lock_column, authoritative_column in (
            ("application_projects", "updated_at", "current_version_id"),
            ("application_project_versions", "created_at", "company_id"),
            ("project_questions", "updated_at", "current_version_id"),
            ("question_versions", "created_at", "prompt"),
        ):
            assert _has_column_privilege(
                migrated_engine,
                "epick_worker",
                table_name,
                lock_column,
                "UPDATE",
            )
            assert not _has_column_privilege(
                migrated_engine,
                "epick_worker",
                table_name,
                authoritative_column,
                "UPDATE",
            )
            assert _has_rls_policy(
                migrated_engine,
                table_name=table_name,
                policy_name=f"{table_name}_worker_question_core_read_policy",
                command="SELECT",
            )
            assert _has_rls_policy(
                migrated_engine,
                table_name=table_name,
                policy_name=f"{table_name}_worker_question_core_lock_policy",
                command="UPDATE",
            )

        with migrated_engine.begin() as worker_connection:
            worker_connection.execute(text("SET LOCAL ROLE epick_worker"))
            for table_name in (
                "users",
                "jobs",
                "inbox_receipts",
                "application_projects",
                "application_project_versions",
                "project_questions",
                "question_versions",
                "job_source_links",
                "sources",
                "job_required_actions",
            ):
                worker_connection.execute(
                    text(f"SELECT * FROM {table_name} LIMIT 1 FOR UPDATE")
                )
            for table_name, row_id in (
                ("application_projects", project_id),
                ("application_project_versions", project_version_id),
                ("project_questions", question_id),
                ("question_versions", question_version_id),
            ):
                visible_id = worker_connection.execute(
                    text(f"SELECT id FROM {table_name} WHERE id = :id FOR UPDATE"),
                    {"id": row_id},
                ).scalar_one_or_none()
                assert visible_id == row_id

        with pytest.raises(DBAPIError):
            with migrated_engine.begin() as worker_connection:
                worker_connection.execute(text("SET LOCAL ROLE epick_worker"))
                worker_connection.execute(
                    text(
                        "UPDATE application_projects SET updated_at = updated_at "
                        "WHERE id = :id"
                    ),
                    {"id": project_id},
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
            connection.execute(
                text(
                    "UPDATE project_questions SET current_version_id = NULL "
                    "WHERE id = :id"
                ),
                {"id": question_id},
            )
            connection.execute(
                text("DELETE FROM question_versions WHERE id = :id"),
                {"id": question_version_id},
            )
            connection.execute(
                text("DELETE FROM project_questions WHERE id = :id"),
                {"id": question_id},
            )
            connection.execute(
                text(
                    "UPDATE application_projects SET current_version_id = NULL "
                    "WHERE id = :id"
                ),
                {"id": project_id},
            )
            connection.execute(
                text("DELETE FROM application_project_versions WHERE id = :id"),
                {"id": project_version_id},
            )
            connection.execute(
                text("DELETE FROM application_projects WHERE id = :id"),
                {"id": project_id},
            )
            connection.execute(
                text("DELETE FROM companies WHERE id = :id"),
                {"id": company_id},
            )
            connection.execute(
                text("DELETE FROM users WHERE id = :id"),
                {"id": owner_id},
            )
