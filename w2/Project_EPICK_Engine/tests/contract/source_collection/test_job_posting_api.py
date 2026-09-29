"""HTTP contracts for future job-posting routes.

T029/T034 public factory contract, intentionally fixed here for TDD:

``create_job_posting_api(*, service, authenticator, settings)`` returns a
``FastAPI`` application with the two W2-owned routes exercised below.
``service`` provides ``import_job_posting`` and ``get_job_posting``. The
factory owns HTTP authentication, UUID/query and idempotency-key validation,
safe error rendering, and public response projection. W1 owns every public
Job retry action, so this factory must not expose a retry route.

The fake service's deterministic idempotency, ownership, and selected-version
branches are boundary-observation switches only. T033/T036 own their production
service implementations; this module captures inputs and asserts public HTTP
responses instead of prescribing their internal algorithms.

``/api/v1/jobs`` is W1-owned. These tests verify only the returned
``status_url`` and never implement or query that route. No ``TestClient`` is
used: requests enter the ASGI application through an in-memory scope,
receive, and send implementation.
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import Awaitable, Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, cast
from urllib.parse import urlencode
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from starlette.types import Message, Receive, Scope, Send

import epick_engine.source_collection.api as api
from epick_engine.source_collection.api import (
    ApiBoundarySettings,
    ApiErrorCode,
    ApiProblem,
    AuthenticatedPrincipal,
)
from epick_engine.source_collection.contracts import (
    CollectionStage,
    ExtractionStatus,
    Failure,
    PostingSection,
    PostingSectionKind,
)
from epick_engine.source_collection.service import (
    JobPostingEvidenceSnapshot,
    JobPostingFailureReference,
    JobPostingView,
)

_OWNER_ID = UUID("00000000-0000-4000-8000-000000009101")
_OTHER_OWNER_ID = UUID("00000000-0000-4000-8000-000000009102")
_COMPANY_ID = UUID("00000000-0000-4000-8000-000000000201")
_SOURCE_ID = UUID("00000000-0000-4000-8000-000000000501")
_OTHER_SOURCE_ID = UUID("00000000-0000-4000-8000-000000000502")
_SOURCE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000601")
_OTHER_SOURCE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000602")
_CURRENT_EXTRACTION_REVISION_ID = UUID("00000000-0000-4000-8000-000000000701")
_EARLIER_EXTRACTION_REVISION_ID = UUID("00000000-0000-4000-8000-000000000702")
_OTHER_EXTRACTION_REVISION_ID = UUID("00000000-0000-4000-8000-000000000703")
_EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000751")
_JOB_POSTING_ID = UUID("00000000-0000-4000-8000-000000000801")
_OTHER_JOB_POSTING_ID = UUID("00000000-0000-4000-8000-000000000802")
_IMPORT_JOB_ID = UUID("00000000-0000-4000-8000-000000000901")
_RETRY_JOB_ID = UUID("00000000-0000-4000-8000-000000000902")
_OTHER_IMPORT_JOB_ID = UUID("00000000-0000-4000-8000-000000000903")
_PRIVATE_SENTINEL_KEY = "__unexpected_private_sentinel__"
_PRIVATE_SENTINEL_VALUE = "test-only-private-sentinel-6a4385c5"
_W3_PUBLIC_SENTINEL = "w3-service-handoff-9c768a9f"
_IMPORT_RESPONSE_KEYS = frozenset({"job_posting_id", "source_id", "job_id", "status_url"})
_GET_RESPONSE_KEYS = frozenset({"job_posting"})
_JOB_POSTING_RESPONSE_KEYS = frozenset(
    {
        "job_posting_id",
        "company_id",
        "source_id",
        "source_version_id",
        "extraction_revision_id",
        "title",
        "sections",
    }
)
_RETRY_RESPONSE_KEYS = frozenset({"job_posting_id", "job_id", "status_url", "resume_stage"})
_W3_RETRY_RESPONSE_KEYS = frozenset({*_RETRY_RESPONSE_KEYS, "w3_handoff"})
_SETTINGS = ApiBoundarySettings(
    idempotency_key_max_length=256,
    cursor_max_token_length=1_024,
    page_default_limit=20,
    page_max_limit=100,
)
_API_EVENT_LOOP: asyncio.AbstractEventLoop | None = None


@dataclass(frozen=True)
class _AsgiResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes

    def payload(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.body))


@dataclass
class _FakeJobPostingService:
    """Test-only deterministic response double for the HTTP boundary."""

    import_records: dict[tuple[UUID, str], tuple[dict[str, Any], dict[str, Any]]] = field(
        default_factory=dict
    )
    retry_records: dict[tuple[UUID, UUID, str], tuple[dict[str, Any], dict[str, Any]]] = field(
        default_factory=dict
    )
    import_count: int = 0
    retry_count: int = 0
    get_count: int = 0
    calls: list[str] = field(default_factory=list)
    retry_call_arguments: list[dict[str, Any]] = field(default_factory=list)
    delegate_w3_failure: bool = False
    view_result: JobPostingView | None = None

    def import_job_posting(
        self,
        *,
        owner_user_id: UUID,
        company_id: UUID,
        url: str,
        analysis_request_id: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.calls.append("import_job_posting")
        payload = {
            "company_id": str(company_id),
            "url": url,
            "analysis_request_id": analysis_request_id,
        }
        key = (owner_user_id, idempotency_key)
        existing = self.import_records.get(key)
        if existing is not None:
            existing_payload, existing_response = existing
            if existing_payload != payload:
                raise ApiProblem(ApiErrorCode.IDEMPOTENCY_CONFLICT)
            return deepcopy(existing_response)

        job_id = _IMPORT_JOB_ID if owner_user_id == _OWNER_ID else _OTHER_IMPORT_JOB_ID
        response = {
            "job_posting_id": str(_JOB_POSTING_ID),
            "source_id": str(_SOURCE_ID),
            "job_id": str(job_id),
            "status_url": f"/api/v1/jobs/{job_id}",
            _PRIVATE_SENTINEL_KEY: _PRIVATE_SENTINEL_VALUE,
        }
        self.import_records[key] = (payload, response)
        self.import_count += 1
        return deepcopy(response)

    def get_job_posting(
        self,
        *,
        owner_user_id: UUID,
        job_posting_id: UUID,
        source_version_id: UUID | None,
        extraction_revision_id: UUID | None,
    ) -> object:
        self.calls.append("get_job_posting")
        self.get_count += 1
        if (job_posting_id, owner_user_id) not in {
            (_JOB_POSTING_ID, _OWNER_ID),
            (_OTHER_JOB_POSTING_ID, _OTHER_OWNER_ID),
        }:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND)

        selected_version = source_version_id or _SOURCE_VERSION_ID
        selected_revision = extraction_revision_id or _CURRENT_EXTRACTION_REVISION_ID
        compatible_revisions = {
            _CURRENT_EXTRACTION_REVISION_ID,
            _EARLIER_EXTRACTION_REVISION_ID,
        }
        if selected_version != _SOURCE_VERSION_ID:
            raise ApiProblem(ApiErrorCode.INVALID_INPUT)
        if selected_revision not in compatible_revisions:
            raise ApiProblem(ApiErrorCode.INVALID_INPUT)

        if self.view_result is not None:
            return self.view_result

        return {
            "job_posting": {
                "job_posting_id": str(_JOB_POSTING_ID),
                "company_id": str(_COMPANY_ID),
                "source_id": str(_SOURCE_ID),
                "source_version_id": str(selected_version),
                "extraction_revision_id": str(selected_revision),
                "title": "Synthetic platform engineer",
                "sections": [
                    {
                        "kind": "required",
                        "text": "Python experience",
                        "evidence": [
                            {
                                "evidence_id": "synthetic-evidence-001",
                                "excerpt": "Python experience",
                            }
                        ],
                    }
                ],
            },
            "private_w1_command": {"authenticated_owner_ref": "private-owner"},
            "raw_body": "private source document body",
            _PRIVATE_SENTINEL_KEY: _PRIVATE_SENTINEL_VALUE,
        }

    def retry_job_posting(
        self,
        *,
        owner_user_id: UUID,
        job_posting_id: UUID,
        job_id: UUID,
        expected_input_version: int,
        expected_result_version: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.calls.append("retry_job_posting")
        self.retry_call_arguments.append(
            {
                "owner_user_id": owner_user_id,
                "job_posting_id": job_posting_id,
                "job_id": job_id,
                "expected_input_version": expected_input_version,
                "expected_result_version": expected_result_version,
                "idempotency_key": idempotency_key,
            }
        )
        if (job_posting_id, owner_user_id) not in {
            (_JOB_POSTING_ID, _OWNER_ID),
            (_OTHER_JOB_POSTING_ID, _OTHER_OWNER_ID),
        }:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND)

        payload = {
            "job_id": str(job_id),
            "expected_input_version": expected_input_version,
            "expected_result_version": expected_result_version,
        }
        key = (owner_user_id, job_posting_id, idempotency_key)
        existing = self.retry_records.get(key)
        if existing is not None:
            existing_payload, existing_response = existing
            if existing_payload != payload:
                raise ApiProblem(ApiErrorCode.IDEMPOTENCY_CONFLICT)
            return deepcopy(existing_response)

        response: dict[str, Any] = {
            "job_posting_id": str(job_posting_id),
            "job_id": str(_RETRY_JOB_ID),
            "status_url": f"/api/v1/jobs/{_RETRY_JOB_ID}",
            "resume_stage": "parse",
            "private_w1_command": {"execution_fence": "private-fence"},
            "raw_body": "private source document body",
            _PRIVATE_SENTINEL_KEY: _PRIVATE_SENTINEL_VALUE,
        }
        if self.delegate_w3_failure:
            response["w3_handoff"] = {
                "owner": "W3",
                "status": "FAILED",
                "code": "CLAIM_INDEX_REJECTED",
                "handoff_ref": _W3_PUBLIC_SENTINEL,
            }
            response["private_w3_failure"] = {"stack_trace": "private W3 detail"}

        self.retry_records[key] = (payload, response)
        self.retry_count += 1
        return deepcopy(response)


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


def _app(service: _FakeJobPostingService) -> FastAPI:
    factory = getattr(api, "create_job_posting_api", None)
    assert callable(factory), (
        "T034 must provide create_job_posting_api(*, service, authenticator, settings) -> FastAPI"
    )
    return cast(Any, factory)(
        service=service,
        authenticator=_authenticate,
        settings=_SETTINGS,
    )


def _owner_headers(**headers: str) -> dict[str, str]:
    return {"authorization": "Bearer synthetic-owner", **headers}


def _other_owner_headers(**headers: str) -> dict[str, str]:
    return {"authorization": "Bearer synthetic-other-owner", **headers}


def _error_code(response: _AsgiResponse) -> str:
    return cast(str, response.payload()["error"]["code"])


def _failure_reference() -> JobPostingFailureReference:
    return JobPostingFailureReference(
        failure=Failure(
            source_id=_SOURCE_ID,
            stage=CollectionStage.PARSE,
            code="private-failure-code",
            missing_sections=[],
            impact=_PRIVATE_SENTINEL_VALUE,
            core_decision_revision=1,
        ),
        checkpoint_ref="private-checkpoint-reference",
        required_actions=(),
    )


def _assert_public_response(payload: Mapping[str, Any], expected_keys: frozenset[str]) -> None:
    assert set(payload) == expected_keys
    serialized_payload = json.dumps(payload)
    assert _PRIVATE_SENTINEL_KEY not in serialized_payload
    assert _PRIVATE_SENTINEL_VALUE not in serialized_payload


@pytest.fixture
def network_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record and block any route-owned network attempt during in-memory ASGI calls."""

    calls: list[str] = []

    def record_and_block(operation: str) -> None:
        calls.append(operation)
        raise AssertionError("HTTP read and retry routes must not start network I/O")

    def block_getaddrinfo(*_args: object, **_kwargs: object) -> Any:
        record_and_block("socket.getaddrinfo")

    def block_create_connection(*_args: object, **_kwargs: object) -> Any:
        record_and_block("socket.create_connection")

    original_socket = socket.socket

    class _NetworkBlockedSocket(original_socket):
        def connect(self, _address: object) -> None:
            record_and_block("socket.socket.connect")

    monkeypatch.setattr(socket, "getaddrinfo", block_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", block_create_connection)
    monkeypatch.setattr(socket, "socket", _NetworkBlockedSocket)
    return calls


@pytest.fixture
def service() -> _FakeJobPostingService:
    return _FakeJobPostingService()


@pytest.fixture
def app(service: _FakeJobPostingService) -> FastAPI:
    return _app(service)


@pytest.mark.parametrize(
    ("method", "path", "body", "headers"),
    [
        (
            "POST",
            "/api/v1/job-postings/import",
            {"company_id": str(_COMPANY_ID), "url": "https://synthetic.test/jobs/1"},
            {"Idempotency-Key": "unauthenticated-import"},
        ),
        ("GET", f"/api/v1/job-postings/{_JOB_POSTING_ID}", None, {}),
    ],
)
def test_job_posting_routes_require_authentication_before_service(
    app: FastAPI,
    service: _FakeJobPostingService,
    network_calls: list[str],
    method: str,
    path: str,
    body: Mapping[str, Any] | None,
    headers: Mapping[str, str],
) -> None:
    response = _request(app, method, path, headers=headers, body=body)

    assert response.status_code == 401
    assert _error_code(response) == ApiErrorCode.AUTHENTICATION_REQUIRED
    assert service.calls == []
    assert network_calls == []


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/api/v1/job-postings/import",
            {"company_id": str(_COMPANY_ID), "url": "https://synthetic.test/jobs/1"},
        ),
    ],
)
def test_job_posting_writes_require_idempotency_key_before_service(
    app: FastAPI,
    service: _FakeJobPostingService,
    network_calls: list[str],
    path: str,
    body: Mapping[str, Any],
) -> None:
    response = _request(app, "POST", path, headers=_owner_headers(), body=body)

    assert response.status_code == 422
    assert _error_code(response) == ApiErrorCode.INVALID_INPUT
    assert service.calls == []
    assert network_calls == []


