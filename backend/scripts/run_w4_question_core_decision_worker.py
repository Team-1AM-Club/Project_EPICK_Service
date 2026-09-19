"""Run the private W4 Question Core consumer outside the public API process."""

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
from app.runtime.w4_question_core_decision_worker import (  # noqa: E402
    W4QuestionCoreDecisionWorker,
)


def _build_worker() -> W4QuestionCoreDecisionWorker:
    if not settings.w4_question_core_decision_queue_url:
        raise SystemExit("W4_QUESTION_CORE_DECISION_QUEUE_URL must be set")
    if not settings.w4_question_core_decision_expected_sender_id:
        raise SystemExit("W4_QUESTION_CORE_DECISION_EXPECTED_SENDER_ID must be set")
    return W4QuestionCoreDecisionWorker(
        session_factory=create_worker_session_factory(),
        sqs=Boto3SqsPort(),
        queue_url=settings.w4_question_core_decision_queue_url,
        expected_sender_id=settings.w4_question_core_decision_expected_sender_id,
        expected_producer=settings.w4_question_core_decision_expected_producer,
        batch_size=settings.w4_question_core_decision_batch_size,
        visibility_timeout_seconds=settings.w4_question_core_decision_visibility_seconds,
        wait_time_seconds=settings.w4_question_core_decision_wait_seconds,
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
