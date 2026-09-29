"""Pinned W2 Source events at the concrete W3 C-01 HTTP boundary."""

from __future__ import annotations

import http.client
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from epick_engine.source_collection import w3_outbox_operator
from epick_engine.source_collection.contracts import SourceEvent
from epick_engine.source_collection.w3_public_transport import (
    W3HTTPResponse,
    W3PublicEventPublisher,
    W3PublicTransportError,
    source_event_to_w3_wire,
)

NOW = datetime(2026, 9, 21, tzinfo=UTC)


def _event(event_type: str) -> SourceEvent:
    source_id = uuid4()
    if event_type == "source.observation.changed":
        payload = {
            "observation_id": uuid4(),
            "source_id": source_id,
            "source_version_id": None,
            "policy_decision_id": None,
            "observed_at": NOW,
            "access_class": "public",
            "acquisition_status": "AVAILABLE",
            "http_status": 200,
            "checked_url": "https://example.test/source",
            "error_code": None,
            "representation": "static_html",
        }
    else:
        payload = {
            "restriction_id": uuid4(),
            "source_id": source_id,
            "source_version_id": None,
            "restriction_revision": 1,
            "restriction_status": "active",
            "accuracy_status": "error_confirmed",
            "reason_code": "CONFIRMED_ERROR",
            "changed_at": NOW,
            "replacement_ref": None,
        }
    return SourceEvent.model_validate(
        {
            "event_id": uuid4(),
            "event_type": event_type,
            "schema_version": "w2.source.v1",
            "aggregate_id": source_id,
            "aggregate_revision": 4,
            "occurred_at": NOW,
            "payload": payload,
        }
    )


@pytest.mark.parametrize("event_type", ["source.observation.changed", "source.restriction.changed"])
def test_adapter_maps_internal_event_to_w1_envelope_without_transport_fields_in_payload(
    event_type: str,
) -> None:
    event = _event(event_type)

    wire = source_event_to_w3_wire(event)

    assert wire["schema_version"] == "1.0"
    assert wire["producer"] == "w2"
    assert wire["aggregate_type"] == "source"
    assert wire["revision"] == 4
    assert wire["event_id"] == str(event.event_id)
    assert wire["aggregate_id"] == str(event.aggregate_id)
    assert wire["event_type"] == event_type
    assert wire["payload"]["schema_version"] == "w2.source.v1"
    transport_keys = {
        "event_id",
        "event_type",
        "producer",
        "aggregate_type",
        "aggregate_id",
        "revision",
    }
    assert not transport_keys & set(wire["payload"])
    assert "aggregate_revision" not in wire


@dataclass
class FakeTransport:
    response: W3HTTPResponse
    calls: list[dict[str, object]] = field(default_factory=list)

    def post(self, **kwargs: object) -> W3HTTPResponse:
        self.calls.append(kwargs)
        return self.response


def _response(event_id: UUID, *, receipt: str = "COMMITTED") -> W3HTTPResponse:
    return W3HTTPResponse(
        status=200,
        content_type="application/json; charset=utf-8",
        body=json.dumps(
            {
                "receipt": receipt,
                "event_id": str(event_id),
                "outcome": "GAP",
                "index_ack": False,
            }
        ).encode(),
    )


def test_publisher_accepts_exact_committed_receipt_not_index_ack() -> None:
    event = _event("source.restriction.changed")
    transport = FakeTransport(_response(event.event_id))
    publisher = W3PublicEventPublisher(
        endpoint="https://w3.example.test/c01/v1/events",
        bearer_token="synthetic-w2-token",
        transport=transport,
    )

    acknowledgement = publisher.publish(event)

    assert acknowledgement.event_id == event.event_id
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["target"] == "/c01/v1/events"
    assert call["headers"]["Authorization"] == "Bearer synthetic-w2-token"
    assert json.loads(call["body"])["payload"]["restriction_revision"] == 1