def test_job_posting_import_returns_202_and_replays_same_idempotency_key(
    app: FastAPI,
    service: _FakeJobPostingService,
) -> None:
    headers = _owner_headers(**{"Idempotency-Key": "import-posting-replay"})
    body = {
        "company_id": str(_COMPANY_ID),
        "url": "https://synthetic.test/jobs/1",
        "analysis_request_id": "analysis-001",
    }
    first = _request(app, "POST", "/api/v1/job-postings/import", headers=headers, body=body)
    replay = _request(app, "POST", "/api/v1/job-postings/import", headers=headers, body=body)

    assert first.status_code == replay.status_code == 202
    assert replay.payload() == first.payload()
    _assert_public_response(first.payload(), _IMPORT_RESPONSE_KEYS)
    assert first.payload()["status_url"] == f"/api/v1/jobs/{_IMPORT_JOB_ID}"
    assert service.import_count == 1


def test_job_posting_import_rejects_changed_payload_for_reused_idempotency_key(
    app: FastAPI,
    service: _FakeJobPostingService,
) -> None:
    headers = _owner_headers(**{"Idempotency-Key": "import-posting-conflict"})
    first = _request(
        app,
        "POST",
        "/api/v1/job-postings/import",
        headers=headers,
        body={"company_id": str(_COMPANY_ID), "url": "https://synthetic.test/jobs/1"},
    )
    conflict = _request(
        app,
        "POST",
        "/api/v1/job-postings/import",
        headers=headers,
        body={"company_id": str(_COMPANY_ID), "url": "https://synthetic.test/jobs/2"},
    )

    assert first.status_code == 202
    _assert_public_response(first.payload(), _IMPORT_RESPONSE_KEYS)
    assert conflict.status_code == 409
    assert _error_code(conflict) == ApiErrorCode.IDEMPOTENCY_CONFLICT
    assert service.import_count == 1


