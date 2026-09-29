"""Bounded W2 recovery transport at the pinned W3 C-01 HTTP boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest

from epick_engine.source_collection.w3_public_transport import W3HTTPResponse
from epick_engine.source_collection.w3_recovery_transport import (
    W3RecoveryClient,
    W3RecoveryTransportError,
)

SOURCE_ID = UUID("11111111-1111-1111-1111-111111111111")
TOKEN = "synthetic-w2-recovery-token"


def _status_body(
    source_id: UUID = SOURCE_ID,
    *,
    outcome: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "w3-c01/0.2-candidate",
        "source_id": str(source_id),
        "event_cursor": 3,
        "required_event_cursor": 3,
        "restriction_revision": 1,
        "required_restriction_revision": 1,
        "generation": 4,
        "restriction_scope": "version",
        "reason": "INDEX_PENDING",
        "index_ack": False,
        "index_key": None,
        "history_complete": True,
    }
    if outcome is not None:
        value["outcome"] = outcome
    return value


def _response(
    value: object,
    *,
    status: int = 200,
    content_type: str | None = "application/json; charset=utf-8",
) -> W3HTTPResponse:
    body = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
    return W3HTTPResponse(status=status, content_type=content_type, body=body)


@dataclass
class RecordingTransport:
    get_response: W3HTTPResponse = field(default_factory=lambda: _response(_status_body()))
    post_response: W3HTTPResponse = field(
        default_factory=lambda: _response(_status_body(outcome="INCOMPLETE"))
    )
    get_error: Exception | None = None
    post_error: Exception | None = None
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def get(self, **kwargs: object) -> W3HTTPResponse:
        self.calls.append(("GET", kwargs))
        if self.get_error is not None:
            raise self.get_error
        return self.get_response

    def post(self, **kwargs: object) -> W3HTTPResponse:
        self.calls.append(("POST", kwargs))
        if self.post_error is not None:
            raise self.post_error
        return self.post_response


def _client(transport: RecordingTransport) -> W3RecoveryClient:
    return W3RecoveryClient(
        endpoint="https://w3.example.test/c01/v1/events",
        bearer_token=TOKEN,
        transport=transport,
    )


def test_status_uses_authenticated_fixed_target_and_returns_validated_metadata() -> None:
    transport = RecordingTransport()

    status = _client(transport).status(source_id=SOURCE_ID)

    assert status.source_id == SOURCE_ID
    assert status.event_cursor == 3
    assert status.required_event_cursor == 3
    assert status.restriction_revision == 1
    assert status.required_restriction_revision == 1
    assert status.outcome is None
    assert status.reason == "INDEX_PENDING"
    assert status.history_complete is True
    assert status.index_ack is False
    assert transport.calls == [
        (
            "GET",
            {
                "scheme": "https",
                "host": "w3.example.test",
                "port": 443,
                "target": f"/c01/v1/status/{SOURCE_ID}",
                "headers": {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"},
                "timeout": 5.0,
                "ssl_context": None,
                "max_response_bytes": 65_536,
            },
        )
    ]


@pytest.mark.parametrize(
    ("method", "outcome", "target", "operation"),
    [
        (
            "replay",
            "INCOMPLETE",
            "/c01/v1/replay",
            lambda client: client.replay(
                source_id=SOURCE_ID,
                batch={
                    "schema_version": "w3-c01/0.2-candidate",
                    "source_id": str(SOURCE_ID),
                    "events": [],
                },
            ),
        ),
        (
            "snapshot",
            "SNAPSHOT_APPLIED",
            "/c01/v1/snapshot",
            lambda client: client.snapshot(
                source_id=SOURCE_ID,
                payload={
                    "schema_version": "w3-c01/0.2-candidate",
                    "source_id": str(SOURCE_ID),
                    "complete": True,
                },
            ),
        ),
    ],
)
def test_recovery_posts_only_to_fixed_target_and_accepts_endpoint_outcome(
    method: str,
    outcome: str,
    target: str,
    operation: object,
) -> None:
    transport = RecordingTransport(post_response=_response(_status_body(outcome=outcome)))

    status = operation(_client(transport))

    assert status.outcome == outcome
    assert len(transport.calls) == 1
    recorded_method, call = transport.calls[0]
    assert recorded_method == "POST"
    assert call["target"] == target
    assert call["headers"] == {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    assert json.loads(call["body"])["schema_version"] == "w3-c01/0.2-candidate"
    assert method in {"replay", "snapshot"}


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://w3.example.test/c01/v1/events",
        "https://user@w3.example.test/c01/v1/events",
        "https://w3.example.test/c01/v1/events?token=bad",
        "https://w3.example.test/c01/v1/replay",
    ],
)
def test_recovery_client_rejects_untrusted_event_endpoint_without_leaking_values(
    endpoint: str,
) -> None:
    with pytest.raises(W3RecoveryTransportError) as raised:
        W3RecoveryClient(endpoint=endpoint, bearer_token=TOKEN)

    assert raised.value.code == "INVALID_CONFIGURATION"
    assert TOKEN not in str(raised.value)
    assert "w3.example.test" not in str(raised.value)


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (lambda _: b"[]", "INVALID_RESPONSE"),
        (lambda _: _status_body() | {"required_event_cursor": None}, "INVALID_RESPONSE"),
        (lambda _: _status_body(uuid4()), "INVALID_RESPONSE"),
        (lambda _: _status_body() | {"schema_version": "wrong"}, "INVALID_RESPONSE"),
        (lambda _: _status_body() | {"event_cursor": True}, "INVALID_RESPONSE"),
        (lambda _: _status_body() | {"restriction_scope": "source"}, "INVALID_RESPONSE"),
        (lambda _: _status_body() | {"index_ack": 0}, "INVALID_RESPONSE"),
        (lambda _: _status_body() | {"history_complete": "yes"}, "INVALID_RESPONSE"),
        (lambda _: _status_body() | {"generation": -1}, "INVALID_RESPONSE"),
    ],
)
def test_status_rejects_malformed_contract_response_without_leaking_body(
    mutate: object,
    expected_code: str,
) -> None:
    response_value = mutate(_status_body())
    transport = RecordingTransport(get_response=_response(response_value))

    with pytest.raises(W3RecoveryTransportError) as raised:
        _client(transport).status(source_id=SOURCE_ID)

    assert raised.value.code == expected_code
    assert TOKEN not in str(raised.value)
    assert "wrong" not in str(raised.value)


@pytest.mark.parametrize(
    "response",
    [
        W3HTTPResponse(status=302, content_type="application/json", body=b"{}"),
        W3HTTPResponse(status=409, content_type="application/json", body=b'{"error":"CONFLICT"}'),
        W3HTTPResponse(
            status=422,
            content_type="application/json",
            body=b'{"error":"SOURCE_NOT_REGISTERED"}',
        ),
        W3HTTPResponse(
            status=503,
            content_type="application/json",
            body=b'{"error":"STORAGE_UNAVAILABLE"}',
        ),
        W3HTTPResponse(status=200, content_type="text/plain", body=b"untrusted-response-body"),
        W3HTTPResponse(status=200, content_type="application/json", body=b"x" * 65_537),
    ],
)
def test_status_rejects_http_or_response_boundaries_without_leaking_response(
    response: W3HTTPResponse,
) -> None:
    transport = RecordingTransport(get_response=response)

    with pytest.raises(W3RecoveryTransportError) as raised:
        _client(transport).status(source_id=SOURCE_ID)

    assert raised.value.code in {"REMOTE_REJECTED", "INVALID_RESPONSE"}
    assert TOKEN not in str(raised.value)
    assert "untrusted-response-body" not in str(raised.value)


def test_status_sanitizes_timeout() -> None:
    transport = RecordingTransport(get_error=TimeoutError("untrusted-response-body"))

    with pytest.raises(W3RecoveryTransportError) as raised:
        _client(transport).status(source_id=SOURCE_ID)

    assert raised.value.code == "REQUEST_FAILED"
    assert TOKEN not in str(raised.value)
    assert "untrusted-response-body" not in str(raised.value)


def test_replay_rejects_oversized_request_before_calling_transport() -> None:
    transport = RecordingTransport()

    with pytest.raises(W3RecoveryTransportError) as raised:
        _client(transport).replay(source_id=SOURCE_ID, batch={"padding": "x" * 2_000_000})

    assert raised.value.code == "REQUEST_TOO_LARGE"
    assert transport.calls == []


@pytest.mark.parametrize("operation", ["replay", "snapshot"])
def test_recovery_post_rejects_payload_for_different_source_before_transport(
    operation: str,
) -> None:
    transport = RecordingTransport()
    client = _client(transport)
    wrong_source_id = uuid4()

    with pytest.raises(W3RecoveryTransportError) as raised:
        if operation == "replay":
            client.replay(
                source_id=SOURCE_ID,
                batch={"source_id": str(wrong_source_id)},
            )
        else:
            client.snapshot(
                source_id=SOURCE_ID,
                payload={"source_id": str(wrong_source_id)},
            )

    assert raised.value.code == "SOURCE_MISMATCH"
    assert transport.calls == []


@pytest.mark.parametrize(
    ("operation", "outcome"),
    [
        ("replay", "SNAPSHOT_APPLIED"),
        ("snapshot", "REPLAYED"),
    ],
)
def test_recovery_post_rejects_outcome_for_other_endpoint(operation: str, outcome: str) -> None:
    transport = RecordingTransport(post_response=_response(_status_body(outcome=outcome)))
    client = _client(transport)

    with pytest.raises(W3RecoveryTransportError) as raised:
        if operation == "replay":
            client.replay(source_id=SOURCE_ID, batch={"source_id": str(SOURCE_ID)})
        else:
            client.snapshot(source_id=SOURCE_ID, payload={"source_id": str(SOURCE_ID)})

    assert raised.value.code == "INVALID_RESPONSE"
