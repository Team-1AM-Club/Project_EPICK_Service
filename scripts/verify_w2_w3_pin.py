"""Verify W2's pinned outbox and actual public wire adapter against W3 C-01."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
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
    from epick_engine.source_collection.source_authority_api import create_source_authority_api
    from epick_engine.source_collection.w3_public_transport import (
        W3PublicEventPublisher,
        source_event_to_w3_wire,
    )
    from epick_engine.source_collection.worker import _source_event_from_outbox_row
    import uvicorn
    from w3_knowledge.c01.contracts import Event
    from w3_knowledge.c01.http import make_server
    from w3_knowledge.c01.source_authority_http import HTTPSourceAuthority
    from w3_knowledge.c01.store import Store

    fixtures = root / "contracts" / "c01" / "v0.2-candidate" / "fixtures"
    internal_events = []
    source_events = []
    rebuilt_deliverable = []
    public_wires = []
    accepted_events = []
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
        rebuilt = _source_event_from_outbox_row(row)
        source_events.append(rebuilt)
        rebuilt_deliverable.append(rebuilt.event_type.value)
        wire = source_event_to_w3_wire(rebuilt)
        public_wires.append(wire)
        accepted_events.append(Event.model_validate(wire))

    revisions = [event.revision for event in accepted_events]
    restriction_revisions = [
        event.payload["restriction_revision"]
        for event in accepted_events
        if event.event_type == "source.restriction.changed"
    ]
    if revisions != [1, 2, 3, 4] or restriction_revisions != [1]:
        raise ValueError("W2_REVISION_SEQUENCE_MISMATCH")

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
            outcomes = [store.consume(event)["outcome"] for event in accepted_events]
            status = store.status(accepted_events[0].aggregate_id)

        class Session:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def get(self, _, source_id):
                return object() if source_id == source_events[0].aggregate_id else None

        authority_app = create_source_authority_api(
            session_factory=Session,
            bearer_token="synthetic-authority-token",
        )
        authority_server = uvicorn.Server(
            uvicorn.Config(
                authority_app, host="127.0.0.1", port=0, log_level="critical", access_log=False
            )
        )
        authority_thread = threading.Thread(target=authority_server.run, daemon=True)
        authority_thread.start()
        try:
            deadline = time.monotonic() + 5
            while not authority_server.started and time.monotonic() < deadline:
                time.sleep(0.01)
            if not authority_server.started:
                raise RuntimeError("W2_AUTHORITY_HTTP_START_FAILED")
            authority_port = authority_server.servers[0].sockets[0].getsockname()[1]
            with Store(
                Path(directory) / "c01-http.sqlite",
                restriction_scope="version",
                max_ttl_seconds=3600,
                source_authority=HTTPSourceAuthority(
                    f"http://127.0.0.1:{authority_port}", "synthetic-authority-token"
                ),
                clock=lambda: "2026-09-16T00:00:00Z",
            ) as http_store:
                w3_server = make_server(
                    http_store,
                    {
                        "w2": "synthetic-w2-token",
                        "operator": "synthetic-operator-token",
                        "w4": "synthetic-w4-token",
                    },
                    port=0,
                )
                w3_thread = threading.Thread(target=w3_server.serve_forever, daemon=True)
                w3_thread.start()
                try:
                    publisher = W3PublicEventPublisher(
                        endpoint=f"http://127.0.0.1:{w3_server.server_port}/c01/v1/events",
                        bearer_token="synthetic-w2-token",
                    )
                    acknowledgements = [
                        str(publisher.publish(event).event_id) for event in source_events
                    ]
                    http_status = http_store.status(accepted_events[0].aggregate_id)
                finally:
                    w3_server.shutdown()
                    w3_thread.join(timeout=3)
                    w3_server.server_close()
        finally:
            authority_server.should_exit = True
            authority_thread.join(timeout=3)

    fixture_hash = hashlib.sha256(canonical(public_wires).encode()).hexdigest()
    report = {
        "status": "W2_PINNED_WIRE_LOCAL_ACCEPTED",
        "actual_environment": False,
        "w2_sha": args.w2_sha,
        "w3_sha": args.w3_sha,
        "w2_wire_sha256": fixture_hash,
        "manual_envelope_mapping": False,
        "w2_adapter": "epick_engine.source_collection.w3_public_transport.source_event_to_w3_wire",
        "w2_internal_events_valid": len(internal_events),
        "w2_outbox_rebuilt_deliverable_types": rebuilt_deliverable,
        "w2_restriction_delivery": "DELIVERABLE",
        "outer_revisions": revisions,
        "restriction_revisions": restriction_revisions,
        "w2_public_wire_validated_by_w3": len(accepted_events),
        "w3_local_consume_outcomes": outcomes,
        "w3_local_status": {
            "event_cursor": status["event_cursor"],
            "restriction_revision": status["restriction_revision"],
            "index_ack": status["index_ack"],
            "reason": status["reason"],
        },
        "synthetic_bidirectional_http": {
            "status": "PASS",
            "w2_publisher_receipts": len(acknowledgements),
            "w3_event_cursor": http_status["event_cursor"],
            "w3_restriction_revision": http_status["restriction_revision"],
            "w3_index_ack": http_status["index_ack"],
            "w2_authority_backend": "SYNTHETIC_SESSION_NOT_POSTGRESQL",
        },
        "postgres_outbox_e2e": "NOT_RUN",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