def test_job_posting_import_scopes_idempotency_key_to_authenticated_principal(
    app: FastAPI,
    service: _FakeJobPostingService,
) -> None:
    body = {"company_id": str(_COMPANY_ID), "url": "https://synthetic.test/jobs/1"}
    owner = _request(
        app,
        "POST",
        "/api/v1/job-postings/import",
        headers=_owner_headers(**{"Idempotency-Key": "same-key-different-owner"}),
        body=body,
    )
    other_owner = _request(
        app,
        "POST",
        "/api/v1/job-postings/import",
        headers=_other_owner_headers(**{"Idempotency-Key": "same-key-different-owner"}),
        body=body,
    )

    assert owner.status_code == other_owner.status_code == 202
    _assert_public_response(owner.payload(), _IMPORT_RESPONSE_KEYS)
    _assert_public_response(other_owner.payload(), _IMPORT_RESPONSE_KEYS)
    assert owner.payload()["status_url"] != other_owner.payload()["status_url"]
    assert service.import_count == 2


def test_job_posting_get_returns_explicit_version_and_revision_without_private_body_or_fetch(
    app: FastAPI,
    service: _FakeJobPostingService,
    network_calls: list[str],
) -> None:
    current = _request(
        app,
        "GET",
        f"/api/v1/job-postings/{_JOB_POSTING_ID}",
        headers=_owner_headers(),
    )
    version_only = _request(
        app,
        "GET",
        f"/api/v1/job-postings/{_JOB_POSTING_ID}",
        query={"source_version_id": str(_SOURCE_VERSION_ID)},
        headers=_owner_headers(),
    )
    selected = _request(
        app,
        "GET",
        f"/api/v1/job-postings/{_JOB_POSTING_ID}",
        query={
            "source_version_id": str(_SOURCE_VERSION_ID),
            "extraction_revision_id": str(_EARLIER_EXTRACTION_REVISION_ID),
        },
        headers=_owner_headers(),
    )

    assert current.status_code == version_only.status_code == selected.status_code == 200
    _assert_public_response(current.payload(), _GET_RESPONSE_KEYS)
    _assert_public_response(version_only.payload(), _GET_RESPONSE_KEYS)
    _assert_public_response(selected.payload(), _GET_RESPONSE_KEYS)
    assert set(current.payload()["job_posting"]) == _JOB_POSTING_RESPONSE_KEYS
    assert set(version_only.payload()["job_posting"]) == _JOB_POSTING_RESPONSE_KEYS
    assert set(selected.payload()["job_posting"]) == _JOB_POSTING_RESPONSE_KEYS
    assert current.payload()["job_posting"]["source_version_id"] == str(_SOURCE_VERSION_ID)
    assert current.payload()["job_posting"]["extraction_revision_id"] == str(
        _CURRENT_EXTRACTION_REVISION_ID
    )
    assert version_only.payload()["job_posting"]["source_version_id"] == str(_SOURCE_VERSION_ID)
    assert version_only.payload()["job_posting"]["extraction_revision_id"] == str(
        _CURRENT_EXTRACTION_REVISION_ID
    )
    assert selected.payload()["job_posting"]["source_version_id"] == str(_SOURCE_VERSION_ID)
    assert selected.payload()["job_posting"]["extraction_revision_id"] == str(
        _EARLIER_EXTRACTION_REVISION_ID
    )
    assert "raw_body" not in json.dumps(selected.payload())
    assert "private_w1_command" not in json.dumps(selected.payload())
    assert network_calls == []


