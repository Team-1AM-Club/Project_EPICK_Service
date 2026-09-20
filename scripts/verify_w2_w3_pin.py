"""Verify a pinned W2 model against W3 C-01 without claiming live transport."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
FIXTURE_NAMES = (
    "version-available",
    "version-partial",
    "observation-changed",
    "restriction-changed",
)


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def git_head(path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(path.resolve()), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def agreed_envelope(source_event) -> dict:
    value = source_event.model_dump(mode="json")
    payload = value["payload"]
    payload.setdefault("schema_version", "w2.source.v1")
    return {
        "schema_version": "1.0",
        "event_id": value["event_id"],
        "event_type": value["event_type"],
        "producer": "w2",
        "occurred_at": value["occurred_at"],
        "aggregate_type": "source",
        "aggregate_id": value["aggregate_id"],
        "revision": value["aggregate_revision"],
        "payload": payload,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--w2-checkout", type=Path, required=True)
    parser.add_argument("--w2-sha", required=True)
    parser.add_argument("--w3-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not FULL_SHA.fullmatch(args.w2_sha) or not FULL_SHA.fullmatch(args.w3_sha):
        raise ValueError("FULL_SHA_REQUIRED")
    w2_source = args.w2_checkout.resolve() / "src"
    if not w2_source.is_dir():
        raise ValueError("W2_SOURCE_CHECKOUT_REQUIRED")
    root = Path(__file__).resolve().parents[1]
    if git_head(args.w2_checkout) != args.w2_sha:
        raise ValueError("W2_CHECKOUT_SHA_MISMATCH")
    if git_head(root) != args.w3_sha:
        raise ValueError("W3_CHECKOUT_SHA_MISMATCH")
    sys.path.insert(0, str(w2_source))
    sys.path.insert(0, str(root / "src"))

    from epick_engine.source_collection.contracts import SourceEvent
    from epick_engine.source_collection.worker import (
        OutboxDeliveryError,
        _source_event_from_outbox_row,
    )
    from pydantic import ValidationError
    from w3_knowledge.c01.contracts import Event
    from w3_knowledge.c01.store import Store

    fixtures = root / "contracts" / "c01" / "v0.2-candidate" / "fixtures"
    internal_events = []
    rebuilt_deliverable = []
    restriction_delivery = None
    raw_rejected = 0
    agreed_events = []
    for name in FIXTURE_NAMES:
        envelope = json.loads((fixtures / f"source-event-{name}.json").read_text(encoding="utf-8"))
        payload = envelope["payload"]
        if name in {"observation-changed", "restriction-changed"}:
            payload.pop("schema_version")
        internal = SourceEvent.model_validate(
            {
                "event_id": envelope["event_id"],
                "event_type": envelope["event_type"],
                "schema_version": "w2.source.v1",
                "aggregate_id": envelope["aggregate_id"],
                "aggregate_revision": envelope["revision"],
                "occurred_at": envelope["occurred_at"],
                "payload": payload,
            }
        )
        internal_events.append(internal.model_dump(mode="json"))
        row = SimpleNamespace(
            event_id=internal.event_id,
            event_type=internal.event_type.value,
            schema_version=internal.schema_version,
            aggregate_id=internal.aggregate_id,
            aggregate_revision=internal.aggregate_revision,
            occurred_at=internal.occurred_at,
            payload=internal.payload.model_dump(mode="json"),
        )
        try:
            rebuilt = _source_event_from_outbox_row(row)
            rebuilt_deliverable.append(rebuilt.event_type.value)
        except OutboxDeliveryError:
            if name != "restriction-changed":
                raise
            restriction_delivery = "REJECTED_NOT_DELIVERABLE"
        try:
            Event.model_validate(internal.model_dump(mode="json"))
        except ValidationError:
            raw_rejected += 1
        agreed_events.append(Event.model_validate(agreed_envelope(internal)))

    class Authority:
        @staticmethod
        def is_registered(source_id: UUID) -> bool:
            return isinstance(source_id, UUID)

    with tempfile.TemporaryDirectory() as directory:
        with Store(
            Path(directory) / "c01.sqlite",
            restriction_scope="version",
            max_ttl_seconds=3600,
            source_authority=Authority(),
            clock=lambda: "2026-09-16T00:00:00Z",
        ) as store:
            outcomes = [store.consume(event)["outcome"] for event in agreed_events]
            status = store.status(agreed_events[0].aggregate_id)

    fixture_hash = hashlib.sha256(canonical(internal_events).encode()).hexdigest()
    report = {
        "status": "W2_TRANSPORT_ADAPTER_BLOCKED",
        "actual_environment": False,
        "w2_sha": args.w2_sha,
        "w3_sha": args.w3_sha,
        "fixture_sha256": fixture_hash,
        "w2_internal_events_valid": len(internal_events),
        "w2_outbox_rebuilt_deliverable_types": rebuilt_deliverable,
        "w2_restriction_delivery": restriction_delivery,
        "raw_w2_wire_rejected_by_w3": raw_rejected,
        "agreed_envelope_validated_by_w3": len(agreed_events),
        "w3_local_consume_outcomes": outcomes,
        "w3_local_status": {
            "event_cursor": status["event_cursor"],
            "restriction_revision": status["restriction_revision"],
            "index_ack": status["index_ack"],
            "reason": status["reason"],
        },
        "live_transport": "NOT_RUN",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
