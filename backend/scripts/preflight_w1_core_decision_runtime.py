"""Mutation-free W1 checks for the private W3 Core Decision consumer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.runtime.session import create_worker_session_factory  # noqa: E402
from app.runtime.sqs import Boto3SqsPort  # noqa: E402
from app.runtime.w3_core_decision_preflight import (  # noqa: E402
    W3CoreDecisionPreflightError,
    build_w3_core_decision_preflight_config,
    verify_w3_core_decision_runtime,
)


def main() -> None:
    config = build_w3_core_decision_preflight_config(
        queue_url=settings.w3_core_decision_queue_url,
        dlq_url=settings.w3_core_decision_dlq_url,
        expected_producer=settings.w3_core_decision_expected_producer,
        expected_sender_id=settings.w3_core_decision_expected_sender_id,
    )
    result = verify_w3_core_decision_runtime(
        config=config,
        session_factory=create_worker_session_factory(),
        sqs=Boto3SqsPort(),
    )
    print(json.dumps(result.as_safe_dict(), separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except W3CoreDecisionPreflightError as error:
        raise SystemExit(str(error)) from error
