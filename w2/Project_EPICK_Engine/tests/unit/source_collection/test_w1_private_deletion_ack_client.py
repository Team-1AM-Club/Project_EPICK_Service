"""Offline tests for W1's authenticated private-deletion ACK callback."""

from __future__ import annotations

import importlib
import importlib.util
import json
import ssl
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from epick_engine.source_collection import persistence, private_deletion_v2, w1_lookup_client
from epick_engine.source_collection.private_deletion_v2 import (
    PrivateDeletionAckV2,
    PrivateDeletionCommandV2,
    PrivateDeletionScope,
)

ENDPOINT = "https://w1-private.example.test/internal/v1/w2-private/deletion/ack"
BEARER = "synthetic-ack-bearer"
DELETION_ID = UUID("10000000-0000-4000-8000-000000000001")
OWNER_ID = UUID("20000000-0000-4000-8000-000000000001")
PROJECT_ID = UUID("30000000-0000-4000-8000-000000000001")


def _client_module() -> ModuleType:
    name = "epick_engine.source_collection.w1_private_deletion_ack_client"
    assert importlib.util.find_spec(name) is not None, "private deletion ACK client is missing"
    return importlib.import_module(name)


class _RecordingTransport:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        return self.response


def _response(
    payload: dict[str, object] | bytes,
    *,
    status: int = 200,
    content_type: str | None = "application/json; charset=utf-8",
) -> object:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return w1_lookup_client.LookupHTTPResponse(
        status=status,
        content_type=content_type,
        body=body,
    )


def _acknowledgement() -> PrivateDeletionAckV2:
    return PrivateDeletionAckV2(
        deletion_id=DELETION_ID,
        owner_user_id=OWNER_ID,
        deletion_epoch=7,
        scope=PrivateDeletionScope(kind="PROJECT", project_id=PROJECT_ID),
        outcome="APPLIED",
    )


class _CommitTrackingTransaction:
    def __init__(self, session: object | None = None) -> None:
        self.session = object() if session is None else session
        self.committed = False

    def __enter__(self) -> object:
        return self.session

    def __exit__(self, exception_type: object, exception: object, traceback: object) -> None:
        self.committed = exception_type is None


class _CommitTrackingSessionFactory:
    def __init__(self, receipt: object) -> None:
        self.transaction = _CommitTrackingTransaction()
        self.confirmation_transaction = _CommitTrackingTransaction(
            SimpleNamespace(scalar=lambda _query: receipt)
        )
        self.begin_count = 0

    def begin(self) -> _CommitTrackingTransaction:
        self.begin_count += 1
        return self.transaction if self.begin_count == 1 else self.confirmation_transaction


class _CommitObservingCallback:
    def __init__(self, transaction: _CommitTrackingTransaction) -> None:
        self.transaction = transaction
        self.acknowledgements: list[PrivateDeletionAckV2] = []

    def acknowledge(self, *, acknowledgement: PrivateDeletionAckV2) -> None:
        assert self.transaction.committed is True
        self.acknowledgements.append(acknowledgement)


def _client(
    transport: _RecordingTransport,
    *,
    endpoint: str = ENDPOINT,
    ssl_context: ssl.SSLContext | None = None,
    timeout_seconds: float = 2.5,
) -> object:
    module = _client_module()
    return module.W1PrivateDeletionAckClient(
        endpoint=endpoint,
        bearer=BEARER,
        ssl_context=ssl.create_default_context() if ssl_context is None else ssl_context,
        timeout_seconds=timeout_seconds,
        transport=transport,
    )


def test_process_sends_one_exact_ack_only_after_w2_transaction_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = PrivateDeletionCommandV2(
        deletion_id=DELETION_ID,
        owner_user_id=OWNER_ID,
        deletion_epoch=7,
        scope=PrivateDeletionScope(kind="PROJECT", project_id=PROJECT_ID),
    )
    receipt = SimpleNamespace(
        contract_version="w2.private-deletion.v2",
        command_digest=private_deletion_v2.command_digest_v2(command),
        ack_confirmed_at=None,
    )
    factory = _CommitTrackingSessionFactory(receipt)
    callback = _CommitObservingCallback(factory.transaction)

    def apply(session: object, applied_command: PrivateDeletionCommandV2) -> str:
        assert session is factory.transaction.session
        assert applied_command == command
        return "APPLIED"

    monkeypatch.setattr(persistence, "apply_private_deletion_v2", apply)

    result = private_deletion_v2.process_private_deletion_v2(  # type: ignore[arg-type]
        factory,
        command,
        callback,
    )

    assert result == _acknowledgement()
    assert callback.acknowledgements == [result]
    assert factory.confirmation_transaction.committed is True
    assert receipt.ack_confirmed_at is not None


