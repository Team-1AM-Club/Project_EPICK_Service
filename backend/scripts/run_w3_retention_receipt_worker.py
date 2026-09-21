"""Run the bounded W3 retention-receipt consumer outside the public API process."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.runtime.session import create_deletion_worker_session_factory  # noqa: E402
from app.runtime.sqs import Boto3SqsPort  # noqa: E402
from app.runtime.w3_deletion_worker import W3RetentionReceiptWorker  # noqa: E402


def _build_worker() -> W3RetentionReceiptWorker:
    if not settings.w3_retention_receipt_queue_url:
        raise SystemExit("W3_RETENTION_RECEIPT_QUEUE_URL must be set")
    if not settings.w3_retention_expected_w3_sender_id:
        raise SystemExit("W3_RETENTION_EXPECTED_W3_SENDER_ID must be set")
    return W3RetentionReceiptWorker(
        session_factory=create_deletion_worker_session_factory(),
        sqs=Boto3SqsPort(),
        queue_url=settings.w3_retention_receipt_queue_url,
        expected_sender_id=settings.w3_retention_expected_w3_sender_id,
        batch_size=settings.w3_retention_receipt_batch_size,
        visibility_timeout_seconds=settings.w3_retention_receipt_visibility_seconds,
        wait_time_seconds=settings.w3_retention_receipt_wait_seconds,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    worker = _build_worker()
    while True:
        result = worker.drain_once()
        # Only bounded counts are emitted. Queue URLs, Role IDs, payloads and
        # receipt handles are deliberately absent from process output.
        print(json.dumps(asdict(result), separators=(",", ":")))
        if args.once:
            return
        if result.received == 0:
            time.sleep(settings.w1_worker_poll_seconds)


if __name__ == "__main__":
    main()
