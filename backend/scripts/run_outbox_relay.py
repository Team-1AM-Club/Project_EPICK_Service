"""Run the W1 private Job outbox relay outside the public API process."""

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
from app.models.registry import load_all_models  # noqa: E402
from app.runtime.outbox_relay import OutboxRelay, QueueUrlRegistry  # noqa: E402
from app.runtime.session import create_worker_session_factory  # noqa: E402
from app.runtime.sqs import Boto3SqsPort  # noqa: E402


def _build_relay(*, scope: str = "all") -> OutboxRelay:
    # This narrow worker entrypoint does not import the API router graph. Load
    # every mapped table before SQLAlchemy resolves OutboxMessage's string
    # foreign keys (notably deletion_requests.id) during the first flush.
    load_all_models()
    if not any(
        (
            settings.w1_execution_queue_url,
            settings.w2_collection_command_queue_url,
            settings.w2_commit_gate_outbound_queue_url,
            settings.w4_recommendation_execution_queue_url,
            settings.w3_retention_command_queue_url,
        )
    ):
        raise SystemExit("at least one private outbox destination must be configured")
    relay_id = settings.w1_outbox_relay_instance_id or socket.gethostname()
    return OutboxRelay(
        session_factory=create_worker_session_factory(),
        sqs=Boto3SqsPort(),
        queues=QueueUrlRegistry(
            w1_execution_queue_url=settings.w1_execution_queue_url,
            w2_collection_command_queue_url=settings.w2_collection_command_queue_url,
            w2_commit_gate_command_queue_url=settings.w2_commit_gate_outbound_queue_url,
            w4_recommendation_execution_queue_url=(settings.w4_recommendation_execution_queue_url),
            w3_retention_command_queue_url=settings.w3_retention_command_queue_url,
            commit_gate_only=settings.w2_ct15_gate_only_queue_approved,
            retention_only=scope == "w3-retention",
        ),
        relay_id=relay_id[:128],
        lease_seconds=settings.w1_outbox_relay_lease_seconds,
        retry_base_seconds=settings.w1_outbox_relay_retry_base_seconds,
        retry_max_seconds=settings.w1_outbox_relay_retry_max_seconds,
    )


def _run_once(relay: OutboxRelay) -> int:
    result = relay.drain_once(limit=settings.w1_outbox_relay_batch_size)
    print(
        json.dumps(
            {
                "claimed": result.claimed,
                "published": result.published,
                "retry_scheduled": result.retry_scheduled,
                "failed_final": result.failed_final,
                "stale_completion": result.stale_completion,
            },
            separators=(",", ":"),
        )
    )
    return result.claimed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Drain one bounded batch and exit")
    parser.add_argument(
        "--scope",
        choices=("all", "w3-retention"),
        default="all",
        help="Limit claims to one dedicated route set",
    )
    args = parser.parse_args()
    relay = _build_relay(scope=args.scope)
    if args.once:
        _run_once(relay)
        return
    while True:
        claimed = _run_once(relay)
        if claimed == 0:
            time.sleep(settings.w1_outbox_relay_poll_seconds)


if __name__ == "__main__":
    main()
