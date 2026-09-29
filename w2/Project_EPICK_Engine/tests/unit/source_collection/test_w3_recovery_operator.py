"""Fail-closed W2 recovery control over W3's durable cursor."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from epick_engine.source_collection import w3_recovery_operator
from epick_engine.source_collection.contracts import (
    SourceEvent,
    SourceEventType,
    SourceObservationSnapshot,
)
from epick_engine.source_collection.w3_recovery_operator import (
    RecoveryResult,
    W3RecoveryOperationError,
    W3SourceRecovery,
)
from epick_engine.source_collection.w3_recovery_store import RecoveryHistory
from epick_engine.source_collection.w3_recovery_transport import W3RecoveryStatus

SOURCE_ID = UUID("00000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 9, 21, tzinfo=UTC)


def _event(revision: int) -> SourceEvent:
    return SourceEvent(
        event_id=uuid4(),
        event_type=SourceEventType.OBSERVATION_CHANGED,
        schema_version="w2.source.v1",
        aggregate_id=SOURCE_ID,
        aggregate_revision=revision,
        occurred_at=NOW,
        payload=SourceObservationSnapshot(
            observation_id=uuid4(),
            source_id=SOURCE_ID,
            source_version_id=None,
            policy_decision_id=None,
            observed_at=NOW,
            access_class="public",
            acquisition_status="AVAILABLE",
            http_status=200,
            checked_url="https://careers.example.test/jobs/platform-engineer",
            error_code=None,
            representation="static_html",
        ),
    )


def _history(high_watermark: int, *, restriction_revision: int = 0) -> RecoveryHistory:
    return RecoveryHistory(
        source_id=SOURCE_ID,
        high_watermark=high_watermark,
        restriction_revision=restriction_revision,
        events=tuple(_event(revision) for revision in range(1, high_watermark + 1)),
        as_of=NOW,
    )


def _status(
    *,
    source_id: UUID = SOURCE_ID,
    event_cursor: int = 0,
    required_event_cursor: int | None = None,
    restriction_revision: int = 0,
    required_restriction_revision: int | None = None,
    outcome: str | None = None,
    reason: str = "INDEX_PENDING",
    history_complete: bool = False,
    index_ack: bool = False,
) -> W3RecoveryStatus:
    return W3RecoveryStatus(
        source_id=source_id,
        event_cursor=event_cursor,
        required_event_cursor=(
            event_cursor if required_event_cursor is None else required_event_cursor
        ),
        restriction_revision=restriction_revision,
        required_restriction_revision=(
            restriction_revision
            if required_restriction_revision is None
            else required_restriction_revision
        ),
        outcome=outcome,
        reason=reason,
        history_complete=history_complete,
        index_ack=index_ack,
    )


@dataclass
class RecordingStore:
    history: RecoveryHistory
    source_ids: list[UUID] = field(default_factory=list)

    def load_history(self, *, source_id: UUID) -> RecoveryHistory:
        self.source_ids.append(source_id)
        return self.history


@dataclass
class RecordingClient:
    statuses: list[W3RecoveryStatus]
    replay_responses: list[W3RecoveryStatus] = field(default_factory=list)
    snapshot_responses: list[W3RecoveryStatus] = field(default_factory=list)
    replay_error: Exception | None = None
    status_source_ids: list[UUID] = field(default_factory=list)
    replay_bodies: list[dict[str, object]] = field(default_factory=list)
    snapshot_bodies: list[dict[str, object]] = field(default_factory=list)

    def status(self, *, source_id: UUID) -> W3RecoveryStatus:
        self.status_source_ids.append(source_id)
        if not self.statuses:
            raise AssertionError("unexpected W3 status request")
        return self.statuses.pop(0)

    def replay(self, *, source_id: UUID, batch: dict[str, object]) -> W3RecoveryStatus:
        assert source_id == SOURCE_ID
        self.replay_bodies.append(batch)
        if self.replay_error is not None:
            raise self.replay_error
        if not self.replay_responses:
            raise AssertionError("unexpected W3 replay request")
        return self.replay_responses.pop(0)

    def snapshot(self, *, source_id: UUID, payload: dict[str, object]) -> W3RecoveryStatus:
        assert source_id == SOURCE_ID
        self.snapshot_bodies.append(payload)
        if not self.snapshot_responses:
            raise AssertionError("unexpected W3 snapshot request")
        return self.snapshot_responses.pop(0)


def _recovery(
    history: RecoveryHistory,
    client: RecordingClient,
) -> W3SourceRecovery:
    return W3SourceRecovery(store=RecordingStore(history), client=client)


def test_replay_uses_only_durable_cursor_and_refetches_before_next_page() -> None:
    """The second page must start at the post-commit W3 cursor, never a local estimate."""

    client = RecordingClient(
        statuses=[_status(), _status(event_cursor=500)],
        replay_responses=[
            _status(event_cursor=500, outcome="INCOMPLETE"),
            _status(event_cursor=501, outcome="REPLAYED"),
        ],
    )

    result = _recovery(_history(501), client).recover(source_id=SOURCE_ID, mode="replay")

    assert [body["after_cursor"] for body in client.replay_bodies] == [0, 500]
    assert client.status_source_ids == [SOURCE_ID, SOURCE_ID]
    assert result == RecoveryResult(
        kind="REPLAYED",
        source_id=SOURCE_ID,
        event_cursor=501,
        restriction_revision=0,
    )


def test_replay_resumes_from_fresh_w3_cursor_without_mutating_history() -> None:
    """A retried command starts from W3's new durable state and does not write W2 history."""

    history = _history(501)
    original_events = history.events
    client = RecordingClient(
        statuses=[_status(event_cursor=500)],
        replay_responses=[_status(event_cursor=501, outcome="REPLAYED")],
    )

    result = _recovery(history, client).recover(source_id=SOURCE_ID, mode="replay")

    assert [body["after_cursor"] for body in client.replay_bodies] == [500]
    assert history.events is original_events
    assert result.event_cursor == 501