def test_job_posting_get_rejects_failure_only_service_view_without_private_detail(
    app: FastAPI,
    service: _FakeJobPostingService,
    network_calls: list[str],
) -> None:
    service.view_result = JobPostingView(
        job_posting_id=_JOB_POSTING_ID,
        company_id=_COMPANY_ID,
        source_id=_SOURCE_ID,
        source_version_id=None,
        extraction_revision_id=None,
        title=None,
        extraction_status=None,
        sections=(),
        limitations=(),
        failure_reference=_failure_reference(),
    )

    response = _request(
        app,
        "GET",
        f"/api/v1/job-postings/{_JOB_POSTING_ID}",
        headers=_owner_headers(),
    )

    assert response.status_code == 503
    assert _error_code(response) == ApiErrorCode.DEPENDENCY_UNAVAILABLE
    assert "failure_reference" not in json.dumps(response.payload())
    assert "private-failure-code" not in json.dumps(response.payload())
    assert _PRIVATE_SENTINEL_VALUE not in json.dumps(response.payload())
    assert network_calls == []


def test_job_posting_get_returns_partial_service_view_with_usable_revision(
    app: FastAPI,
    service: _FakeJobPostingService,
    network_calls: list[str],
) -> None:
    service.view_result = JobPostingView(
        job_posting_id=_JOB_POSTING_ID,
        company_id=_COMPANY_ID,
        source_id=_SOURCE_ID,
        source_version_id=_SOURCE_VERSION_ID,
        extraction_revision_id=_CURRENT_EXTRACTION_REVISION_ID,
        title="Synthetic platform engineer",
        extraction_status=ExtractionStatus.PARTIAL,
        sections=(
            PostingSection(
                section_key="required",
                kind=PostingSectionKind.REQUIRED,
                heading_raw=None,
                text_raw="Python experience",
                evidence_ids=[_EVIDENCE_ID],
                order=0,
                relation_text=None,
            ),
        ),
        limitations=("Some content is unavailable.",),
        failure_reference=_failure_reference(),
        evidence=(
            JobPostingEvidenceSnapshot(
                evidence_id=_EVIDENCE_ID,
                source_version_id=_SOURCE_VERSION_ID,
                text_excerpt="Python experience",
            ),
        ),
    )

    response = _request(
        app,
        "GET",
        f"/api/v1/job-postings/{_JOB_POSTING_ID}",
        headers=_owner_headers(),
    )

    assert response.status_code == 200
    _assert_public_response(response.payload(), _GET_RESPONSE_KEYS)
    assert list(response.payload()) == ["job_posting"]
    assert list(response.payload()["job_posting"]) == [
        "job_posting_id",
        "company_id",
        "source_id",
        "source_version_id",
        "extraction_revision_id",
        "title",
        "sections",
    ]
    assert response.payload()["job_posting"]["source_version_id"] == str(_SOURCE_VERSION_ID)
    assert response.payload()["job_posting"]["extraction_revision_id"] == str(
        _CURRENT_EXTRACTION_REVISION_ID
    )
    assert response.payload()["job_posting"]["sections"] == [
        {
            "kind": "required",
            "text": "Python experience",
            "evidence": [
                {
                    "evidence_id": str(_EVIDENCE_ID),
                    "excerpt": "Python experience",
                }
            ],
        }
    ]
    assert network_calls == []


