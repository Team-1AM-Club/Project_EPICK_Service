"""Apply EPICK's explicit runtime DML manifest using the migration principal.

This is a release operation, not an API/worker startup action. It is
idempotent and intentionally requires MIGRATION_DATABASE_URL so a runtime
principal cannot grant itself additional database rights.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine

BACKEND_ROOT = Path(__file__).parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402

PRIVILEGE_MANIFEST_PATH = BACKEND_ROOT / "infra" / "postgres" / "runtime_privileges.sql"


def main() -> None:
    migration_url = settings.migration_database_url
    if not migration_url:
        raise SystemExit("MIGRATION_DATABASE_URL must be set for the migration principal")

    manifest = PRIVILEGE_MANIFEST_PATH.read_text(encoding="utf-8")
    engine = create_engine(migration_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(manifest)
    finally:
        engine.dispose()

    print("PostgreSQL runtime privilege manifest applied")


if __name__ == "__main__":
    main()
