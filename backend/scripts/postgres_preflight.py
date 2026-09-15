"""Read-only production preflight for an EPICK PostgreSQL migration release.

This command intentionally never prints a connection URL, applies a migration,
or changes a database role.  Run it with the dedicated migration principal
before and after a manually approved ``alembic upgrade`` invocation.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

BACKEND_ROOT = Path(__file__).parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402

ROLE_NAMES = (
    "epick_migrator",
    "epick_runtime",
    "epick_worker",
    "epick_deleter",
)
RUNTIME_ROLE_NAMES = ROLE_NAMES[1:]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backup-confirmed-at",
        required=True,
        help="UTC ISO-8601 timestamp from the operator's verified RDS backup evidence.",
    )
    parser.add_argument(
        "--require-head",
        action="store_true",
        help="Fail unless the current Alembic revision is the repository head.",
    )
    return parser.parse_args()


def _require_backup_evidence(value: str) -> None:
    normalized = value.removesuffix("Z").replace("Z", "+00:00")
    try:
        datetime.fromisoformat(normalized)
    except ValueError as error:
        raise SystemExit("--backup-confirmed-at must be an ISO-8601 timestamp") from error


def _target_revision() -> str:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()
    if head is None:
        raise SystemExit("Alembic has no single migration head")
    return head


def _read_current_revision(connection: object) -> str | None:
    version_table_exists = connection.execute(
        text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
    ).scalar_one()
    if not version_table_exists:
        return None
    return connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()


def _verify_roles(connection: object) -> None:
    role_rows = connection.execute(
        text(
            "SELECT rolname, rolsuper, rolbypassrls, rolcanlogin "
            "FROM pg_roles WHERE rolname = ANY(:role_names)"
        ),
        {"role_names": list(ROLE_NAMES)},
    ).mappings()
    roles = {row["rolname"]: row for row in role_rows}
    missing = sorted(set(ROLE_NAMES) - roles.keys())
    if missing:
        raise SystemExit(f"missing required PostgreSQL group roles: {', '.join(missing)}")

    invalid_roles = [
        role_name
        for role_name, role in roles.items()
        if role["rolsuper"] or role["rolbypassrls"] or role["rolcanlogin"]
    ]
    if invalid_roles:
        raise SystemExit(
            "group roles must be NOLOGIN, NOSUPERUSER, and NOBYPASSRLS: "
            + ", ".join(sorted(invalid_roles))
        )

    migrator_can_create = connection.execute(
        text("SELECT has_schema_privilege('epick_migrator', 'public', 'CREATE')")
    ).scalar_one()
    if not migrator_can_create:
        raise SystemExit("epick_migrator is missing CREATE on the public schema")

    runtime_roles_with_create = [
        role_name
        for role_name in RUNTIME_ROLE_NAMES
        if connection.execute(
            text("SELECT has_schema_privilege(:role_name, 'public', 'CREATE')"),
            {"role_name": role_name},
        ).scalar_one()
    ]
    if runtime_roles_with_create:
        raise SystemExit(
            "runtime group roles must not have CREATE on the public schema: "
            + ", ".join(runtime_roles_with_create)
        )


def main() -> None:
    args = _parse_args()
    _require_backup_evidence(args.backup_confirmed_at)
    migration_url = settings.migration_database_url
    if not migration_url:
        raise SystemExit("MIGRATION_DATABASE_URL must be set for the migration principal")

    target_revision = _target_revision()
    engine = create_engine(migration_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            server = (
                connection.execute(
                    text(
                        "SELECT current_database() AS database_name, "
                        "current_user AS database_user, "
                        "current_setting('server_version_num')::integer AS server_version_num"
                    )
                )
                .mappings()
                .one()
            )
            current_revision = _read_current_revision(connection)
            _verify_roles(connection)
    finally:
        engine.dispose()

    if args.require_head and current_revision != target_revision:
        raise SystemExit(
            "database revision is not at the repository head: "
            f"current={current_revision or 'base'}, target={target_revision}"
        )

    print("PostgreSQL migration preflight passed")
    print(f"database={server['database_name']}")
    print(f"migration_principal={server['database_user']}")
    print(f"server_version_num={server['server_version_num']}")
    print(f"current_revision={current_revision or 'base'}")
    print(f"target_revision={target_revision}")
    print(f"backup_evidence_confirmed_at={args.backup_confirmed_at}")


if __name__ == "__main__":
    main()