def test_replay_already_current_does_not_post_or_claim_index_readiness() -> None:
    """Cursor equality is sufficient for recovery, not for W3 index readiness."""

    client = RecordingClient(
        statuses=[
            _status(
                event_cursor=1,
                restriction_revision=0,
                reason="INDEX_PENDING",
                history_complete=False,
                index_ack=False,
            )
        ]
    )

    result = _recovery(_history(1), client).recover(source_id=SOURCE_ID, mode="replay")

    assert client.replay_bodies == []
    assert result.kind == "ALREADY_CURRENT"
    assert result.event_cursor == 1


@pytest.mark.parametrize(
    "remote",
    [
        _status(source_id=uuid4()),
        _status(event_cursor=2),
        _status(required_event_cursor=2),
        _status(restriction_revision=1),
        _status(required_restriction_revision=1),
        _status(reason="CONFLICT"),
    ],
)
def test_replay_rejects_unsafe_remote_status_before_post(remote: W3RecoveryStatus) -> None:
    """A foreign, ahead, conflicted, or unknown W3 state never receives a recovery page."""

    client = RecordingClient(statuses=[remote])

    with pytest.raises(W3RecoveryOperationError):
        _recovery(_history(1), client).recover(source_id=SOURCE_ID, mode="replay")

    assert client.replay_bodies == []


def test_empty_w3_status_replays_registered_history_after_authority_validation() -> None:
    """UNKNOWN_SOURCE is an empty W3 state, not an immutable conflict with W2 history."""

    client = RecordingClient(
        statuses=[_status(reason="UNKNOWN_SOURCE", history_complete=True)],
        replay_responses=[_status(event_cursor=1, outcome="REPLAYED")],
    )

    result = _recovery(_history(1), client).recover(source_id=SOURCE_ID, mode="replay")

    assert [body["after_cursor"] for body in client.replay_bodies] == [0]
    assert result.kind == "REPLAYED"


def test_empty_w3_status_allows_explicit_snapshot_after_authority_validation() -> None:
    """An operator-requested snapshot may initialize W3 after its authority check."""

    client = RecordingClient(
        statuses=[_status(reason="UNKNOWN_SOURCE", history_complete=True)],
        snapshot_responses=[_status(event_cursor=1, outcome="SNAPSHOT_APPLIED")],
    )

    result = _recovery(_history(1), client).recover(source_id=SOURCE_ID, mode="snapshot")

    assert len(client.snapshot_bodies) == 1
    assert result.kind == "SNAPSHOT_APPLIED"


def test_versionless_w3_status_can_advance_an_unknown_source_cursor() -> None:
    """W3 keeps UNKNOWN_SOURCE after observation-only replay without a Source Version."""

    client = RecordingClient(
        statuses=[
            _status(
                event_cursor=1,
                reason="UNKNOWN_SOURCE",
                history_complete=True,
            )
        ],
        replay_responses=[
            _status(
                event_cursor=2,
                outcome="REPLAYED",
                reason="UNKNOWN_SOURCE",
                history_complete=True,
            )
        ],
    )

    result = _recovery(_history(2), client).recover(source_id=SOURCE_ID, mode="replay")

    assert [body["after_cursor"] for body in client.replay_bodies] == [1]
    assert result.kind == "REPLAYED"
    assert result.event_cursor == 2