@pytest.mark.parametrize(
    "query",
    [
        pytest.param(
            {
                "source_version_id": str(_OTHER_SOURCE_VERSION_ID),
                "extraction_revision_id": str(_CURRENT_EXTRACTION_REVISION_ID),
            },
            id="source-version-belongs-to-another-source",
        ),
        pytest.param(
            {
                "source_version_id": str(_SOURCE_VERSION_ID),
                "extraction_revision_id": str(_OTHER_EXTRACTION_REVISION_ID),
            },
            id="revision-does-not-belong-to-selected-version",
        ),
    ],
)
def test_job_posting_get_rejects_other_source_version_or_incompatible_revision(
    app: FastAPI,
    service: _FakeJobPostingService,
    network_calls: list[str],
    query: Mapping[str, str],
) -> None:
    response = _request(
        app,
        "GET",
        f"/api/v1/job-postings/{_JOB_POSTING_ID}",
        query=query,
        headers=_owner_headers(),
    )

    assert response.status_code == 422
    assert _error_code(response) == ApiErrorCode.INVALID_INPUT
    assert network_calls == []


def test_job_posting_get_hides_non_owned_and_unknown_resources_equally(
    app: FastAPI,
    service: _FakeJobPostingService,
    network_calls: list[str],
) -> None:
    non_owned = _request(
        app,
        "GET",
        f"/api/v1/job-postings/{_OTHER_JOB_POSTING_ID}",
        headers=_owner_headers(),
    )
    unknown = _request(
        app,
        "GET",
        "/api/v1/job-postings/00000000-0000-4000-8000-000000000899",
        headers=_owner_headers(),
    )

    assert non_owned.status_code == unknown.status_code == 404
    assert _error_code(non_owned) == _error_code(unknown) == ApiErrorCode.RESOURCE_NOT_FOUND
    assert network_calls == []


def test_job_posting_retry_is_not_registered_in_w2(
    app: FastAPI,
    service: _FakeJobPostingService,
    network_calls: list[str],
) -> None:
    response = _request(
        app,
        "POST",
        f"/api/v1/job-postings/{_JOB_POSTING_ID}/retry",
        headers=_owner_headers(**{"Idempotency-Key": "must-be-owned-by-w1"}),
        body={
            "job_id": str(_IMPORT_JOB_ID),
            "expected_input_version": 1,
            "expected_result_version": 1,
        },
    )

    assert response.status_code == 404
    assert service.calls == []
    assert service.retry_count == 0
    assert network_calls == []
