"""Run the W1 W2-collection-result queue worker outside the public API process."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.runtime.session import create_worker_session_factory  # noqa: E402
from app.runtime.sqs import Boto3SqsPort  # noqa: E402
from app.runtime.workers import CollectionResultWorker  # noqa: E402


def _build_worker() -> CollectionResultWorker:
    if not settings.w2_collection_result_queue_url:
        raise SystemExit(
            "W2_COLLECTION_RESULT_QUEUE_URL must be set before starting the result worker"
        )
    return CollectionResultWorker(
        session_factory=create_worker_session_factory(),
        sqs=Boto3SqsPort(),
        result_queue_url=settings.w2_collection_result_queue_url,
        visibility_timeout_seconds=settings.w1_sqs_visibility_seconds,
        long_poll_seconds=settings.w1_sqs_long_poll_seconds,
    )


def _run_once(worker: CollectionResultWorker) -> int:
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
