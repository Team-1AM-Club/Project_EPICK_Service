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
    ("epick_worker", "w2_staged_results", "INSERT", True),
    ("epick_worker", "w2_staged_results", "UPDATE", True),
    ("epick_lookup", "jobs", "UPDATE", False),
    ("epick_lookup", "outbox_messages", "SELECT", False),
    ("epick_deleter", "deletion_requests", "UPDATE", True),
    ("epick_deleter", "users", "DELETE", True),
    ("epick_deleter", "sources", "DELETE", False),
    ("epick_deleter", "w2_staged_results", "UPDATE", True),
    ("epick_runtime", "w2_staged_results", "UPDATE", False),
)

COLUMN_PRIVILEGE_EXPECTATIONS = (
    ("epick_worker", "users", "updated_at", "UPDATE", True),
    ("epick_worker", "users", "account_status", "UPDATE", False),
    ("epick_lookup", "users", "deletion_epoch", "SELECT", True),
    ("epick_lookup", "jobs", "execution_fence", "SELECT", True),
    ("epick_lookup", "job_commands", "payload", "SELECT", True),
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
    finally:
        engine.dispose()

    print("PostgreSQL runtime privilege preflight passed")


if __name__ == "__main__":
    main()
