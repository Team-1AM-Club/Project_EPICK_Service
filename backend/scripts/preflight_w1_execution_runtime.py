"""Verify W1 execution runtime prerequisites without publishing or consuming SQS messages."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import boto3  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.runtime.execution_preflight import (  # noqa: E402
    ExecutionRuntimePreflightError,
    build_execution_runtime_preflight_config,
    verify_execution_runtime,
)
from app.runtime.session import create_worker_session_factory  # noqa: E402


def main() -> None:
    aws_session = boto3.session.Session()
    config = build_execution_runtime_preflight_config(
        execution_queue_url=settings.w1_execution_queue_url,
        command_queue_url=settings.w2_collection_command_queue_url,
        worker_id=settings.w1_worker_id,
        relay_instance_id=settings.w1_outbox_relay_instance_id,
        aws_region=aws_session.region_name,
    )
    result = verify_execution_runtime(
        config=config,
        session_factory=create_worker_session_factory(),
        sqs_client=aws_session.client("sqs"),
    )
    print(json.dumps(result.as_safe_dict(), separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except ExecutionRuntimePreflightError as error:
        raise SystemExit(str(error)) from error
