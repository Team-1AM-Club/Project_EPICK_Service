"""Offline tests for the protected W1 lookup HTTPS caller."""

from __future__ import annotations

import importlib
import importlib.util
import json
import math
import ssl
from copy import deepcopy
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID

import pytest

from epick_engine.source_collection import w1_transport

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "w1_private_contract"
ENDPOINT = "https://w1-private.example.test/internal/v1/job-commands/lookup"
BEARER = "synthetic-test-bearer"


def _load(name: str) -> dict[str, Any]:
    with (FIXTURE_DIR / name).open(encoding="utf-8") as stream:
        value: dict[str, Any] = json.load(stream)
    return value


def _client_module() -> ModuleType:
    name = "epick_engine.source_collection.w1_lookup_client"
    assert importlib.util.find_spec(name) is not None, "protected W1 lookup client is missing"
    return importlib.import_module(name)


class _RecordingTransport:
    def __init__(self, response: object | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def post(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _response(
    module: ModuleType,
    payload: dict[str, Any] | bytes,
    *,
    status: int = 200,
    content_type: str | None = "application/json; charset=utf-8",
) -> object:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return module.LookupHTTPResponse(status=status, content_type=content_type, body=body)


def _dispatch() -> w1_transport.W1Dispatch:
    return w1_transport.parse_w1_dispatch(_load("private-w2-command-dispatch.json"))


def test_lookup_dispatch_posts_strict_request_and_preserves_original_dispatch() -> None:
    module = _client_module()
    dispatch = _dispatch()
    original = deepcopy(dispatch.model_dump(mode="python"))
    response_payload = _load("private-command-lookup-available.json")
    transport = _RecordingTransport(_response(module, response_payload))

    result = module.W1LookupClient(
        endpoint=ENDPOINT,
        bearer=BEARER,
        timeout_seconds=3.5,
        transport=transport,
    ).lookup_dispatch(dispatch)

    assert result.status == "AVAILABLE"
    assert result.command == dispatch.payload
    assert dispatch.model_dump(mode="python") == original
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["host"] == "w1-private.example.test"
    assert call["port"] == 443
    assert call["target"] == "/internal/v1/job-commands/lookup"
    assert call["timeout"] == 3.5
    assert call["headers"] == {
        "Accept": "application/json",
        "Authorization": f"Bearer {BEARER}",
        "Content-Length": str(len(call["body"])),
        "Content-Type": "application/json",
        "X-EPICK-Service-Principal": "w2",
    }
    assert json.loads(call["body"].decode("utf-8")) == dispatch.lookup_request.model_dump(
        mode="json"
    )
    context = call["ssl_context"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_source_onboarding_lookup_requires_exact_command_binding() -> None:
    module = _client_module()
    request = _dispatch().lookup_request
    payload = {
        "schema_version": "w1.private.source-onboarding-lookup.v1",
        "command_id": str(request.command_id),
        "status": "AVAILABLE",
        "source": {
            "source_id": str(_dispatch().payload.source_id),
            "company_id": str(_dispatch().payload.company_id),
            "canonical_url": "https://careers.example.test/job/1",
            "source_type": "CAREERS",
            "title": None,
            "company_legal_name": "Example",
            "company_official_domain": "example.test",
            "company_identification_status": "VERIFIED",
        },
    }
    transport = _RecordingTransport(_response(module, payload))
    client = module.W1LookupClient(endpoint=ENDPOINT, bearer=BEARER, transport=transport)

    response = client.lookup_source_onboarding(request)
    assert response.status == "AVAILABLE"
    assert response.source is not None
    assert str(response.source.source_id) == payload["source"]["source_id"]
    assert transport.calls[0]["target"] == module.SOURCE_ONBOARDING_TARGET
    assert json.loads(transport.calls[0]["body"]) == request.model_dump(mode="json")

    payload["command_id"] = "40000000-0000-4000-8000-000000000099"
    mismatched = _RecordingTransport(_response(module, payload))
    client = module.W1LookupClient(endpoint=ENDPOINT, bearer=BEARER, transport=mismatched)
    with pytest.raises(module.W1LookupClientError, match="COMMAND_MISMATCH"):
        client.lookup_source_onboarding(request)


def test_lookup_dispatch_revalidates_complete_dispatch_before_network() -> None:
    module = _client_module()
    dispatch = _dispatch()
    invalid = dispatch.model_copy(
        update={"payload": dispatch.payload.model_copy(update={"input_version": True})}
    )
    transport = _RecordingTransport(
        _response(module, _load("private-command-lookup-available.json"))
    )
    client = module.W1LookupClient(endpoint=ENDPOINT, bearer=BEARER, transport=transport)

    with pytest.raises(w1_transport.W1WireContractError):
        client.lookup_dispatch(invalid)

    assert transport.calls == []


def test_lookup_rejects_wrong_runtime_type_before_network() -> None:
    module = _client_module()
    transport = _RecordingTransport(
        _response(module, _load("private-command-lookup-available.json"))
    )
    client = module.W1LookupClient(endpoint=ENDPOINT, bearer=BEARER, transport=transport)

    with pytest.raises(w1_transport.W1WireContractError):
        client.lookup(None)

    assert transport.calls == []


def test_lookup_dispatch_rejects_available_command_mismatch_after_network() -> None:
    module = _client_module()
    dispatch = _dispatch()
    payload = _load("private-command-lookup-available.json")
    payload["command"]["project_ref"] = "different-project"
    transport = _RecordingTransport(_response(module, payload))
    client = module.W1LookupClient(endpoint=ENDPOINT, bearer=BEARER, transport=transport)

    with pytest.raises(w1_transport.W1WireContractError):
        client.lookup_dispatch(dispatch)

    assert len(transport.calls) == 1


def test_non_available_lookup_remains_read_only_status_data() -> None:
    module = _client_module()
    dispatch = _dispatch()
    transport = _RecordingTransport(_response(module, _load("private-command-lookup-deleted.json")))

    result = module.W1LookupClient(
        endpoint=ENDPOINT, bearer=BEARER, transport=transport
    ).lookup_dispatch(dispatch)

    assert result.status == "DELETED"
    assert result.command is None


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, "UNAUTHENTICATED_SERVICE_PRINCIPAL", False),
        (403, "FORBIDDEN_SERVICE_PRINCIPAL", False),
        (422, "INVALID_LOOKUP_REQUEST", False),
        (503, "INTERNAL_RETRYABLE", True),
    ],
)
def test_valid_w1_http_errors_propagate_without_retry(
    status: int, code: str, retryable: bool
) -> None:
    module = _client_module()
    body = {
        "schema_version": "w1.private.error.v1",
        "code": code,
        "retryable": retryable,
        "correlation_id": "40000000-0000-4000-8000-000000000001",
    }
    transport = _RecordingTransport(_response(module, body, status=status))
    client = module.W1LookupClient(endpoint=ENDPOINT, bearer=BEARER, transport=transport)

    with pytest.raises(w1_transport.W1LookupError) as caught:
        client.lookup(_dispatch().lookup_request)

    assert caught.value.http_status == status
    assert caught.value.code == code
    assert caught.value.retryable is retryable
    assert caught.value.correlation_id == UUID("40000000-0000-4000-8000-000000000001")
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b"{", "application/json"),
        (b"\xff", "application/json"),
        (
            b'{"schema_version":"w1.private.command-lookup.v1",'
            b'"command_id":"10000000-0000-4000-8000-000000000001",'
            b'"status":"DELETED","status":"DELETED",'
            b'"reason_code":"OWNER_DATA_DELETED","command":null}',
            "application/json",
        ),
        (
            b'{"schema_version":"w1.private.command-lookup.v1",'
            b'"command_id":"10000000-0000-4000-8000-000000000001",'
            b'"status":"DELETED","reason_code":NaN,"command":null}',
            "application/json",
        ),
        (b'{"private":"raw-body-marker"}', "text/html"),
        (b"{}", None),
    ],
)
def test_malformed_or_unsupported_response_is_rejected_without_data_leak(
    body: bytes,
    content_type: str | None,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _client_module()
    endpoint_marker = "private-endpoint-marker"
    bearer_marker = "private-bearer-marker"
    transport = _RecordingTransport(_response(module, body, content_type=content_type))
    client = module.W1LookupClient(
        endpoint=f"https://{endpoint_marker}.example.test/internal/v1/job-commands/lookup",
        bearer=bearer_marker,
        transport=transport,
    )

    with pytest.raises((module.W1LookupClientError, w1_transport.W1WireContractError)) as caught:
        client.lookup(_dispatch().lookup_request)

    rendered = f"{caught.value!s} {caught.value!r} {client!r}"
    for marker in (endpoint_marker, bearer_marker, "raw-body-marker"):
        assert marker not in rendered
    assert caplog.records == []
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_overflowing_json_number_is_rejected_by_strict_decoder() -> None:
    module = _client_module()
    body = (
        b'{"schema_version":"w1.private.command-lookup.v1",'
        b'"command_id":"10000000-0000-4000-8000-000000000001",'
        b'"status":"DELETED","reason_code":1e400,"command":null}'
    )
    transport = _RecordingTransport(_response(module, body))
    client = module.W1LookupClient(endpoint=ENDPOINT, bearer=BEARER, transport=transport)

    with pytest.raises(module.W1LookupClientError) as caught:
        client.lookup(_dispatch().lookup_request)

    assert caught.value.code == "INVALID_RESPONSE_BODY"


def test_oversized_response_is_rejected_before_json_decode() -> None:
    module = _client_module()
    body = b"x" * (module.MAX_LOOKUP_RESPONSE_BYTES + 1)
    transport = _RecordingTransport(_response(module, body))
    client = module.W1LookupClient(endpoint=ENDPOINT, bearer=BEARER, transport=transport)

    with pytest.raises(module.W1LookupClientError) as caught:
        client.lookup(_dispatch().lookup_request)

    assert caught.value.code == "RESPONSE_TOO_LARGE"
    assert "x" not in str(caught.value)


def test_transport_response_repr_does_not_expose_raw_body() -> None:
    module = _client_module()
    marker = b"private-response-body-marker"

    response = module.LookupHTTPResponse(
        status=200,
        content_type="application/json",
        body=marker,
    )

    assert marker.decode() not in repr(response)


def test_transport_failure_is_sanitized_and_never_retried(
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _client_module()
    endpoint_marker = "transport-endpoint-marker"
    bearer_marker = "transport-bearer-marker"
    raw_marker = "transport-raw-marker"
    transport = _RecordingTransport(
        error=RuntimeError(f"{endpoint_marker} {bearer_marker} {raw_marker}")
    )
    client = module.W1LookupClient(
        endpoint=f"https://{endpoint_marker}.example.test/internal/v1/job-commands/lookup",
        bearer=bearer_marker,
        transport=transport,
    )

    with pytest.raises(module.W1LookupClientError) as caught:
        client.lookup(_dispatch().lookup_request)

    assert caught.value.code == "TRANSPORT_FAILURE"
    assert len(transport.calls) == 1
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    rendered = f"{caught.value!s} {caught.value!r} {client!r}"
    for marker in (endpoint_marker, bearer_marker, raw_marker):
        assert marker not in rendered
    assert caplog.records == []
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://w1.example.test/internal/v1/job-commands/lookup",
        "https://w1.example.test/other",
        "https://w1.example.test/internal/v1/job-commands/lookup?debug=1",
        "https://w1.example.test/internal/v1/job-commands/lookup#fragment",
        "https://user:password@w1.example.test/internal/v1/job-commands/lookup",
        "https://w1.example.test\x7f/internal/v1/job-commands/lookup",
    ],
)
def test_client_rejects_non_exact_or_insecure_endpoint(endpoint: str) -> None:
    module = _client_module()
    with pytest.raises(module.W1LookupClientError) as caught:
        module.W1LookupClient(endpoint=endpoint, bearer=BEARER)
    assert caught.value.code == "INVALID_CONFIGURATION"


