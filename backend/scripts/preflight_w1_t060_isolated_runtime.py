"""Verify T060 uses only disposable DB and SQS resources before any mutation."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import boto3  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.runtime.t060_isolation_preflight import (  # noqa: E402
    T060IsolationPreflightError,
    build_t060_isolation_preflight_config,
    verify_t060_isolation,
)


def main() -> None:
    aws_session = boto3.session.Session()
    config = build_t060_isolation_preflight_config(
        worker_database_url=os.environ.get("T060_WORKER_DATABASE_URL"),
        execution_queue_url=os.environ.get("T060_EXECUTION_QUEUE_URL"),
        command_queue_url=os.environ.get("T060_W2_COMMAND_QUEUE_URL"),
        result_queue_url=os.environ.get("T060_W2_RESULT_QUEUE_URL"),
        run_id=os.environ.get("T060_RUN_ID"),
        aws_region=aws_session.region_name,
    )
    engine = create_engine(config.worker_database_url, pool_pre_ping=True)
    try:
        result = verify_t060_isolation(
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
    except T060IsolationPreflightError as error:
        raise SystemExit(str(error)) from error
