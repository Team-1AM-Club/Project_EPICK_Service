"""Offline tests for W1's protected private-authority HTTPS caller."""

from __future__ import annotations

import importlib
import importlib.util
import json
import ssl
from types import ModuleType
from typing import Any
from uuid import UUID

import pytest

from epick_engine.source_collection import w1_lookup_client
from epick_engine.source_collection.private_deletion_v2 import PrivateDeletionScope
from epick_engine.source_collection.w1_private_authority_contracts import (
    W1GateBinding,
    W1PrivateBinding,
)
from epick_engine.source_collection.w1_transport import W1WireContractError

ENDPOINT = "https://w1-private.example.test/internal/v1/job-commands/lookup"
BEARER = "synthetic-test-bearer"
OWNER_ID = UUID("10000000-0000-4000-8000-000000000001")
COMMAND_ID = UUID("20000000-0000-4000-8000-000000000001")
JOB_ID = UUID("30000000-0000-4000-8000-000000000001")
OPERATION_ID = UUID("40000000-0000-4000-8000-000000000001")
DIGEST = "sha256:" + "a" * 64


def _client_module() -> ModuleType:
    name = "epick_engine.source_collection.w1_private_authority_client"
    assert importlib.util.find_spec(name) is not None, "private authority client is missing"
    return importlib.import_module(name)


