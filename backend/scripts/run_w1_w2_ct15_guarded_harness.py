"""Run one W1-owned, synthetic-only CT15 fixture or state-transition action."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.runtime.w1_w2_ct15_harness import (  # noqa: E402
    Ct15HarnessError,
    cancel_primary,
    delete_primary,
    inspect_fixture,
    redrive_primary_gate_operation,
    seed_fixture,
    validate_run_id,
)

_ACTIONS = ("seed", "inspect", "cancel-primary", "delete-primary", "redrive-primary")


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value or not value.strip():
        raise Ct15HarnessError(f"{name} must be set")
    return value.strip()


def _require_configuration() -> tuple[str, str]:
    if os.environ.get("W1_CT15_EXECUTE_SYNTHETIC") != "YES":
        raise Ct15HarnessError("set W1_CT15_EXECUTE_SYNTHETIC=YES to run the CT15 harness")
    run_id = validate_run_id(_required("W1_CT15_RUN_ID"))
    database_url = _required("W1_CT15_SEED_DATABASE_URL")
    try:
        parsed = make_url(database_url)
    except Exception as error:  # SQLAlchemy keeps parse details out of operator output.
        raise Ct15HarnessError(
            "W1_CT15_SEED_DATABASE_URL must be a PostgreSQL CT15 database"
        ) from error
    database_name = parsed.database or ""
    if not parsed.drivername.startswith("postgresql") or "ct15" not in database_name.lower():
        raise Ct15HarnessError("W1_CT15_SEED_DATABASE_URL must be a PostgreSQL CT15 database")
    return run_id, database_url


def run(*, action: str) -> dict[str, object]:
    run_id, database_url = _require_configuration()
    engine = create_engine(database_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory.begin() as session:
        if action == "seed":
            return seed_fixture(session=session, run_id=run_id).as_safe_dict()
        if action == "inspect":
            return inspect_fixture(session=session, run_id=run_id)
        if action == "cancel-primary":
            return cancel_primary(session=session, run_id=run_id)
        if action == "delete-primary":
            return delete_primary(session=session, run_id=run_id)
        if action == "redrive-primary":
            return redrive_primary_gate_operation(session=session, run_id=run_id)
    raise Ct15HarnessError("CT15 harness action is unsupported")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=_ACTIONS)
    args = parser.parse_args()
    print(json.dumps(run(action=args.action), separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (Ct15HarnessError, SQLAlchemyError) as error:
        if isinstance(error, Ct15HarnessError):
            raise SystemExit(str(error)) from error
        raise SystemExit("CT15 harness database operation failed") from error
