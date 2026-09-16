"""Run the W1 execution-queue worker outside the public API process."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.runtime.session import create_worker_session_factory  # noqa: E402
from app.runtime.sqs import Boto3SqsPort  # noqa: E402
from app.runtime.workers import JobWorker  # noqa: E402


def _build_worker() -> JobWorker:
    if not settings.w1_execution_queue_url:
        raise SystemExit("W1_JOB_EXECUTION_QUEUE_URL must be set before starting the job worker")
    worker_id = settings.w1_worker_id or socket.gethostname()
    return JobWorker(
        session_factory=create_worker_session_factory(),
        sqs=Boto3SqsPort(),
        execution_queue_url=settings.w1_execution_queue_url,
        worker_id=worker_id[:128],
        visibility_timeout_seconds=settings.w1_sqs_visibility_seconds,
        long_poll_seconds=settings.w1_sqs_long_poll_seconds,
        lease_heartbeat_seconds=settings.w1_lease_heartbeat_seconds,
    )


def _run_once(worker: JobWorker) -> int:
    result = worker.drain_once()
    print(json.dumps(result.__dict__, separators=(",", ":")))
    return result.received


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="Receive one bounded SQS batch and exit"
    )
    args = parser.parse_args()
    worker = _build_worker()
    if args.once:
        _run_once(worker)
        return
    while True:
        received = _run_once(worker)
        if received == 0:
            time.sleep(settings.w1_worker_poll_seconds)


if __name__ == "__main__":
    main()
