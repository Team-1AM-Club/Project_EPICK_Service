"""Pure W2 recovery payloads for the pinned W3 C-01 contract."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from tests.contract.source_collection.test_posting_handoff import _posting_envelope

from epick_engine.source_collection.contracts import (
    SourceEnvelope,
    SourceEvent,
    SourceObservationSnapshot,
    SourceRestrictionSnapshot,
)
from epick_engine.source_collection.w3_recovery_payloads import (
    W3RecoveryPayloadError,
    make_replay_batch,
    make_snapshot,
)
from epick_engine.source_collection.w3_recovery_store import RecoveryHistory

SOURCE_ID = UUID("00000000-0000-4000-8000-000000000001")
COMPANY_ID = UUID("00000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 9, 21, tzinfo=UTC)


def _history(
    *events: SourceEvent,
    restriction_revision: int = 0,
    as_of: datetime = NOW,
) -> RecoveryHistory:
    return RecoveryHistory(
        source_id=SOURCE_ID,
        high_watermark=len(events),
        restriction_revision=restriction_revision,
        events=events,
        as_of=as_of,
    )


def _observation(revision: int, *, occurred_at: datetime = NOW) -> SourceEvent:
    return SourceEvent(
        event_id=uuid4(),
        event_type="source.observation.changed",
        schema_version="w2.source.v1",
        aggregate_id=SOURCE_ID,
        aggregate_revision=revision,
        occurred_at=occurred_at,
        payload=SourceObservationSnapshot(
            observation_id=uuid4(),
            source_id=SOURCE_ID,
            source_version_id=None,
            policy_decision_id=None,
            observed_at=occurred_at,
            access_class="public",
            acquisition_status="AVAILABLE",
            http_status=200,
            checked_url="https://careers.example.com/jobs/platform-engineer",
            error_code=None,
            representation="static_html",
        ),
    )


def _version(
    revision: int,
    *,
    version_id: UUID,
    occurred_at: datetime = NOW,
) -> SourceEvent:
    envelope = _posting_envelope(SimpleNamespace(source_id=SOURCE_ID, company_id=COMPANY_ID))
    envelope = SourceEnvelope.model_validate(
        envelope.model_copy(
            update={
                "source_version_id": version_id,
                "extraction_revision_id": uuid4(),
                "evidence_spans": [
                    span.model_copy(update={"source_version_id": version_id})
                    for span in envelope.evidence_spans
                ],
            }
        ).model_dump(mode="python")
    )
    return SourceEvent(
        event_id=uuid4(),
        event_type="source.version.available",
        schema_version="w2.source.v1",
        aggregate_id=SOURCE_ID,
        aggregate_revision=revision,
        occurred_at=occurred_at,
        payload=envelope,
    )


def _restriction(
    revision: int,
    *,
    restriction_id: UUID,
    restriction_revision: int,
    status: str = "active",
) -> SourceEvent:
    return SourceEvent(
        event_id=uuid4(),
        event_type="source.restriction.changed",
        schema_version="w2.source.v1",
        aggregate_id=SOURCE_ID,
        aggregate_revision=revision,
        occurred_at=NOW,
        payload=SourceRestrictionSnapshot(
            restriction_id=restriction_id,
            source_id=SOURCE_ID,
            source_version_id=None,
            restriction_revision=restriction_revision,
            restriction_status=status,
            accuracy_status="error_confirmed",
            reason_code="CONFIRMED_ERROR",
            changed_at=NOW,
            replacement_ref=None,
        ),
    )


def test_replay_batch_uses_exact_wire_fields_and_durable_cursor_page() -> None:
    """A wrong slice boundary would omit or duplicate revision 501."""

    events = tuple(_observation(revision) for revision in range(1, 502))

    first_page = make_replay_batch(_history(*events), after_cursor=0)
    last_page = make_replay_batch(_history(*events), after_cursor=500)

    assert set(first_page) == {
        "schema_version",
        "source_id",
        "after_cursor",
        "high_watermark",
        "retention_floor_cursor",
        "events",
    }
    assert first_page["schema_version"] == "w3-c01/0.2-candidate"
    assert first_page["source_id"] == str(SOURCE_ID)
    assert first_page["after_cursor"] == 0
    assert first_page["high_watermark"] == 501
    assert first_page["retention_floor_cursor"] == 0
    assert [event["revision"] for event in first_page["events"]] == list(range(1, 501))
    assert last_page["after_cursor"] == 500
    assert last_page["high_watermark"] == 501
    assert [event["revision"] for event in last_page["events"]] == [501]
    assert all(event["schema_version"] == "1.0" for event in last_page["events"])
    assert all(event["producer"] == "w2" for event in last_page["events"])
    replay_json = json.dumps(first_page)
    assert "job_id" not in replay_json
    assert "owner_id" not in replay_json
    assert "authenticated_owner_ref" not in replay_json


def test_replay_batch_allows_one_event_and_empty_page_only_at_high_watermark() -> None:
    """A valid A=H page must not be confused with missing history."""

    history = _history(_observation(1))

    assert [
        event["revision"] for event in make_replay_batch(history, after_cursor=0)["events"]
    ] == [1]
    assert make_replay_batch(history, after_cursor=1)["events"] == []


@pytest.mark.parametrize("cursor", [-1, True, 1.0, "0", 2])
def test_replay_batch_rejects_invalid_or_ahead_cursor(cursor: object) -> None:
    """A malformed cursor must not skip a missing event."""

    with pytest.raises(W3RecoveryPayloadError) as raised:
        make_replay_batch(_history(_observation(1)), after_cursor=cursor)

    assert raised.value.code == "INVALID_CURSOR"


def test_snapshot_keeps_latest_state_per_id_in_revision_order() -> None:
    """Keeping the first event for a state ID would regress W3's current view."""

    version_a, version_b = uuid4(), uuid4()
    restriction_a, restriction_b = uuid4(), uuid4()
    later = NOW + timedelta(hours=1)
    events = (
        _version(1, version_id=version_a),
        _version(2, version_id=version_a),
        _version(3, version_id=version_b),
        _restriction(4, restriction_id=restriction_a, restriction_revision=1),
        _restriction(5, restriction_id=restriction_a, restriction_revision=2, status="cleared"),
        _restriction(6, restriction_id=restriction_b, restriction_revision=3),
        _observation(7),
        _observation(8, occurred_at=later),
    )

    snapshot = make_snapshot(_history(*events, restriction_revision=3, as_of=NOW))

    assert set(snapshot) == {
        "schema_version",
        "source_id",
        "as_of",
        "event_cursor",
        "restriction_revision",
        "complete",
        "versions",
        "restrictions",
        "observation",
    }
    assert snapshot["schema_version"] == "w3-c01/0.2-candidate"
    assert snapshot["source_id"] == str(SOURCE_ID)
    assert snapshot["event_cursor"] == 8
    assert snapshot["restriction_revision"] == 3
    assert snapshot["complete"] is True
    assert [event["revision"] for event in snapshot["versions"]] == [2, 3]
    assert [event["revision"] for event in snapshot["restrictions"]] == [5, 6]
    assert snapshot["restrictions"][0]["payload"]["restriction_status"] == "cleared"
    assert snapshot["observation"]["revision"] == 8
    assert snapshot["as_of"] == later.isoformat()
    included = [*snapshot["versions"], *snapshot["restrictions"], snapshot["observation"]]
    assert all(datetime.fromisoformat(event["occurred_at"]) <= later for event in included)
    assert "job_id" not in json.dumps(snapshot)
    assert "authenticated_owner_ref" not in json.dumps(snapshot)


