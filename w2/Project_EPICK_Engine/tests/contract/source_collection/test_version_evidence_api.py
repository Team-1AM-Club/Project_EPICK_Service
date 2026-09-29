"""HTTP contracts for SourceVersion, Evidence, and explicit Source refresh.

T037 fixes the future ``create_version_evidence_api`` boundary before T042/T043
provide its service and routes.  The factory receives owner-authentication,
cursor, and service dependencies and exposes only these public operations:

* ``GET /api/v1/sources/{source_id}/versions``;
* ``GET /api/v1/source-versions/{version_id}/evidence``; and
* ``POST /api/v1/sources/{source_id}/refresh``.

The fake service models policy, slot, idempotency, and historical-result
branches solely as observable boundary inputs.  It deliberately keeps private
Job fields in its return values so the HTTP layer must allow-list public data.
No ``TestClient`` is used: requests enter FastAPI through an in-memory ASGI
scope, receive, and send implementation.
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import Awaitable, Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, Request
from starlette.types import Message, Receive, Scope, Send

import epick_engine.source_collection.api as api
from epick_engine.source_collection.api import (
    ApiBoundarySettings,
    ApiErrorCode,
    ApiProblem,
    AuthenticatedPrincipal,
    CursorCodec,
)

_OWNER_ID = UUID("00000000-0000-4000-8000-000000009301")
_OTHER_OWNER_ID = UUID("00000000-0000-4000-8000-000000009302")
_SOURCE_ID = UUID("00000000-0000-4000-8000-000000000601")
_OTHER_SOURCE_ID = UUID("00000000-0000-4000-8000-000000000602")
_VERSION_EARLIEST_ID = UUID("00000000-0000-4000-8000-000000000701")
_VERSION_TIE_ID = UUID("00000000-0000-4000-8000-000000000702")
_VERSION_LATEST_ID = UUID("00000000-0000-4000-8000-000000000703")
_VERSION_CHANGED_ID = UUID("00000000-0000-4000-8000-000000000704")
_REVISION_ID = UUID("00000000-0000-4000-8000-000000000801")
_OTHER_REVISION_ID = UUID("00000000-0000-4000-8000-000000000802")
_EVIDENCE_TIE_FIRST_ID = UUID("00000000-0000-4000-8000-000000000901")
_EVIDENCE_TIE_SECOND_ID = UUID("00000000-0000-4000-8000-000000000902")
_EVIDENCE_LATER_ID = UUID("00000000-0000-4000-8000-000000000903")
_COMPLETED_JOB_ID = UUID("00000000-0000-4000-8000-000000000951")
_ANALYSIS_REQUEST_ID = UUID("00000000-0000-4000-8000-000000000981")
_OTHER_ANALYSIS_REQUEST_ID = UUID("00000000-0000-4000-8000-000000000982")
_PRIVATE_SENTINEL_KEY = "__unexpected_private_sentinel__"
_PRIVATE_SENTINEL_VALUE = "test-only-private-sentinel-3344dbec"
_SETTINGS = ApiBoundarySettings(
    idempotency_key_max_length=256,
    cursor_max_token_length=1_024,
    page_default_limit=20,
    page_max_limit=100,
)
_API_EVENT_LOOP: asyncio.AbstractEventLoop | None = None
_ENGINE_ROOT = Path(__file__).resolve().parents[3]
_REFRESH_SCHEMA_PATH = (
    _ENGINE_ROOT.parent
    / "specs"
    / "001-official-source-collection"
    / "contracts"
    / "collection-actions.schema.json"
)
_VERSION_PUBLIC_FIELDS = frozenset(
    {
        "source_version_id",
        "title",
        "source_type",
        "canonical_url",
        "content_hash",
        "hash_profile_version",
        "representation",
        "collected_at",
    }
)
_EVIDENCE_PUBLIC_FIELDS = frozenset(
    {
        "evidence_id",
        "source_version_id",
        "section_title",
        "text_excerpt",
        "locator",
        "chunk_order",
        "origin_kind",
    }
)
_PRIVATE_RESPONSE_FIELDS = frozenset(
    {
        "owner_user_id",
        "execution_fence",
        "private_w1_command",
        _PRIVATE_SENTINEL_KEY,
    }
)


@dataclass(frozen=True)
class _AsgiResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes

    def payload(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.body))


@dataclass
class _FetchSpy:
    calls: list[str] = field(default_factory=list)

    def fetch(self, url: str) -> None:
        self.calls.append(url)
        raise AssertionError("GET routes must not start an external fetch")


@dataclass
class _FakeVersionEvidenceService:
    fetch_spy: _FetchSpy = field(default_factory=_FetchSpy)
    refresh_policy_allowed: bool = True
    evidence_policy_allowed: bool = True
    slots_available: bool = True
    calls: list[str] = field(default_factory=list)
    version_cursors: list[object | None] = field(default_factory=list)
    evidence_cursors: list[object | None] = field(default_factory=list)
    policy_checks: list[str] = field(default_factory=list)
    refresh_records: dict[tuple[UUID, UUID, str], tuple[dict[str, Any], dict[str, Any]]] = field(
        default_factory=dict
    )
    jobs: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    completed_results: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    dispatches: list[UUID] = field(default_factory=list)
    source_versions: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[tuple[UUID, UUID], list[dict[str, Any]]] = field(default_factory=dict)
    body_ref: object | None = None
    retention_scope: str = "excerpts_only"
    current_restriction: object = field(default_factory=lambda: {"state": "current"})

    def __post_init__(self) -> None:
        self.jobs[_COMPLETED_JOB_ID] = {
            "status": "SUCCEEDED",
            "source_id": str(_SOURCE_ID),
            "result_ref": "completed-result-951",
        }
        self.completed_results[_COMPLETED_JOB_ID] = {
            "source_version_id": str(_VERSION_LATEST_ID),
            "evidence_ids": [str(_EVIDENCE_TIE_FIRST_ID)],
        }
        self.source_versions = [
            self._version(
                _VERSION_EARLIEST_ID,
                collected_at="2026-09-10T10:00:00Z",
                content_hash="a" * 64,
            ),
            self._version(
                _VERSION_TIE_ID,
                collected_at="2026-09-10T11:00:00Z",
                content_hash="b" * 64,
            ),
            self._version(
                _VERSION_LATEST_ID,
                collected_at="2026-09-10T11:00:00Z",
                content_hash="c" * 64,
            ),
        ]
        self.evidence[(_VERSION_LATEST_ID, _REVISION_ID)] = [
            self._evidence(_EVIDENCE_TIE_SECOND_ID, chunk_order=1, text="Second excerpt"),
            self._evidence(_EVIDENCE_LATER_ID, chunk_order=2, text="Later excerpt"),
            self._evidence(_EVIDENCE_TIE_FIRST_ID, chunk_order=1, text="First excerpt"),
        ]

    def _version(
        self,
        source_version_id: UUID,
        *,
        collected_at: str,
        content_hash: str,
    ) -> dict[str, Any]:
        return {
            "source_version_id": str(source_version_id),
            "title": "Synthetic version",
            "source_type": "job_posting",
            "canonical_url": "https://synthetic-version.test/careers/engineer",
            "content_hash": content_hash,
            "hash_profile_version": "epick-response-sha256-v1",
            "representation": "html",
            "collected_at": collected_at,
            "owner_user_id": str(_OWNER_ID),
            _PRIVATE_SENTINEL_KEY: _PRIVATE_SENTINEL_VALUE,
        }

    def _evidence(self, evidence_id: UUID, *, chunk_order: int, text: str) -> dict[str, Any]:
        return {
            "evidence_id": str(evidence_id),
            "source_version_id": str(_VERSION_LATEST_ID),
            "section_title": "Requirements",
            "text_excerpt": text,
            "locator": {"kind": "text_offset", "start": 0, "end": len(text)},
            "chunk_order": chunk_order,
            "origin_kind": "html",
            "execution_fence": "private-execution-fence",
            _PRIVATE_SENTINEL_KEY: _PRIVATE_SENTINEL_VALUE,
        }

    def _require_source_owner(self, owner_user_id: UUID, source_id: UUID) -> None:
        if source_id != _SOURCE_ID or owner_user_id != _OWNER_ID:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND)

    def list_source_versions(
        self,
        *,
        owner_user_id: UUID,
        source_id: UUID,
        cursor: object | None,
        limit: int,
    ) -> dict[str, Any]:
        self.calls.append("list_source_versions")
        self._require_source_owner(owner_user_id, source_id)
        self.version_cursors.append(cursor)
        ordered = sorted(
            self.source_versions,
            key=lambda item: (item["collected_at"], item["source_version_id"]),
            reverse=True,
        )
        offset = cast(int, cursor) if cursor is not None else 0
        page = ordered[offset : offset + limit]
        next_offset = offset + len(page)
        return {
            "items": deepcopy(page),
            "next_cursor": next_offset if next_offset < len(ordered) else None,
            "current_restriction": deepcopy(self.current_restriction),
            _PRIVATE_SENTINEL_KEY: _PRIVATE_SENTINEL_VALUE,
        }

    def list_evidence(
        self,
        *,
        owner_user_id: UUID,
        source_version_id: UUID,
        extraction_revision_id: UUID,
        cursor: object | None,
        limit: int,
    ) -> dict[str, Any]:
        self.calls.append("list_evidence")
        self._require_source_owner(owner_user_id, _SOURCE_ID)
        self.policy_checks.append("evidence")
        if not self.evidence_policy_allowed:
            raise ApiProblem(ApiErrorCode.SOURCE_POLICY_BLOCKED)
        rows = self.evidence.get((source_version_id, extraction_revision_id))
        if rows is None:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND)
        self.evidence_cursors.append(cursor)
        ordered = sorted(rows, key=lambda item: (item["chunk_order"], item["evidence_id"]))
        offset = cast(int, cursor) if cursor is not None else 0
        page = ordered[offset : offset + limit]
        next_offset = offset + len(page)
        return {
            "items": deepcopy(page),
            "next_cursor": next_offset if next_offset < len(ordered) else None,
            "retention_scope": self.retention_scope,
            "body_ref": deepcopy(self.body_ref),
            "current_restriction": deepcopy(self.current_restriction),
            _PRIVATE_SENTINEL_KEY: _PRIVATE_SENTINEL_VALUE,
        }

    def refresh_source(
        self,
        *,
        owner_user_id: UUID,
        source_id: UUID,
        analysis_request_id: UUID | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.calls.append("refresh_source")
        self._require_source_owner(owner_user_id, source_id)
        self.policy_checks.append("refresh")
        if not self.refresh_policy_allowed:
            raise ApiProblem(ApiErrorCode.SOURCE_POLICY_BLOCKED)

        payload = {"analysis_request_id": str(analysis_request_id) if analysis_request_id else None}
        record_key = (owner_user_id, source_id, idempotency_key)
        existing = self.refresh_records.get(record_key)
        if existing is not None:
            existing_payload, existing_response = existing
            if existing_payload != payload:
                raise ApiProblem(ApiErrorCode.IDEMPOTENCY_CONFLICT)
            return deepcopy(existing_response)

        job_id = uuid4()
        response = {
            "source_id": str(source_id),
            "job_id": str(job_id),
            "status_url": f"/api/v1/jobs/{job_id}",
            _PRIVATE_SENTINEL_KEY: _PRIVATE_SENTINEL_VALUE,
        }
        self.refresh_records[record_key] = (payload, response)
        self.jobs[job_id] = {
            "status": "RUNNING" if self.slots_available else "QUEUED",
            "source_id": str(source_id),
            "owner_user_id": str(owner_user_id),
            "slot_acquired": self.slots_available,
        }
        if self.slots_available:
            self.dispatches.append(job_id)
        return deepcopy(response)

    def complete_refresh(self, job_id: UUID, *, content_hash: str) -> None:
        """Model worker completion without making any network request in a GET test."""

        self.jobs[job_id]["status"] = "SUCCEEDED"
        if any(item["content_hash"] == content_hash for item in self.source_versions):
            return
        self.source_versions.append(
            self._version(
                _VERSION_CHANGED_ID,
                collected_at="2026-09-10T12:00:00Z",
                content_hash=content_hash,
            )
        )
        self.evidence[(_VERSION_CHANGED_ID, _OTHER_REVISION_ID)] = [
            self._evidence(
                UUID("00000000-0000-4000-8000-000000000904"),
                chunk_order=1,
                text="Changed excerpt",
            )
        ]


def _authenticate(request: Request) -> AuthenticatedPrincipal | None:
    if request.headers.get("authorization") == "Bearer synthetic-owner":
        return AuthenticatedPrincipal(user_id=_OWNER_ID)
    if request.headers.get("authorization") == "Bearer synthetic-other-owner":
        return AuthenticatedPrincipal(user_id=_OTHER_OWNER_ID)
    return None


def _complete(awaitable: Awaitable[Any]) -> Any:
    if _API_EVENT_LOOP is None:
        raise RuntimeError("test-local API event loop is not configured")
    return _API_EVENT_LOOP.run_until_complete(cast(Any, awaitable))


@pytest.fixture(scope="module", autouse=True)
def _manage_api_event_loop() -> Iterator[None]:
    global _API_EVENT_LOOP
    event_loop = asyncio.new_event_loop()
    _API_EVENT_LOOP = event_loop
    try:
        yield
    finally:
        event_loop.close()
        _API_EVENT_LOOP = None


def _request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    query: Mapping[str, str] | None = None,
    headers: Mapping[str, str] | None = None,
    body: Mapping[str, Any] | None = None,
) -> _AsgiResponse:
    request_body = b"" if body is None else json.dumps(body).encode("utf-8")
    raw_headers = [(b"host", b"testserver")]
    raw_headers.extend(
        (name.lower().encode("ascii"), value.encode("utf-8"))
        for name, value in (headers or {}).items()
    )
    if body is not None:
        raw_headers.append((b"content-type", b"application/json"))

    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": urlencode(query or {}).encode("ascii"),
            "headers": raw_headers,
            "client": ("test-client", 1),
            "server": ("testserver", 80),
        },
    )
    request_delivered = False
    messages: list[Message] = []

    async def receive() -> Message:
        nonlocal request_delivered
        if request_delivered:
            return {"type": "http.disconnect"}
        request_delivered = True
        return {"type": "http.request", "body": request_body, "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    _complete(app(scope, cast(Receive, receive), cast(Send, send)))
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        cast(bytes, message.get("body", b""))
        for message in messages
        if message["type"] == "http.response.body"
    )
    response_headers = {
        name.decode("latin-1"): value.decode("latin-1")
        for name, value in cast(list[tuple[bytes, bytes]], start["headers"])
    }
    return _AsgiResponse(
        status_code=cast(int, start["status"]),
        headers=response_headers,
        body=response_body,
    )


def _app(
    service: _FakeVersionEvidenceService,
    *,
    cursor_codec: CursorCodec | None = None,
) -> FastAPI:
    factory = getattr(api, "create_version_evidence_api", None)
    assert callable(factory), (
        "T043 must provide create_version_evidence_api("
        "*, service, authenticator, settings, cursor_codec) -> FastAPI"
    )
    return cast(Any, factory)(
        service=service,
        authenticator=_authenticate,
        settings=_SETTINGS,
        cursor_codec=cursor_codec
        or CursorCodec("contract-test-cursor-secret-at-least-32-bytes", _SETTINGS),
    )


def _owner_headers(**headers: str) -> dict[str, str]:
    return {"Authorization": "Bearer synthetic-owner", **headers}


def _other_owner_headers(**headers: str) -> dict[str, str]:
    return {"Authorization": "Bearer synthetic-other-owner", **headers}


def _error_without_correlation(response: _AsgiResponse) -> dict[str, Any]:
    payload = deepcopy(response.payload())
    del payload["error"]["correlation_id"]
    return payload


def _assert_no_private_response_fields(payload: object) -> None:
    if isinstance(payload, Mapping):
        assert not (_PRIVATE_RESPONSE_FIELDS & set(payload))
        for value in payload.values():
            _assert_no_private_response_fields(value)
    elif isinstance(payload, list):
        for value in payload:
            _assert_no_private_response_fields(value)


def _assert_indistinguishable_not_found(*responses: _AsgiResponse) -> None:
    assert all(response.status_code == 404 for response in responses)
    payloads = [response.payload() for response in responses]
    assert all(set(payload) == {"error"} for payload in payloads)
    assert all(
        set(payload["error"]) == {"code", "message_ko", "retryable", "actions", "correlation_id"}
        for payload in payloads
    )
    assert all(payload["error"]["code"] == ApiErrorCode.RESOURCE_NOT_FOUND for payload in payloads)
    assert all(
        _error_without_correlation(response) == _error_without_correlation(responses[0])
        for response in responses[1:]
    )


@pytest.fixture
def service() -> _FakeVersionEvidenceService:
    return _FakeVersionEvidenceService()


@pytest.fixture
def app(service: _FakeVersionEvidenceService) -> FastAPI:
    return _app(service)


@pytest.fixture
def network_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def _blocked_connect(*_args: object, **_kwargs: object) -> None:
        calls.append("socket.create_connection")
        raise AssertionError("contract HTTP tests must not use the network")

    monkeypatch.setattr(socket, "create_connection", _blocked_connect)
    return calls


def test_refresh_request_schema_allows_only_optional_analysis_request_uuid() -> None:
    schema = json.loads(_REFRESH_SCHEMA_PATH.read_text(encoding="utf-8"))
    refresh_request = schema["$defs"]["RefreshRequest"]

    assert refresh_request == {
        "type": "object",
        "additionalProperties": False,
        "properties": {"analysis_request_id": {"type": "string", "format": "uuid"}},
    }


@pytest.mark.parametrize(
    ("method", "path", "query", "body"),
    [
        ("GET", f"/api/v1/sources/{_SOURCE_ID}/versions", None, None),
        (
            "GET",
            f"/api/v1/source-versions/{_VERSION_LATEST_ID}/evidence",
            {"extraction_revision_id": str(_REVISION_ID)},
            None,
        ),
        ("POST", f"/api/v1/sources/{_SOURCE_ID}/refresh", None, {}),
    ],
)
def test_version_evidence_routes_require_authentication_before_service_or_network(
    app: FastAPI,
    service: _FakeVersionEvidenceService,
    network_calls: list[str],
    method: str,
    path: str,
    query: Mapping[str, str] | None,
    body: Mapping[str, Any] | None,
) -> None:
    response = _request(app, method, path, query=query, body=body)

    assert response.status_code == 401
    assert response.payload()["error"]["code"] == ApiErrorCode.AUTHENTICATION_REQUIRED
    assert service.calls == []
    assert service.fetch_spy.calls == []
    assert network_calls == []


def test_source_version_list_uses_stable_cursor_order_and_public_allow_list(
    app: FastAPI,
    service: _FakeVersionEvidenceService,
    network_calls: list[str],
) -> None:
    first = _request(
        app,
        "GET",
        f"/api/v1/sources/{_SOURCE_ID}/versions",
        query={"limit": "2"},
        headers=_owner_headers(),
    )
    second = _request(
        app,
        "GET",
        f"/api/v1/sources/{_SOURCE_ID}/versions",
        query={"limit": "2", "cursor": cast(str, first.payload()["next_cursor"])},
        headers=_owner_headers(),
    )

    assert first.status_code == second.status_code == 200
    assert [item["source_version_id"] for item in first.payload()["items"]] == [
        str(_VERSION_LATEST_ID),
        str(_VERSION_TIE_ID),
    ]
    assert [item["source_version_id"] for item in second.payload()["items"]] == [
        str(_VERSION_EARLIEST_ID)
    ]
    assert all(set(item) == _VERSION_PUBLIC_FIELDS for item in first.payload()["items"])
    _assert_no_private_response_fields(first.payload())
    _assert_no_private_response_fields(second.payload())
    assert service.version_cursors == [None, 2]
    assert service.fetch_spy.calls == network_calls == []


def test_evidence_list_requires_matching_revision_sorts_and_discloses_excerpts_only_retention(
    app: FastAPI,
    service: _FakeVersionEvidenceService,
    network_calls: list[str],
) -> None:
    first = _request(
        app,
        "GET",
        f"/api/v1/source-versions/{_VERSION_LATEST_ID}/evidence",
        query={"extraction_revision_id": str(_REVISION_ID), "limit": "2"},
        headers=_owner_headers(),
    )
    second = _request(
        app,
        "GET",
        f"/api/v1/source-versions/{_VERSION_LATEST_ID}/evidence",
        query={
            "extraction_revision_id": str(_REVISION_ID),
            "limit": "2",
            "cursor": cast(str, first.payload()["next_cursor"]),
        },
        headers=_owner_headers(),
    )

    assert first.status_code == second.status_code == 200
    assert [item["evidence_id"] for item in first.payload()["items"]] == [
        str(_EVIDENCE_TIE_FIRST_ID),
        str(_EVIDENCE_TIE_SECOND_ID),
    ]
    assert [item["evidence_id"] for item in second.payload()["items"]] == [str(_EVIDENCE_LATER_ID)]
    assert all(set(item) == _EVIDENCE_PUBLIC_FIELDS for item in first.payload()["items"])
    assert first.payload()["retention_scope"] == "excerpts_only"
    assert first.payload()["body_ref"] is None
    _assert_no_private_response_fields(first.payload())
    _assert_no_private_response_fields(second.payload())
    assert service.evidence_cursors == [None, 2]
    assert service.fetch_spy.calls == network_calls == []


def test_evidence_locator_allow_lists_nested_fields_and_supports_frozen_dtos(
    app: FastAPI,
    service: _FakeVersionEvidenceService,
    network_calls: list[str],
) -> None:
    mapping_locator = service.evidence[(_VERSION_LATEST_ID, _REVISION_ID)][0]["locator"]
    mapping_locator.update(
        {
            "value": "requirements[0]",
            "normalization_version": "v1",
            "body_path": "private-body-path-901",
            "owner_user_id": str(_OWNER_ID),
            _PRIVATE_SENTINEL_KEY: _PRIVATE_SENTINEL_VALUE,
        }
    )

    mapping_response = _request(
        app,
        "GET",
        f"/api/v1/source-versions/{_VERSION_LATEST_ID}/evidence",
        query={"extraction_revision_id": str(_REVISION_ID), "limit": "10"},
        headers=_owner_headers(),
    )

    assert mapping_response.status_code == 200
    mapping_item = next(
        item
        for item in mapping_response.payload()["items"]
        if item["evidence_id"] == str(_EVIDENCE_TIE_SECOND_ID)
    )
    assert mapping_item["locator"] == {
        "kind": "text_offset",
        "value": "requirements[0]",
        "normalization_version": "v1",
        "start": 0,
        "end": len("Second excerpt"),
    }
    assert "private-body-path-901" not in mapping_response.body.decode("utf-8")
    _assert_no_private_response_fields(mapping_response.payload())

    @dataclass(frozen=True)
    class _FrozenLocator:
        kind: str = "css_selector"
        value: str = ".job-description"
        normalization_version: str = "v2"
        body_path: str = "private-body-path-902"
        owner_user_id: str = str(_OWNER_ID)

    service.evidence[(_VERSION_LATEST_ID, _REVISION_ID)][1]["locator"] = _FrozenLocator()
    dto_response = _request(
        app,
        "GET",
        f"/api/v1/source-versions/{_VERSION_LATEST_ID}/evidence",
        query={"extraction_revision_id": str(_REVISION_ID), "limit": "10"},
        headers=_owner_headers(),
    )

    assert dto_response.status_code == 200
    dto_item = next(
        item
        for item in dto_response.payload()["items"]
        if item["evidence_id"] == str(_EVIDENCE_LATER_ID)
    )
    assert dto_item["locator"] == {
        "kind": "css_selector",
        "value": ".job-description",
        "normalization_version": "v2",
    }
    assert "private-body-path-902" not in dto_response.body.decode("utf-8")
    _assert_no_private_response_fields(dto_response.payload())
    assert service.fetch_spy.calls == network_calls == []


def test_evidence_rejects_malformed_locator_without_disclosing_raw_value(
    app: FastAPI,
    service: _FakeVersionEvidenceService,
    network_calls: list[str],
) -> None:
    service.evidence[(_VERSION_LATEST_ID, _REVISION_ID)][0]["locator"] = {
        "kind": "css_selector",
        "value": 7,
        "body_path": "private-body-path-903",
    }

    response = _request(
        app,
        "GET",
        f"/api/v1/source-versions/{_VERSION_LATEST_ID}/evidence",
        query={"extraction_revision_id": str(_REVISION_ID)},
        headers=_owner_headers(),
    )

    assert response.status_code == 503
    assert response.payload()["error"]["code"] == ApiErrorCode.DEPENDENCY_UNAVAILABLE
    assert "private-body-path-903" not in response.body.decode("utf-8")
    assert service.fetch_spy.calls == network_calls == []


def test_evidence_body_ref_requires_the_central_opaque_reference_validator(
    app: FastAPI,
    service: _FakeVersionEvidenceService,
    network_calls: list[str],
) -> None:
    service.retention_scope = "normalized_body"
    service.body_ref = "retained-body:opaque-01"

    allowed = _request(
        app,
        "GET",
        f"/api/v1/source-versions/{_VERSION_LATEST_ID}/evidence",
        query={"extraction_revision_id": str(_REVISION_ID)},
        headers=_owner_headers(),
    )

    service.body_ref = "C:\\private\\retained-body.txt"
    blocked = _request(
        app,
        "GET",
        f"/api/v1/source-versions/{_VERSION_LATEST_ID}/evidence",
        query={"extraction_revision_id": str(_REVISION_ID)},
        headers=_owner_headers(),
    )

    assert allowed.status_code == 200
    assert allowed.payload()["body_ref"] == "retained-body:opaque-01"
    assert blocked.status_code == 503
    assert blocked.payload()["error"]["code"] == ApiErrorCode.DEPENDENCY_UNAVAILABLE
    assert "private\\retained-body.txt" not in blocked.body.decode("utf-8")
    assert service.fetch_spy.calls == network_calls == []


def test_evidence_projects_frozen_current_restriction_without_private_attributes(
    app: FastAPI,
    service: _FakeVersionEvidenceService,
    network_calls: list[str],
) -> None:
    @dataclass(frozen=True)
    class _FrozenRestriction:
        state: str = "restricted"
        reason_code: str = "official-source-limited"
        owner_user_id: str = str(_OWNER_ID)
        body_path: str = "private-body-path-904"

    service.current_restriction = _FrozenRestriction()

    response = _request(
        app,
        "GET",
        f"/api/v1/source-versions/{_VERSION_LATEST_ID}/evidence",
        query={"extraction_revision_id": str(_REVISION_ID)},
        headers=_owner_headers(),
    )

    assert response.status_code == 200
    restriction = response.payload()["current_restriction"]
    assert restriction["state"] == "restricted"
    assert restriction["reason_code"] == "official-source-limited"
    assert "owner_user_id" not in restriction
    assert "private-body-path-904" not in response.body.decode("utf-8")
    _assert_no_private_response_fields(response.payload())
    assert service.fetch_spy.calls == network_calls == []


def test_evidence_rejects_a_revision_that_does_not_belong_to_the_requested_version(
    app: FastAPI,
    service: _FakeVersionEvidenceService,
    network_calls: list[str],
) -> None:
    response = _request(
        app,
        "GET",
        f"/api/v1/source-versions/{_VERSION_TIE_ID}/evidence",
        query={"extraction_revision_id": str(_REVISION_ID)},
        headers=_owner_headers(),
    )

    assert response.status_code == 404
    assert response.payload()["error"]["code"] == ApiErrorCode.RESOURCE_NOT_FOUND
    assert service.fetch_spy.calls == network_calls == []


def test_evidence_rechecks_current_policy_without_starting_a_fetch(
    app: FastAPI,
    service: _FakeVersionEvidenceService,
    network_calls: list[str],
) -> None:
    service.evidence_policy_allowed = False

    response = _request(
        app,
        "GET",
        f"/api/v1/source-versions/{_VERSION_LATEST_ID}/evidence",
        query={"extraction_revision_id": str(_REVISION_ID)},
        headers=_owner_headers(),
    )

    assert response.status_code == 403
    assert response.payload()["error"]["code"] == ApiErrorCode.SOURCE_POLICY_BLOCKED
    assert service.policy_checks == ["evidence"]
    assert service.fetch_spy.calls == network_calls == []


def test_version_and_evidence_reads_hide_non_owned_and_unknown_resources_equally(
    app: FastAPI,
    service: _FakeVersionEvidenceService,
    network_calls: list[str],
) -> None:
    non_owned = _request(
        app,
        "GET",
        f"/api/v1/sources/{_OTHER_SOURCE_ID}/versions",
        headers=_owner_headers(),
    )
    unknown = _request(
        app,
        "GET",
        "/api/v1/sources/00000000-0000-4000-8000-000000000699/versions",
        headers=_owner_headers(),
    )
    other_owner = _request(
        app,
        "GET",
        f"/api/v1/source-versions/{_VERSION_LATEST_ID}/evidence",
        query={"extraction_revision_id": str(_REVISION_ID)},
        headers=_other_owner_headers(),
    )

    _assert_indistinguishable_not_found(non_owned, unknown, other_owner)
    assert service.fetch_spy.calls == network_calls == []


def test_refresh_requires_idempotency_key_and_schema_conforming_body_before_service(
    app: FastAPI,
    service: _FakeVersionEvidenceService,
    network_calls: list[str],
) -> None:
    missing_key = _request(
        app,
        "POST",
        f"/api/v1/sources/{_SOURCE_ID}/refresh",
        headers=_owner_headers(),
        body={},
    )
    invalid_body = _request(
        app,
        "POST",
        f"/api/v1/sources/{_SOURCE_ID}/refresh",
        headers=_owner_headers(**{"Idempotency-Key": "invalid-refresh-body"}),
        body={"analysis_request_id": str(_ANALYSIS_REQUEST_ID), "url": "https://attacker.test"},
    )

    assert missing_key.status_code == invalid_body.status_code == 422
    assert missing_key.payload()["error"]["code"] == ApiErrorCode.INVALID_INPUT
    assert invalid_body.payload()["error"]["code"] == ApiErrorCode.INVALID_INPUT
    assert service.calls == []
    assert service.fetch_spy.calls == network_calls == []


def test_source_retry_is_not_registered_in_w2(
    app: FastAPI,
    service: _FakeVersionEvidenceService,
    network_calls: list[str],
) -> None:
    response = _request(
        app,
        "POST",
        f"/api/v1/sources/{_SOURCE_ID}/retry",
        headers=_owner_headers(**{"Idempotency-Key": "must-be-owned-by-w1"}),
        body={
            "job_id": str(_COMPLETED_JOB_ID),
            "expected_input_version": 1,
            "expected_result_version": 1,
        },
    )

    assert response.status_code == 404
    assert service.calls == []
    assert service.fetch_spy.calls == network_calls == []


def test_refresh_hides_non_owned_and_unknown_sources_without_creating_private_work(
    app: FastAPI,
    service: _FakeVersionEvidenceService,
    network_calls: list[str],
) -> None:
    initial_job_ids = set(service.jobs)
    non_owned = _request(
        app,
        "POST",
        f"/api/v1/sources/{_SOURCE_ID}/refresh",
        headers=_other_owner_headers(**{"Idempotency-Key": "other-owner-refresh"}),
        body={},
    )
    unknown = _request(
        app,
        "POST",
        "/api/v1/sources/00000000-0000-4000-8000-000000000699/refresh",
        headers=_owner_headers(**{"Idempotency-Key": "unknown-source-refresh"}),
        body={},
    )

    _assert_indistinguishable_not_found(non_owned, unknown)
    assert set(service.jobs) == initial_job_ids
    assert service.refresh_records == {}
    assert service.dispatches == []
    assert service.fetch_spy.calls == network_calls == []


def test_refresh_creates_one_new_job_replays_same_key_and_preserves_completed_history(
    app: FastAPI,
    service: _FakeVersionEvidenceService,
    network_calls: list[str],
) -> None:
    headers = _owner_headers(**{"Idempotency-Key": "refresh-source-replay"})
    body = {"analysis_request_id": str(_ANALYSIS_REQUEST_ID)}
    first = _request(
        app,
        "POST",
        f"/api/v1/sources/{_SOURCE_ID}/refresh",
        headers=headers,
        body=body,
    )
    replay = _request(
        app,
        "POST",
        f"/api/v1/sources/{_SOURCE_ID}/refresh",
        headers=headers,
        body=body,
    )
    refresh_job_id = UUID(first.payload()["job_id"])
    service.complete_refresh(refresh_job_id, content_hash="c" * 64)

    assert first.status_code == replay.status_code == 202
    assert replay.payload() == first.payload()
    assert first.payload()["source_id"] == str(_SOURCE_ID)
    assert first.payload()["job_id"] != str(_COMPLETED_JOB_ID)
    assert first.payload()["status_url"] == f"/api/v1/jobs/{first.payload()['job_id']}"
    assert set(first.payload()) == {"source_id", "job_id", "status_url"}
    assert service.jobs[_COMPLETED_JOB_ID]["status"] == "SUCCEEDED"
    assert service.completed_results[_COMPLETED_JOB_ID]["source_version_id"] == str(
        _VERSION_LATEST_ID
    )
    assert len(service.source_versions) == 3
    assert service.fetch_spy.calls == network_calls == []


def test_refresh_rejects_changed_payload_for_reused_key_and_honors_current_policy(
    app: FastAPI,
    service: _FakeVersionEvidenceService,
    network_calls: list[str],
) -> None:
    headers = _owner_headers(**{"Idempotency-Key": "refresh-source-conflict"})
    accepted = _request(
        app,
        "POST",
        f"/api/v1/sources/{_SOURCE_ID}/refresh",
        headers=headers,
        body={"analysis_request_id": str(_ANALYSIS_REQUEST_ID)},
    )
    conflict = _request(
        app,
        "POST",
        f"/api/v1/sources/{_SOURCE_ID}/refresh",
        headers=headers,
        body={"analysis_request_id": str(_OTHER_ANALYSIS_REQUEST_ID)},
    )
    service.refresh_policy_allowed = False
    policy_blocked = _request(
        app,
        "POST",
        f"/api/v1/sources/{_SOURCE_ID}/refresh",
        headers=_owner_headers(**{"Idempotency-Key": "refresh-source-policy"}),
        body={},
    )

    assert accepted.status_code == 202
    assert conflict.status_code == 409
    assert conflict.payload()["error"]["code"] == ApiErrorCode.IDEMPOTENCY_CONFLICT
    assert policy_blocked.status_code == 403
    assert policy_blocked.payload()["error"]["code"] == ApiErrorCode.SOURCE_POLICY_BLOCKED
    assert service.policy_checks == ["refresh", "refresh", "refresh"]
    assert service.fetch_spy.calls == network_calls == []


def test_refresh_with_no_execution_slot_queues_a_new_job_and_changed_content_keeps_history(
    app: FastAPI,
    service: _FakeVersionEvidenceService,
    network_calls: list[str],
) -> None:
    service.slots_available = False
    queued = _request(
        app,
        "POST",
        f"/api/v1/sources/{_SOURCE_ID}/refresh",
        headers=_owner_headers(**{"Idempotency-Key": "refresh-source-queued"}),
        body={},
    )
    queued_job_id = UUID(queued.payload()["job_id"])
    service.complete_refresh(queued_job_id, content_hash="d" * 64)

    assert queued.status_code == 202
    assert service.jobs[queued_job_id]["slot_acquired"] is False
    assert service.jobs[queued_job_id]["status"] == "SUCCEEDED"
    assert [item["source_version_id"] for item in service.source_versions] == [
        str(_VERSION_EARLIEST_ID),
        str(_VERSION_TIE_ID),
        str(_VERSION_LATEST_ID),
        str(_VERSION_CHANGED_ID),
    ]
    assert service.completed_results[_COMPLETED_JOB_ID]["evidence_ids"] == [
        str(_EVIDENCE_TIE_FIRST_ID)
    ]
    assert service.fetch_spy.calls == network_calls == []
