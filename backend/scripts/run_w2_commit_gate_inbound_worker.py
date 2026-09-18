"""Run the dedicated W2 staged-result consumer outside the public API process."""

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
from app.runtime.session import create_worker_session_factory  # noqa: E402
from app.runtime.sqs import Boto3SqsPort  # noqa: E402
from app.runtime.w2_commit_gate_worker import W2CommitGateInboundWorker  # noqa: E402


def _build_worker() -> W2CommitGateInboundWorker:
    if not settings.w2_commit_gate_inbound_queue_url:
        raise SystemExit("W2_COMMIT_GATE_INBOUND_QUEUE_URL must be set")
    if not settings.w2_commit_gate_expected_sender_id:
        raise SystemExit("W2_COMMIT_GATE_EXPECTED_SENDER_ID must be set")
    return W2CommitGateInboundWorker(
        session_factory=create_worker_session_factory(),
        sqs=Boto3SqsPort(),
        queue_url=settings.w2_commit_gate_inbound_queue_url,
        expected_sender_id=settings.w2_commit_gate_expected_sender_id,
        expected_producer=settings.w2_commit_gate_expected_producer,
        batch_size=settings.w2_commit_gate_batch_size,
        visibility_timeout_seconds=settings.w2_commit_gate_visibility_seconds,
        wait_time_seconds=settings.w2_commit_gate_wait_seconds,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    worker = _build_worker()
    while True:
        result = worker.drain_once()
        print(json.dumps(asdict(result), separators=(",", ":")))
        if args.once:
            return
        if result.received == 0:
            time.sleep(settings.w1_worker_poll_seconds)


if __name__ == "__main__":
    main()
