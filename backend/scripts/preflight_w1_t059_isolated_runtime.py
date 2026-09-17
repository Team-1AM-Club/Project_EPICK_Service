"""Verify T059 uses only disposable DB and SQS resources before any message operation."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import boto3  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.runtime.t059_isolation_preflight import (  # noqa: E402
    T059IsolationPreflightError,
    build_t059_isolation_preflight_config,
    verify_t059_isolation,
)


def _environment(name: str) -> str | None:
    import os

    return os.environ.get(name)


def main() -> None:
    aws_session = boto3.session.Session()
    config = build_t059_isolation_preflight_config(
        worker_database_url=_environment("T059_WORKER_DATABASE_URL"),
        execution_queue_url=_environment("T059_EXECUTION_QUEUE_URL"),
        command_queue_url=_environment("T059_W2_COMMAND_QUEUE_URL"),
        run_id=_environment("T059_RUN_ID"),
        aws_region=aws_session.region_name,
    )
    engine = create_engine(config.worker_database_url, pool_pre_ping=True)
    try:
        result = verify_t059_isolation(
            config=config,
            session_factory=sessionmaker(bind=engine, autoflush=False, expire_on_commit=False),
            sqs_client=aws_session.client("sqs"),
        )
    finally:
        engine.dispose()
    print(json.dumps(result.as_safe_dict(), separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except T059IsolationPreflightError as error:
        raise SystemExit(str(error)) from error
