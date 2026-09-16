import json

import pytest
from pydantic import ValidationError

from w3_knowledge.restriction.contracts import Event, ReplayBatch, Snapshot, Signal


def event(revision=1, status="RELEASED", event_id=None):
    return {
        "schema_version": "w3-restriction/0.1-draft",
        "event_type": "source.restriction.changed",
        "event_id": event_id or f"evt-{revision}",
        "aggregate_id": "source-a",
        "aggregate_revision": revision,
        "occurred_at": "2026-09-13T00:00:00Z",
        "published_at": "2026-09-13T00:00:01Z",
        "payload": {
            "source_id": "source-a",
            "restriction_id": "restriction-a",
            "status": status,
            "accuracy": "UNKNOWN",
            "reason_code": "POLICY_REVIEW",
            "replacement_source_id": None,
            "index_key": {
                "source_version_id": "version-a",
                "extraction_revision_id": "extraction-a",
                "representation": "text",
                "normalization_version": "normalization-a",
            },
        },
    }


def parse(cls, value):
    return cls.model_validate_json(json.dumps(value))


@pytest.mark.parametrize("status", ["RESTRICTED", "RELEASED"])
def test_valid_contract_preserves_snapshot(status):
    value = event(status=status)
    assert parse(Event, value).model_dump(mode="json") == value


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "w3-restriction/1.0"},
        {"aggregate_revision": True},
        {"aggregate_revision": "1"},
        {"aggregate_revision": 0},
        {"aggregate_revision": 2**63},
        {"aggregate_id": "another-source"},
        {"project_id": "private-project"},
        {"occurred_at": "2026-02-30T00:00:00Z"},
        {"occurred_at": "2026-09-13T00:00:00"},
    ],
)
def test_public_contract_rejects_ambiguous_or_private_input(change):
    with pytest.raises(ValidationError):
        parse(Event, {**event(), **change})


def test_payload_rejects_body_and_invalid_replay_source():
    value = event()
    value["payload"]["body"] = "must not be public"
    with pytest.raises(ValidationError):
        parse(Event, value)
    with pytest.raises(ValidationError):
        parse(
            ReplayBatch,
            {
                "schema_version": "w3-restriction/0.1-draft",
                "source_id": "source-b",
                "after_revision": 0,
                "high_watermark": 1,
                "retention_start": "2026-09-12T00:00:00Z",
                "events": [event()],
            },
        )


def test_snapshot_cannot_claim_time_before_its_event():
    with pytest.raises(ValidationError):
        parse(
            Snapshot,
            {
                "schema_version": "w3-restriction/0.1-draft",
                "as_of": "2026-09-12T00:00:00Z",
                "event": event(),
            },
        )


def test_signal_cannot_claim_usable_while_restricted():
    with pytest.raises(ValidationError):
        parse(
            Signal,
            {
                "schema_version": "w3-restriction/0.1-draft",
                "event_type": "w3.source.usability.changed",
                "signal_id": "sig-a",
                "source_id": "source-a",
                "generation": 1,
                "restriction_revision": 1,
                "required_revision": 1,
                "usable": True,
                "reason": "RESTRICTED",
                "index_key": event()["payload"]["index_key"],
                "history_complete": True,
            },
        )