def test_empty_local_history_never_declares_current_from_empty_w3_status() -> None:
    """No authoritative W2 event history is never converted into ALREADY_CURRENT."""

    client = RecordingClient(statuses=[_status(reason="UNKNOWN_SOURCE", history_complete=True)])

    with pytest.raises(W3RecoveryOperationError) as raised:
        _recovery(_history(0), client).recover(source_id=SOURCE_ID, mode="replay")

    assert raised.value.code == "LOCAL_HISTORY_UNSAFE"
    assert client.status_source_ids == []
    assert client.replay_bodies == []


def test_replay_rejects_no_progress_and_stops_before_page_two() -> None:
    """An INCOMPLETE receipt without a cursor advance is not safely retryable in-process."""

    client = RecordingClient(
        statuses=[_status()],
        replay_responses=[_status(event_cursor=0, outcome="INCOMPLETE")],
    )

    with pytest.raises(W3RecoveryOperationError) as raised:
        _recovery(_history(501), client).recover(source_id=SOURCE_ID, mode="replay")

    assert raised.value.code == "REPLAY_NO_PROGRESS"
    assert [body["after_cursor"] for body in client.replay_bodies] == [0]
    assert client.status_source_ids == [SOURCE_ID]


def test_replay_does_not_automatically_fallback_to_snapshot() -> None:
    """SNAPSHOT_REQUIRED is an explicit operator decision, never a replay side effect."""

    client = RecordingClient(
        statuses=[_status()],
        replay_responses=[_status(event_cursor=0, outcome="SNAPSHOT_REQUIRED")],
    )

    with pytest.raises(W3RecoveryOperationError) as raised:
        _recovery(_history(1), client).recover(source_id=SOURCE_ID, mode="replay")

    assert raised.value.code == "SNAPSHOT_REQUIRED"
    assert client.snapshot_bodies == []


def test_replay_failure_stops_before_a_second_page() -> None:
    """A transport failure leaves retry ownership to the next fresh operator invocation."""

    client = RecordingClient(statuses=[_status()], replay_error=RuntimeError("transport failed"))

    with pytest.raises(RuntimeError, match="transport failed"):
        _recovery(_history(501), client).recover(source_id=SOURCE_ID, mode="replay")

    assert [body["after_cursor"] for body in client.replay_bodies] == [0]
    assert client.status_source_ids == [SOURCE_ID]


@pytest.mark.parametrize(
    "remote",
    [
        _status(event_cursor=2),
        _status(required_event_cursor=2),
        _status(restriction_revision=1),
        _status(required_restriction_revision=1),
    ],
)
def test_snapshot_rejects_remote_watermarks_ahead_of_local_history(
    remote: W3RecoveryStatus,
) -> None:
    """W2 cannot safely replace a W3 state whose known watermark is newer."""

    client = RecordingClient(statuses=[remote])

    with pytest.raises(W3RecoveryOperationError):
        _recovery(_history(1), client).recover(source_id=SOURCE_ID, mode="snapshot")

    assert client.snapshot_bodies == []


def test_explicit_snapshot_requires_exact_receipt_but_not_index_ack() -> None:
    """A committed snapshot is recovery success even while W3 indexing remains pending."""

    client = RecordingClient(
        statuses=[_status()],
        snapshot_responses=[
            _status(
                event_cursor=1,
                restriction_revision=0,
                outcome="SNAPSHOT_APPLIED",
                history_complete=False,
                index_ack=False,
            )
        ],
    )

    result = _recovery(_history(1), client).recover(source_id=SOURCE_ID, mode="snapshot")

    assert client.replay_bodies == []
    assert len(client.snapshot_bodies) == 1
    assert client.snapshot_bodies[0]["complete"] is True
    assert result == RecoveryResult(
        kind="SNAPSHOT_APPLIED",
        source_id=SOURCE_ID,
        event_cursor=1,
        restriction_revision=0,
    )


def test_snapshot_rejects_non_exact_receipt() -> None:
    """A stale or partial snapshot receipt cannot be represented as recovery success."""

    client = RecordingClient(
        statuses=[_status()],
        snapshot_responses=[_status(event_cursor=0, outcome="SNAPSHOT_APPLIED")],
    )

    with pytest.raises(W3RecoveryOperationError) as raised:
        _recovery(_history(1), client).recover(source_id=SOURCE_ID, mode="snapshot")

    assert raised.value.code == "SNAPSHOT_RECEIPT_UNSAFE"


@pytest.mark.parametrize(
    ("history_complete", "index_ack", "reason"),
    [
        (True, False, "INDEX_PENDING"),
        (False, True, "READY"),
    ],
)
def test_snapshot_rejects_receipt_that_claims_history_or_index_ready(
    history_complete: bool,
    index_ack: bool,
    reason: str,
) -> None:
    """Snapshot application does not establish retained history or index readiness."""

    client = RecordingClient(
        statuses=[_status()],
        snapshot_responses=[
            _status(
                event_cursor=1,
                outcome="SNAPSHOT_APPLIED",
                history_complete=history_complete,
                index_ack=index_ack,
                reason=reason,
            )
        ],
    )

    with pytest.raises(W3RecoveryOperationError) as raised:
        _recovery(_history(1), client).recover(source_id=SOURCE_ID, mode="snapshot")

    assert raised.value.code == "SNAPSHOT_RECEIPT_UNSAFE"