@pytest.mark.parametrize("bearer", ["", " leading", "trailing ", "line\r\nbreak", "\ud800"])
def test_client_rejects_unsafe_bearer(bearer: str) -> None:
    module = _client_module()
    with pytest.raises(module.W1LookupClientError) as caught:
        module.W1LookupClient(endpoint=ENDPOINT, bearer=bearer)
    assert caught.value.code == "INVALID_CONFIGURATION"


@pytest.mark.parametrize("timeout", [0.0, -1.0, math.inf, math.nan, True, 10**400])
def test_client_rejects_non_finite_or_non_positive_timeout(timeout: float) -> None:
    module = _client_module()
    with pytest.raises(module.W1LookupClientError) as caught:
        module.W1LookupClient(endpoint=ENDPOINT, bearer=BEARER, timeout_seconds=timeout)
    assert caught.value.code == "INVALID_CONFIGURATION"


def test_client_rejects_ssl_context_without_hostname_and_certificate_verification() -> None:
    module = _client_module()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    with pytest.raises(module.W1LookupClientError) as caught:
        module.W1LookupClient(endpoint=ENDPOINT, bearer=BEARER, ssl_context=context)

    assert caught.value.code == "INVALID_CONFIGURATION"


def test_request_size_is_bounded_before_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _client_module()
    transport = _RecordingTransport(
        _response(module, _load("private-command-lookup-available.json"))
    )
    client = module.W1LookupClient(endpoint=ENDPOINT, bearer=BEARER, transport=transport)
    monkeypatch.setattr(module, "MAX_LOOKUP_REQUEST_BYTES", 1)

    with pytest.raises(module.W1LookupClientError) as caught:
        client.lookup(_dispatch().lookup_request)

    assert caught.value.code == "REQUEST_TOO_LARGE"
    assert transport.calls == []


