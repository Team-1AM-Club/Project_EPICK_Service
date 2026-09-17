"""Run W1's durable W2 commit-gate recovery loop outside the public API process.

This worker only re-drives W1-owned durable operations and existing outbox messages.
It deliberately does not parse or consume a raw W2 ACK; that adapter remains disabled
until W2 supplies its canonical ACK schema and fixtures.
"""

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
from app.runtime.w2_commit_gate_worker import W2CommitGateRecoveryWorker  # noqa: E402


def _build_worker() -> W2CommitGateRecoveryWorker:
    return W2CommitGateRecoveryWorker(session_factory=create_worker_session_factory())


def _run_once(worker: W2CommitGateRecoveryWorker) -> int:
    result = worker.recover_once(limit=settings.w1_commit_gate_recovery_batch_size)
    print(json.dumps(result.__dict__, separators=(",", ":")))
    return result.scanned


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="Recover one bounded operation batch and exit",
    )
    args = parser.parse_args()
    worker = _build_worker()
    if args.once:
        _run_once(worker)
        return

    while True:
        _run_once(worker)
        time.sleep(settings.w1_commit_gate_recovery_poll_seconds)


if __name__ == "__main__":
    main()
