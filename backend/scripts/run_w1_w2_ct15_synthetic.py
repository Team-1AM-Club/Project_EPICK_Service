"""Drain a disposable W2 commit-gate CT15 queue by receipt, never by purge.

The script is intentionally a W1-side probe.  It does not fabricate a W2
private state transition: W2's approved isolated producer must place the
pinned fixtures on the dedicated queue before it runs.  The output contains
only totals and one-way identifiers, never a DSN, a secret, or a staged body.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.engine import make_url

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
from app.runtime.w2_commit_gate_worker import W2CommitGateInboundWorker  # noqa: E402


class Ct15ScenarioError(RuntimeError):
    """A safe explanation for refusing an unsafe CT15 probe."""


@dataclass(frozen=True, slots=True)
class Ct15Configuration:
    run_id: str
    queue_url: str
    dlq_url: str
    max_drain_cycles: int


def _value(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value else None


def _required(name: str) -> str:
    value = _value(name)
    if not value:
        raise Ct15ScenarioError(f"{name} must be set")
    return value


def _queue_name(queue_url: str) -> str:
    return urlparse(queue_url).path.rsplit("/", 1)[-1]


def _safe_id(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_configuration() -> Ct15Configuration:
    if _value("W1_CT15_EXECUTE_SYNTHETIC") != "YES":
        raise Ct15ScenarioError("set W1_CT15_EXECUTE_SYNTHETIC=YES to run the CT15 probe")
    run_id = _required("W1_CT15_RUN_ID")
    if "ct15" not in run_id.lower():
        raise Ct15ScenarioError("W1_CT15_RUN_ID must contain ct15")
    worker_database_url = _required("WORKER_DATABASE_URL")
    database_name = make_url(worker_database_url).database or ""
    if "ct15" not in database_name.lower():
        raise Ct15ScenarioError("CT15 requires a dedicated database containing ct15 in its name")

    config = build_w2_commit_gate_preflight_config(
        queue_url=settings.w2_commit_gate_inbound_queue_url,
        dlq_url=settings.w2_commit_gate_inbound_dlq_url,
        legacy_result_queue_url=settings.w2_collection_result_queue_url,
        expected_producer=settings.w2_commit_gate_expected_producer,
        expected_sender_id=settings.w2_commit_gate_expected_sender_id,
    )
    for queue_url in (config.queue_url, config.dlq_url):
        if "ct15" not in _queue_name(queue_url).lower():
            raise Ct15ScenarioError("CT15 requires dedicated queue names containing ct15")
    raw_cycles = _value("W1_CT15_MAX_DRAIN_CYCLES") or "20"
    try:
        max_drain_cycles = int(raw_cycles)
    except ValueError as error:
        raise Ct15ScenarioError("W1_CT15_MAX_DRAIN_CYCLES must be an integer") from error
    if not 1 <= max_drain_cycles <= 100:
        raise Ct15ScenarioError("W1_CT15_MAX_DRAIN_CYCLES must be between 1 and 100")
    return Ct15Configuration(
        run_id=run_id,
        queue_url=config.queue_url,
        dlq_url=config.dlq_url,
        max_drain_cycles=max_drain_cycles,
    )


def run() -> dict[str, object]:
    config = _require_configuration()
    sqs = Boto3SqsPort()
    session_factory = create_worker_session_factory()
    preflight = verify_w2_commit_gate_runtime(
        config=build_w2_commit_gate_preflight_config(
            queue_url=settings.w2_commit_gate_inbound_queue_url,
            dlq_url=settings.w2_commit_gate_inbound_dlq_url,
            legacy_result_queue_url=settings.w2_collection_result_queue_url,
            expected_producer=settings.w2_commit_gate_expected_producer,
            expected_sender_id=settings.w2_commit_gate_expected_sender_id,
        ),
        session_factory=session_factory,
        sqs=sqs,
    )
    worker = W2CommitGateInboundWorker(
        session_factory=session_factory,
        sqs=sqs,
        queue_url=config.queue_url,
        expected_sender_id=settings.w2_commit_gate_expected_sender_id or "",
        expected_producer=settings.w2_commit_gate_expected_producer,
        batch_size=settings.w2_commit_gate_batch_size,
        visibility_timeout_seconds=settings.w2_commit_gate_visibility_seconds,
        wait_time_seconds=settings.w2_commit_gate_wait_seconds,
    )
    totals = {
        "received": 0,
        "acknowledged": 0,
        "retry_scheduled": 0,
        "stale_discarded": 0,
        "rejected_schema": 0,
        "duplicate": 0,
    }
    for _ in range(config.max_drain_cycles):
        result = worker.drain_once()
        for name in totals:
            totals[name] += int(getattr(result, name))
        if result.received == 0:
            break
    # Deliberately no PurgeQueue call: successful messages were individually
    # deleted by receipt only after their W1 transaction committed.
    return {
        "status": "ok",
        "run_id": config.run_id,
        "preflight": preflight.as_safe_dict(),
        "queue_id": _safe_id(config.queue_url),
        "dlq_id": _safe_id(config.dlq_url),
        "max_drain_cycles": config.max_drain_cycles,
        "totals": totals,
    }


def main() -> None:
    print(json.dumps(run(), separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (Ct15ScenarioError, W2CommitGatePreflightError) as error:
        raise SystemExit(str(error)) from error