def test_stdlib_transport_does_not_use_proxy_follow_redirect_or_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _client_module()
    observed: dict[str, Any] = {"requests": []}

    class FakeHTTPResponse:
        status = 302

        @staticmethod
        def getheader(name: str) -> str | None:
            return "application/json" if name == "Content-Type" else None

        @staticmethod
        def read(amount: int) -> bytes:
            observed["read_amount"] = amount
            return b"{}"

    class FakeHTTPSConnection:
        def __init__(
            self,
            host: str,
            port: int,
            *,
            timeout: float,
            context: ssl.SSLContext,
        ) -> None:
            observed.update(host=host, port=port, timeout=timeout, context=context)

        def request(
            self, method: str, target: str, *, body: bytes, headers: dict[str, str]
        ) -> None:
            observed["requests"].append((method, target, body, headers))

        @staticmethod
        def getresponse() -> FakeHTTPResponse:
            return FakeHTTPResponse()

        @staticmethod
        def close() -> None:
            observed["closed"] = True

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy-marker.invalid:8888")
    monkeypatch.setattr(module.http.client, "HTTPSConnection", FakeHTTPSConnection)
    client = module.W1LookupClient(endpoint=ENDPOINT, bearer=BEARER)

    with pytest.raises(w1_transport.W1WireContractError):
        client.lookup(_dispatch().lookup_request)

    assert observed["host"] == "w1-private.example.test"
    assert observed["port"] == 443
    assert len(observed["requests"]) == 1
    assert observed["requests"][0][0:2] == (
        "POST",
        "/internal/v1/job-commands/lookup",
    )
    assert observed["read_amount"] == module.MAX_LOOKUP_RESPONSE_BYTES + 1
    assert observed["closed"] is True