class _RecordingTransport:
    def __init__(
        self,
        responses: list[object] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.responses = [] if responses is None else responses
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def post(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.responses[len(self.calls) - 1]


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


def _binding() -> W1PrivateBinding:
    return W1PrivateBinding(
        owner_user_id=OWNER_ID,
        owner_deletion_epoch=0,
        command_id=COMMAND_ID,
        job_id=JOB_ID,
        execution_fence=7,
    )


def _gate() -> W1GateBinding:
    return W1GateBinding(
        private=_binding(),
        operation_id=OPERATION_ID,
        operation_revision=3,
        action="PREPARE",
        result_digest=DIGEST,
        purge_owner_deletion_epoch=None,
    )


def _binding_payload() -> dict[str, object]:
    return {
        "owner_user_id": str(OWNER_ID),
        "owner_deletion_epoch": 0,
        "command_id": str(COMMAND_ID),
        "job_id": str(JOB_ID),
        "execution_fence": 7,
    }


def test_private_client_posts_exact_protected_path_and_principal() -> None:
    module = _client_module()
    account_scope = PrivateDeletionScope(kind="ACCOUNT", project_id=None)
    current_request = {
        "schema_version": "w1.private.w2-current-write-scope-lookup.v1",
        **_binding_payload(),
    }
    write_request = {
        "schema_version": "w1.private.w2-write-authority.v1",
        **_binding_payload(),
        "scope": {"type": "ACCOUNT"},
    }
    gate_lookup_request = {
        "schema_version": "w1.private.w2-gate-scope-lookup.v1",
        **_binding_payload(),
        "operation_id": str(OPERATION_ID),
        "operation_revision": 3,
        "action": "PREPARE",
        "phase": "APPLY",
        "result_digest": DIGEST,
    }
    gate_authority_request = {
        "schema_version": "w1.private.w2-gate-authority.v1",
        **_binding_payload(),
        "scope": {"type": "ACCOUNT"},
        "operation_id": str(OPERATION_ID),
        "operation_revision": 3,
        "action": "PREPARE",
        "phase": "ACK_RELAY",
        "result_digest": DIGEST,
    }
    cleanup_request = {
        "schema_version": "w1.private.w2-terminal-cleanup.v1",
        **_binding_payload(),
        "scope": {"type": "ACCOUNT"},
        "cleanup_kind": "STAGED_OUTBOX",
    }
    request_cases = [
        (
            "/internal/v1/w2-private/current-write-scope-lookup",
            current_request,
            {**current_request, "scope": {"type": "ACCOUNT"}},
        ),
        (
            "/internal/v1/w2-private/authority",
            write_request,
            {**write_request, "authority_ref": "w1:write:1"},
        ),
        (
            "/internal/v1/w2-private/gate-scope-lookup",
            gate_lookup_request,
            {**gate_lookup_request, "scope": {"type": "ACCOUNT"}},
        ),
        (
            "/internal/v1/w2-private/gate-authority",
            gate_authority_request,
            {**gate_authority_request, "authority_ref": "w1:gate:1"},
        ),
        (
            "/internal/v1/w2-private/terminal-cleanup-authority",
            cleanup_request,
            {
                **cleanup_request,
                "authority_ref": "w1:cleanup:1",
                "allowed_effect": "OWNER_LOCKED_PRIVATE_CLEANUP_ONLY",
            },
        ),
    ]
    transport = _RecordingTransport([_response(response) for _, _, response in request_cases])
    context = ssl.create_default_context()
    client = module.W1PrivateAuthorityClient(
        endpoint=ENDPOINT,
        bearer=BEARER,
        ssl_context=context,
        transport=transport,
    )

    results = [
        client.lookup_current_scope(_binding()),
        client.authorize_write(_binding(), account_scope),
        client.lookup_gate_scope(_gate(), "APPLY"),
        client.authorize_gate(_gate(), "ACK_RELAY", account_scope),
        client.authorize_terminal_cleanup(_binding(), account_scope, "STAGED_OUTBOX"),
    ]

    assert [result.model_dump(mode="json", exclude_none=True) for result in results] == [
        response for _, _, response in request_cases
    ]
    assert len(transport.calls) == 5
    for call, (target, request_payload, _) in zip(transport.calls, request_cases, strict=True):
        assert call["host"] == "w1-private.example.test"
        assert call["port"] == 443
        assert call["target"] == target
        assert call["timeout"] == w1_lookup_client.DEFAULT_LOOKUP_TIMEOUT_SECONDS
        assert call["max_response_bytes"] == w1_lookup_client.MAX_LOOKUP_RESPONSE_BYTES
        assert call["headers"] == {
            "Accept": "application/json",
            "Authorization": f"Bearer {BEARER}",
            "Content-Length": str(len(call["body"])),
            "Content-Type": "application/json",
            "X-EPICK-Service-Principal": "w2",
        }
        assert json.loads(call["body"].decode("utf-8")) == request_payload
        assert call["ssl_context"] is context


@pytest.mark.parametrize(
    "failure",
    [
        "http-401",
        "http-403",
        "http-422",
        "http-503",
        "timeout",
        "malformed-json",
        "oversized-json",
        "wrong-content-type",
        "redirect",
        "wrong-echo",
    ],
)
def test_private_client_fail_closed_without_retry_or_secret_echo(
    failure: str,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _client_module()
    endpoint_marker = "authority-endpoint-marker"
    bearer_marker = "authority-bearer-marker"
    raw_marker = "authority-raw-response-marker"
    endpoint = f"https://{endpoint_marker}.example.test/internal/v1/job-commands/lookup"
    successful_payload = {
        "schema_version": "w1.private.w2-current-write-scope-lookup.v1",
        **_binding_payload(),
        "scope": {"type": "ACCOUNT"},
    }
    expected_code: str | None
    error: Exception | None = None
    if failure.startswith("http-"):
        status = int(failure.removeprefix("http-"))
        response = _response({"private": raw_marker}, status=status)
        expected_code = f"HTTP_{status}"
    elif failure == "timeout":
        response = None
        error = TimeoutError(f"{endpoint_marker} {bearer_marker} {raw_marker}")
        expected_code = "TRANSPORT_FAILURE"
    elif failure == "malformed-json":
        response = _response(b'{"private":"' + raw_marker.encode("utf-8"))
        expected_code = "INVALID_RESPONSE_BODY"
    elif failure == "oversized-json":
        oversized = json.dumps({"private": raw_marker + "x" * (64 * 1024)}).encode()
        response = _response(oversized)
        expected_code = "RESPONSE_TOO_LARGE"
    elif failure == "wrong-content-type":
        response = _response(
            {"private": raw_marker},
            content_type="text/html",
        )
        expected_code = "UNSUPPORTED_RESPONSE_CONTENT"
    elif failure == "redirect":
        response = _response({"private": raw_marker}, status=302)
        expected_code = "HTTP_302"
    else:
        response = _response(
            {
                **successful_payload,
                "owner_user_id": "99999999-9999-4999-8999-999999999999",
            },
        )
        expected_code = None
    transport = _RecordingTransport(
        [] if response is None else [response],
        error=error,
    )
    client = module.W1PrivateAuthorityClient(
        endpoint=endpoint,
        bearer=bearer_marker,
        ssl_context=ssl.create_default_context(),
        transport=transport,
    )

    with pytest.raises((module.W1LookupClientError, W1WireContractError)) as caught:
        client.lookup_current_scope(_binding())

    assert len(transport.calls) == 1
    if expected_code is not None:
        assert isinstance(caught.value, module.W1LookupClientError)
        assert caught.value.code == expected_code
    else:
        assert isinstance(caught.value, W1WireContractError)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    rendered = f"{caught.value!s} {caught.value!r} {client!r}"
    for marker in (endpoint_marker, bearer_marker, raw_marker):
        assert marker not in rendered
    assert caplog.records == []
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