def test_snapshot_allows_null_observation_and_500_distinct_versions() -> None:
    """Exactly 500 current Version IDs are allowed even without observation."""

    events = tuple(_version(revision, version_id=uuid4()) for revision in range(1, 501))

    snapshot = make_snapshot(_history(*events))

    assert snapshot["observation"] is None
    assert len(snapshot["versions"]) == 500
    assert snapshot["restrictions"] == []


def test_snapshot_rejects_501_distinct_versions_but_not_501_events_for_one_id() -> None:
    """The W3 limit applies to current states, not to retained history length."""

    one_version = uuid4()
    repeated = tuple(_version(revision, version_id=one_version) for revision in range(1, 502))
    distinct = tuple(_version(revision, version_id=uuid4()) for revision in range(1, 502))

    assert len(make_snapshot(_history(*repeated))["versions"]) == 1
    with pytest.raises(W3RecoveryPayloadError) as raised:
        make_snapshot(_history(*distinct))
    assert raised.value.code == "SNAPSHOT_TOO_LARGE"


def test_snapshot_rejects_501_distinct_restrictions() -> None:
    """Sending a truncated restriction set would falsely claim complete recovery."""

    events = tuple(
        _restriction(revision, restriction_id=uuid4(), restriction_revision=revision)
        for revision in range(1, 502)
    )

    with pytest.raises(W3RecoveryPayloadError) as raised:
        make_snapshot(_history(*events, restriction_revision=501))

    assert raised.value.code == "SNAPSHOT_TOO_LARGE"


def test_snapshot_allows_500_distinct_restrictions() -> None:
    """The exact W3 restriction-state bound must remain usable."""

    events = tuple(
        _restriction(revision, restriction_id=uuid4(), restriction_revision=revision)
        for revision in range(1, 501)
    )

    snapshot = make_snapshot(_history(*events, restriction_revision=500))

    assert len(snapshot["restrictions"]) == 500
    assert snapshot["restriction_revision"] == 500


@pytest.mark.parametrize(
    "mutate",
    [
        lambda history: replace(history, high_watermark=0),
        lambda history: replace(history, high_watermark=2),
        lambda history: replace(history, events=()),
        lambda history: replace(
            history, events=(history.events[0].model_copy(update={"aggregate_revision": 2}),)
        ),
        lambda history: replace(history, source_id=uuid4()),
        lambda history: replace(history, as_of=NOW.replace(tzinfo=None)),
        lambda history: replace(history, restriction_revision=1),
    ],
)
def test_snapshot_rejects_malformed_or_incomplete_history(mutate) -> None:
    """An incomplete read must never be sent as an atomic complete snapshot."""

    history = mutate(_history(_observation(1)))

    with pytest.raises(W3RecoveryPayloadError) as raised:
        make_snapshot(history)

    assert raised.value.code == "INVALID_HISTORY"


def test_snapshot_rejects_restriction_revision_gap() -> None:
    """The restriction watermark cannot hide a missing independent revision."""

    event = _restriction(1, restriction_id=uuid4(), restriction_revision=2)

    with pytest.raises(W3RecoveryPayloadError) as raised:
        make_snapshot(_history(event, restriction_revision=2))

    assert raised.value.code == "INVALID_HISTORY"


def test_snapshot_sanitizes_a_corrupt_event_payload() -> None:
    """Malformed in-memory history must not leak the original payload in errors."""

    private_value = "SYNTHETIC_PRIVATE_VALUE"
    corrupt = _observation(1).model_copy(update={"payload": {"private": private_value}})

    with pytest.raises(W3RecoveryPayloadError) as raised:
        make_snapshot(_history(corrupt))

    assert raised.value.code == "INVALID_HISTORY"
    assert private_value not in str(raised.value)