@pytest.mark.parametrize(
    "response_kind",
    [
        "wrong_event",
        "not_committed",
        "redirect",
        "unauthorized",
        "invalid_json",
        "invalid_shape",
        "oversized",
    ],
)
def test_publisher_rejects_uncommitted_or_invalid_response_without_leaking_token(
    response_kind: str,
) -> None:
    event = _event("source.observation.changed")
    response = _response(event.event_id)
    if response_kind == "wrong_event":
        response = _response(uuid4())
    elif response_kind == "not_committed":
        response = _response(event.event_id, receipt="PENDING")
    elif response_kind == "redirect":
        response = W3HTTPResponse(302, "application/json", b"{}")
    elif response_kind == "unauthorized":
        response = W3HTTPResponse(401, "application/json", b"{}")
    elif response_kind == "invalid_json":
        response = W3HTTPResponse(200, "application/json", b"not-json")
    elif response_kind == "invalid_shape":
        response = W3HTTPResponse(200, "application/json", b"[]")
    else:
        response = W3HTTPResponse(200, "application/json", b"x" * 65_537)
    publisher = W3PublicEventPublisher(
        endpoint="https://w3.example.test/c01/v1/events",
        bearer_token="synthetic-w2-token",
        transport=FakeTransport(response),
    )

    with pytest.raises(W3PublicTransportError) as raised:
        publisher.publish(event)
    assert "synthetic-w2-token" not in str(raised.value)
    assert "w3.example.test" not in str(raised.value)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://w3.example.test/c01/v1/events",
        "https://user@w3.example.test/c01/v1/events",
        "https://w3.example.test/c01/v1/events?token=bad",
        "https://w3.example.test/c01/v1/replay",
    ],
)
def test_publisher_rejects_untrusted_endpoint(endpoint: str) -> None:
    with pytest.raises(W3PublicTransportError):
        W3PublicEventPublisher(endpoint=endpoint, bearer_token="synthetic-w2-token")


def test_local_loopback_http_is_allowed_only_for_explicit_local_integration() -> None:
    publisher = W3PublicEventPublisher(
        endpoint="http://127.0.0.1:8764/c01/v1/events",
        bearer_token="synthetic-w2-token",
        transport=FakeTransport(_response(uuid4())),
    )
    assert publisher is not None


def test_default_https_transport_sends_one_bounded_post_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _event("source.observation.changed")
    calls: list[tuple[str, object]] = []

    class Response:
        status = 200

        def getheader(self, name: str) -> str | None:
            return "application/json" if name == "Content-Type" else None

        def read(self, size: int) -> bytes:
            calls.append(("read", size))
            return _response(event.event_id).body

    class Connection:
        def __init__(self, host: str, port: int, **kwargs: object) -> None:
            calls.append(("connect", (host, port, kwargs["timeout"])))

        def request(
            self, method: str, target: str, *, body: bytes, headers: dict[str, str]
        ) -> None:
            calls.append(("request", (method, target, json.loads(body), headers)))

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            calls.append(("close", None))

    monkeypatch.setattr(http.client, "HTTPSConnection", Connection)
    publisher = W3PublicEventPublisher(
        endpoint="https://w3.example.test/c01/v1/events",
        bearer_token="synthetic-w2-token",
    )

    assert publisher.publish(event).event_id == event.event_id

    assert calls[0] == ("connect", ("w3.example.test", 443, 5.0))
    assert calls[1][0] == "request"
    method, target, wire, headers = calls[1][1]
    assert (method, target) == ("POST", "/c01/v1/events")
    assert wire["revision"] == 4
    assert headers["Authorization"] == "Bearer synthetic-w2-token"
    assert calls[2:] == [("read", 65_537), ("close", None)]


def test_one_shot_operator_rejects_missing_endpoint_without_leaking_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("EPICK_W3_EVENT_ENDPOINT", raising=False)
    monkeypatch.setenv("EPICK_W3_W2_TOKEN", "CANARY_SECRET")
    monkeypatch.setenv("EPICK_DATABASE_URL", "postgresql+psycopg://secret:CANARY_DB@localhost/db")

    assert w3_outbox_operator.main([]) == 1

    output = capsys.readouterr().out
    assert output == '{"status":"W3_OUTBOX_FAILED"}\n'
    assert "CANARY" not in output