def test_ack_client_posts_exact_v2_body_once_with_authenticated_headers() -> None:
    transport = _RecordingTransport(_response({"status": "ACKNOWLEDGED"}))
    context = ssl.create_default_context()
    client = _client(transport, ssl_context=context)

    client.acknowledge(acknowledgement=_acknowledgement())  # type: ignore[attr-defined]

    assert len(transport.calls) == 1
    call = transport.calls[0]
    expected_body = (
        b'{"schema_version":"w2.private-deletion-ack.v2",'
        b'"deletion_id":"10000000-0000-4000-8000-000000000001",'
        b'"owner_user_id":"20000000-0000-4000-8000-000000000001",'
        b'"deletion_epoch":7,"scope":{"type":"PROJECT",'
        b'"project_id":"30000000-0000-4000-8000-000000000001"},'
        b'"outcome":"APPLIED"}'
    )
    assert call == {
        "host": "w1-private.example.test",
        "port": 443,
        "target": "/internal/v1/w2-private/deletion/ack",
        "headers": {
            "Accept": "application/json",
            "Authorization": f"Bearer {BEARER}",
            "Content-Length": str(len(expected_body)),
            "Content-Type": "application/json",
            "X-EPICK-Service-Principal": "w2",
        },
        "body": expected_body,
        "timeout": 2.5,
        "ssl_context": context,
        "max_response_bytes": w1_lookup_client.MAX_LOOKUP_RESPONSE_BYTES,
    }


@pytest.mark.parametrize("status", [401, 403, 409, 503])
def test_ack_client_treats_every_non_success_as_failure_without_retry(status: int) -> None:
    payload = (
        {"code": "W2_DELETION_V2_BINDING_INVALID"}
        if status == 409
        else {"code": "INTERNAL_RETRYABLE"}
    )
    transport = _RecordingTransport(_response(payload, status=status))
    client = _client(transport)

    with pytest.raises(w1_lookup_client.W1LookupClientError) as raised:
        client.acknowledge(acknowledgement=_acknowledgement())  # type: ignore[attr-defined]

    assert raised.value.code == f"HTTP_{status}"
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "PENDING"},
        {"status": "ACKNOWLEDGED", "extra": True},
        {"code": "ACKNOWLEDGED"},
        b'{"status":"ACKNOWLEDGED","status":"ACKNOWLEDGED"}',
    ],
)
def test_ack_client_requires_exact_acknowledged_success_body(
    payload: dict[str, object] | bytes,
) -> None:
    transport = _RecordingTransport(_response(payload))
    client = _client(transport)

    with pytest.raises(w1_lookup_client.W1LookupClientError):
        client.acknowledge(acknowledgement=_acknowledgement())  # type: ignore[attr-defined]

    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://w1-private.example.test/internal/v1/w2-private/deletion/ack",
        "https://w1-private.example.test/internal/v1/job-commands/lookup",
        "https://user@w1-private.example.test/internal/v1/w2-private/deletion/ack",
        "https://w1-private.example.test/internal/v1/w2-private/deletion/ack?retry=1",
        "https://w1-private.example.test/internal/v1/w2-private/deletion/ack#fragment",
    ],
)
def test_ack_client_rejects_any_endpoint_outside_the_exact_https_target(endpoint: str) -> None:
    transport = _RecordingTransport(_response({"status": "ACKNOWLEDGED"}))

    with pytest.raises(w1_lookup_client.W1LookupClientError) as raised:
        _client(transport, endpoint=endpoint)

    assert raised.value.code == "INVALID_CONFIGURATION"
    assert transport.calls == []


@pytest.mark.parametrize("timeout_seconds", [0.0, -1.0, float("inf"), float("nan")])
def test_ack_client_rejects_unbounded_timeout(timeout_seconds: float) -> None:
    transport = _RecordingTransport(_response({"status": "ACKNOWLEDGED"}))

    with pytest.raises(w1_lookup_client.W1LookupClientError) as raised:
        _client(transport, timeout_seconds=timeout_seconds)

    assert raised.value.code == "INVALID_CONFIGURATION"
    assert transport.calls == []


def test_ack_client_rejects_unverified_tls_and_redacts_configuration() -> None:
    module = _client_module()
    transport = _RecordingTransport(_response({"status": "ACKNOWLEDGED"}))
    insecure_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    insecure_context.check_hostname = False
    insecure_context.verify_mode = ssl.CERT_NONE

    with pytest.raises(w1_lookup_client.W1LookupClientError) as raised:
        _client(transport, ssl_context=insecure_context)
    assert raised.value.code == "INVALID_CONFIGURATION"
    assert transport.calls == []

    client = _client(transport)
    rendered = repr(client)
    assert "w1-private.example.test" not in rendered
    assert BEARER not in rendered
    assert rendered == "W1PrivateDeletionAckClient(endpoint=<redacted>, bearer=<redacted>)"
    assert module.ACK_TARGET == "/internal/v1/w2-private/deletion/ack"
