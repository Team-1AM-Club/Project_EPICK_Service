"""Run W1's mutation-free preflight for the dedicated W2 commit-gate ingress."""

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
from app.runtime.w2_commit_gate_preflight import (  # noqa: E402
    W2CommitGatePreflightError,
    build_w2_commit_gate_preflight_config,
    verify_w2_commit_gate_runtime,
)


def main() -> None:
    config = build_w2_commit_gate_preflight_config(
        queue_url=settings.w2_commit_gate_inbound_queue_url,
        dlq_url=settings.w2_commit_gate_inbound_dlq_url,
        legacy_result_queue_url=settings.w2_collection_result_queue_url,
        expected_producer=settings.w2_commit_gate_expected_producer,
        expected_sender_id=settings.w2_commit_gate_expected_sender_id,
    )
    result = verify_w2_commit_gate_runtime(
        config=config,
        session_factory=create_worker_session_factory(),
        sqs=Boto3SqsPort(),
    )
    print(json.dumps(result.as_safe_dict(), separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except W2CommitGatePreflightError as error:
        raise SystemExit(str(error)) from error