def test_recovery_requires_uuid_source_and_known_mode() -> None:
    """The public control surface never guesses source identity or destructive mode."""

    client = RecordingClient(statuses=[])
    recovery = _recovery(_history(1), client)

    with pytest.raises(W3RecoveryOperationError) as source_error:
        recovery.recover(source_id="not-a-uuid", mode="replay")  # type: ignore[arg-type]
    with pytest.raises(W3RecoveryOperationError) as mode_error:
        recovery.recover(source_id=SOURCE_ID, mode="automatic")  # type: ignore[arg-type]

    assert source_error.value.code == "INVALID_SOURCE_ID"
    assert mode_error.value.code == "INVALID_MODE"
    assert client.status_source_ids == []


def test_cli_wires_only_declared_dependencies_and_emits_safe_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The command exposes only recovery status, Source ID, and cursor."""

    engine = MagicMock()
    session_factory = object()
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            captured["client"] = kwargs

    class FakeStore:
        def __init__(self, **kwargs: object) -> None:
            captured["store"] = kwargs

    class FakeRecovery:
        def __init__(self, **kwargs: object) -> None:
            captured["recovery"] = kwargs

        def recover(
            self, *, source_id: UUID, mode: Literal["replay", "snapshot"]
        ) -> RecoveryResult:
            captured["recover"] = (source_id, mode)
            return RecoveryResult("REPLAYED", source_id, 7, 3)

    monkeypatch.setenv("EPICK_W3_EVENT_ENDPOINT", "https://w3.example.test/c01/v1/events")
    monkeypatch.setenv("EPICK_W3_W2_TOKEN", "synthetic-token")
    monkeypatch.setattr(w3_recovery_operator, "W3RecoveryClient", FakeClient)
    monkeypatch.setattr(w3_recovery_operator, "SqlAlchemyRecoveryHistoryStore", FakeStore)
    monkeypatch.setattr(w3_recovery_operator, "W3SourceRecovery", FakeRecovery)
    monkeypatch.setattr(w3_recovery_operator, "database_url_from_environment", lambda: "safe-url")
    monkeypatch.setattr(w3_recovery_operator, "create_database_engine", lambda _: engine)
    monkeypatch.setattr(w3_recovery_operator, "create_session_factory", lambda _: session_factory)

    assert w3_recovery_operator.main(["--source-id", str(SOURCE_ID), "--mode", "replay"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "event_cursor": 7,
        "source_id": str(SOURCE_ID),
        "status": "REPLAYED",
    }
    assert captured["recover"] == (SOURCE_ID, "replay")
    assert engine.dispose.call_count == 1


def test_cli_sanitizes_failure_and_disposes_created_engine(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Configuration or transport diagnostics must not print a token or database value."""

    engine = MagicMock()
    monkeypatch.setenv("EPICK_W3_W2_TOKEN", "CANARY_SECRET")
    monkeypatch.setattr(w3_recovery_operator, "database_url_from_environment", lambda: "safe-url")
    monkeypatch.setattr(w3_recovery_operator, "create_database_engine", lambda _: engine)
    monkeypatch.setattr(
        w3_recovery_operator,
        "W3RecoveryClient",
        lambda **_: (_ for _ in ()).throw(RuntimeError("CANARY_SECRET")),
    )

    assert w3_recovery_operator.main(["--source-id", str(SOURCE_ID)]) == 1

    output = capsys.readouterr().out
    assert json.loads(output) == {"status": "W3_RECOVERY_FAILED"}
    assert "CANARY_SECRET" not in output
    assert engine.dispose.call_count == 0


@pytest.mark.parametrize(
    ("argv", "untrusted_value"),
    [
        (["--source-id", "CANARY_INVALID_SOURCE"], "CANARY_INVALID_SOURCE"),
        (
            ["--source-id", str(SOURCE_ID), "--mode", "CANARY_INVALID_MODE"],
            "CANARY_INVALID_MODE",
        ),
    ],
)
def test_cli_argument_failures_emit_only_constant_sanitized_json(
    argv: list[str],
    untrusted_value: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Argument parsing is inside the same no-diagnostics boundary as runtime failures."""

    assert w3_recovery_operator.main(argv) == 1

    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "W3_RECOVERY_FAILED"}
    assert captured.err == ""
    assert untrusted_value not in captured.out
    assert untrusted_value not in captured.err
