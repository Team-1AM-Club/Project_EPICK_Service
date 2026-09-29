"""HTTP contracts for future company and Source routes.

T021/T025 public factory contract, intentionally fixed here for TDD:

``create_company_source_api(*, service, authenticator, settings, cursor_codec)``
returns a ``FastAPI`` application with the six routes exercised below.  ``service``
provides ``search_companies``, ``resolve_company``, ``get_company``,
``register_source``, ``list_sources``, and ``get_source``.  The factory owns HTTP
validation, authentication, cursor decoding, idempotency-key validation, error
rendering, and removal of W1-private fields from public Source reads.

No TestClient is used: requests enter the ASGI application directly through
an in-memory scope, receive, and send implementation.
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
from fastapi import FastAPI, HTTPException, Request
from starlette.types import Message, Receive, Scope, Send

import epick_engine.source_collection.api as api
from epick_engine.source_collection.api import (
    ApiBoundarySettings,
    ApiErrorCode,
    ApiProblem,
    AuthenticatedPrincipal,
    CursorCodec,
)

ENGINE_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = ENGINE_ROOT / "tests" / "fixtures" / "synthetic_sources" / "companies.json"
_OWNER_ID = UUID("00000000-0000-4000-8000-000000009001")
_OTHER_OWNER_ID = UUID("00000000-0000-4000-8000-000000009002")
_COMPANY_A_ID = UUID("00000000-0000-4000-8000-000000000101")
_COMPANY_B_ID = UUID("00000000-0000-4000-8000-000000000102")
_SUBSIDIARY_ID = UUID("00000000-0000-4000-8000-000000000103")
_SOURCE_ID = UUID("00000000-0000-4000-8000-000000000401")
_OTHER_SOURCE_ID = UUID("00000000-0000-4000-8000-000000000402")
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
class _FetchSpy:
    calls: list[str] = field(default_factory=list)

    def fetch(self, url: str) -> None:
        self.calls.append(url)
        raise AssertionError("GET routes must not start an external fetch")


@dataclass(frozen=True)
class _RaisingCursorCodec:
    phase: str

    def decode(self, *_args: object, **_kwargs: object) -> object:
        if self.phase == "decode":
            raise HTTPException(status_code=418, detail="raw-cursor-codec-detail")
        return 0

    def encode(self, *_args: object, **_kwargs: object) -> str:
        if self.phase == "encode":
            raise HTTPException(status_code=418, detail="raw-cursor-codec-detail")
        return "synthetic-cursor"


@dataclass
class _FakeCompanySourceService:
    fixture: dict[str, Any]
    fetch_spy: _FetchSpy
    sources: dict[UUID, dict[str, Any]] = field(init=False)
    idempotency_records: dict[tuple[UUID, UUID, str], tuple[dict[str, Any], dict[str, Any]]] = (
        field(default_factory=dict)
    )
    jobs: dict[UUID, dict[str, Any]] = field(default_factory=dict)
    registration_count: int = 0
    resolution_count: int = 0
    search_limits: list[int] = field(default_factory=list)
    raise_raw_http_exception: bool = False

    def __post_init__(self) -> None:
        source_rows = self.fixture["source_attributions"]
        self.sources = {
            _SOURCE_ID: self._source_record(source_rows[0], owner_user_id=_OWNER_ID),
            _OTHER_SOURCE_ID: self._source_record(source_rows[1], owner_user_id=_OTHER_OWNER_ID),
        }

    def _candidate(self, company_id: UUID) -> dict[str, Any]:
        company = next(
            row for row in self.fixture["companies"] if row["company_id"] == str(company_id)
        )
        return {
            "company_id": company["company_id"],
            "legal_name": company["legal_name"],
            "identity_evidence_refs": company["identity_evidence"],
        }

    def _source_record(self, row: dict[str, Any], *, owner_user_id: UUID) -> dict[str, Any]:
        return {
            "source_id": str(row["source_id"]),
            "company_id": str(row["company_id"]),
            "url": row["url"],
            "source_type": "job_posting",
            "policy": {
                "official_status": "verified",
                "access_class": "public",
                "collection_permission": "allowed",
                "excerpt_storage_permission": "allowed",
                "body_storage_permission": "unknown",
                "redistribution_permission": "unknown",
                "revision": 1,
            },
            "current_observation": None,
            "latest_available_version": None,
            "current_restriction": {"state": "not_evaluated"},
            "freshness": {"state": "not_collected"},
            "actions": [{"action": "REQUEST_COLLECTION"}],
            "owner_user_id": owner_user_id,
            "private_w1_command": {
                "authenticated_owner_ref": str(owner_user_id),
                "purpose_ref": "private-purpose-ref",
                "execution_fence": "private-execution-fence",
            },
        }

    def search_companies(
        self,
        *,
        owner_user_id: UUID,
        query: str,
        official_domain: str | None,
        cursor: object | None,
        limit: int,
    ) -> dict[str, Any]:
        del owner_user_id
        self.search_limits.append(limit)
        candidates = []
        for company in self.fixture["companies"]:
            domains = company["official_domains"]
            names = [company["legal_name"], *company["aliases"]]
            if query.lower() not in " ".join(names).lower():
                continue
            if official_domain is not None and official_domain not in domains:
                continue
            candidates.append(
                {
                    "company_id": company["company_id"],
                    "legal_name": company["legal_name"],
                    "identity_evidence_refs": company["identity_evidence"],
                }
            )
        offset = cast(int, cursor) if cursor is not None else 0
        page = candidates[offset : offset + limit]
        next_offset = offset + len(page)
        return {
            "candidates": page,
            "selection_required": len(candidates) > 1,
            "next_cursor": next_offset if next_offset < len(candidates) else None,
        }

    def resolve_company(
        self,
        *,
        owner_user_id: UUID,
        name: str | None,
        official_identifiers: list[str] | None,
        selected_company_id: UUID | None,
        identity_evidence_refs: list[str] | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        del owner_user_id, official_identifiers, identity_evidence_refs, idempotency_key
        self.resolution_count += 1
        if selected_company_id is not None:
            return {
                "resolution": "resolved",
                "company_id": str(selected_company_id),
                "candidates": [],
            }
        if name == "Synthetic Meridian Labs Ltd.":
            return {
                "resolution": "selection_required",
                "company_id": None,
                "candidates": [
                    self._candidate(_COMPANY_A_ID),
                    self._candidate(_COMPANY_B_ID),
                ],
            }
        return {
            "resolution": "selection_required",
            "company_id": None,
            "candidates": [
                self._candidate(_COMPANY_A_ID),
                self._candidate(_SUBSIDIARY_ID),
            ],
        }

    def get_company(self, *, owner_user_id: UUID, company_id: UUID) -> dict[str, Any]:
        del owner_user_id
        if self.raise_raw_http_exception:
            raise HTTPException(status_code=500, detail="raw-internal-service-detail")
        company = next(
            (row for row in self.fixture["companies"] if row["company_id"] == str(company_id)),
            None,
        )
        if company is None:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND)
        relationships = [
            relationship
            for relationship in self.fixture["relationships"]
            if company["company_id"]
            in {relationship["company_id"], relationship["related_company_id"]}
        ]
        return {
            "company": deepcopy(company),
            "relationships": deepcopy(relationships),
        }

    def register_source(
        self,
        *,
        owner_user_id: UUID,
        company_id: UUID,
        url: str,
        source_type: str,
        analysis_request_id: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        payload = {
            "url": url,
            "source_type": source_type,
            "analysis_request_id": analysis_request_id,
        }
        key = (owner_user_id, company_id, idempotency_key)
        existing = self.idempotency_records.get(key)
        if existing is not None:
            existing_payload, existing_response = existing
            if existing_payload != payload:
                raise ApiProblem(ApiErrorCode.IDEMPOTENCY_CONFLICT)
            return deepcopy(existing_response)

        source_id = uuid4()
        job_id = uuid4()
        response = {
            "source_id": str(source_id),
            "job_id": str(job_id),
            "status_url": f"/api/v1/jobs/{job_id}",
        }
        self.sources[source_id] = {
            **self._source_record(
                {
                    "source_id": str(source_id),
                    "company_id": str(company_id),
                    "url": url,
                },
                owner_user_id=owner_user_id,
            ),
            "source_type": source_type,
        }
        self.idempotency_records[key] = (payload, response)
        self.jobs[job_id] = {
            "status": "ACCEPTED",
            "source_id": str(source_id),
        }
        self.registration_count += 1
        return deepcopy(response)

    def list_sources(
        self,
        *,
        owner_user_id: UUID,
        company_id: UUID,
        cursor: str | None,
        limit: int,
    ) -> dict[str, Any]:
        del cursor
        items = [
            deepcopy(source)
            for source in self.sources.values()
            if source["owner_user_id"] == owner_user_id and source["company_id"] == str(company_id)
        ]
        return {"items": items[:limit], "next_cursor": None}

    def get_source(self, *, owner_user_id: UUID, source_id: UUID) -> dict[str, Any]:
        source = self.sources.get(source_id)
        if source is None or source["owner_user_id"] != owner_user_id:
            raise ApiProblem(ApiErrorCode.RESOURCE_NOT_FOUND)
        return deepcopy(source)

    def fail_accepted_job(self, status_url: str) -> None:
        job_id = UUID(status_url.rsplit("/", maxsplit=1)[-1])
        source_id = UUID(self.jobs[job_id]["source_id"])
        failure = {
            "stage": "CONTENT_EXTRACTION",
            "code": "UNSUPPORTED_FORMAT",
            "impact": "Source remains registered without a collected observation.",
            "actions": [{"action": "CHOOSE_ALTERNATIVE_OFFICIAL_SOURCE"}],
        }
        self.jobs[job_id] = {
            "status": "FAILED_FINAL",
            "source_id": str(source_id),
            "failure": failure,
        }
        self.sources[source_id]["current_observation"] = {
            "acquisition_status": "failed",
            "error_code": failure["code"],
        }
        self.sources[source_id]["current_restriction"] = {
            key: failure[key] for key in ("stage", "code", "impact")
        }
        self.sources[source_id]["actions"] = failure["actions"]

    def w1_status(self, status_url: str) -> dict[str, Any]:
        job_id = UUID(status_url.rsplit("/", maxsplit=1)[-1])
        return deepcopy(self.jobs[job_id])


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
    service: _FakeCompanySourceService,
    *,
    cursor_codec: CursorCodec | None = None,
) -> FastAPI:
    factory = getattr(api, "create_company_source_api", None)
    assert callable(factory), (
        "T021/T025 must provide create_company_source_api("
        "*, service, authenticator, settings, cursor_codec) -> FastAPI"
    )
    return cast(Any, factory)(
        service=service,
        authenticator=_authenticate,
        settings=_SETTINGS,
        cursor_codec=cursor_codec
        or CursorCodec("contract-test-cursor-secret-at-least-32-bytes", _SETTINGS),
    )


def _request_company_endpoint(app: FastAPI, endpoint: str) -> _AsgiResponse:
    if endpoint == "search":
        return _request(
            app,
            "GET",
            "/api/v1/companies/search",
            query={"q": "Synthetic Meridian Labs"},
            headers=_owner_headers(),
        )
    if endpoint == "resolve":
        return _request(
            app,
            "POST",
            "/api/v1/companies/resolve",
            headers=_owner_headers(**{"Idempotency-Key": "public-response-contract"}),
            body={"name": "Synthetic Meridian Labs Ltd.", "identity_evidence_refs": []},
        )
    if endpoint == "detail":
        return _request(
            app,
            "GET",
            f"/api/v1/companies/{_COMPANY_A_ID}",
            headers=_owner_headers(),
        )
    raise AssertionError(f"unsupported company endpoint: {endpoint}")


def _company_service_method_and_response(
    service: _FakeCompanySourceService,
    endpoint: str,
) -> tuple[str, dict[str, Any]]:
    if endpoint == "search":
        return (
            "search_companies",
            service.search_companies(
                owner_user_id=_OWNER_ID,
                query="Synthetic Meridian Labs",
                official_domain=None,
                cursor=None,
                limit=20,
            ),
        )
    if endpoint == "resolve":
        return (
            "resolve_company",
            service.resolve_company(
                owner_user_id=_OWNER_ID,
                name="Synthetic Meridian Labs Ltd.",
                official_identifiers=None,
                selected_company_id=None,
                identity_evidence_refs=[],
                idempotency_key="public-response-contract",
            ),
        )
    if endpoint == "detail":
        return "get_company", service.get_company(
            owner_user_id=_OWNER_ID,
            company_id=_COMPANY_A_ID,
        )
    raise AssertionError(f"unsupported company endpoint: {endpoint}")


def _owner_headers(**headers: str) -> dict[str, str]:
    return {"authorization": "Bearer synthetic-owner", **headers}


def _error_without_correlation(response: _AsgiResponse) -> dict[str, Any]:
    payload = response.payload()
    error = dict(payload["error"])
    error.pop("correlation_id")
    return error


@pytest.fixture(autouse=True)
def _deny_api_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    def deny(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("API route must not perform an external DNS lookup")

    monkeypatch.setattr(socket, "getaddrinfo", deny)


@pytest.fixture
def service() -> _FakeCompanySourceService:
    with FIXTURE_PATH.open(encoding="utf-8") as fixture_file:
        fixture = json.load(fixture_file)
    return _FakeCompanySourceService(fixture=fixture, fetch_spy=_FetchSpy())


@pytest.fixture
def app(service: _FakeCompanySourceService) -> FastAPI:
    return _app(service)


def test_company_search_returns_registry_candidates_without_external_fetch(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    response = _request(
        app,
        "GET",
        "/api/v1/companies/search",
        query={"q": "Synthetic Meridian Labs"},
        headers=_owner_headers(),
    )

    assert response.status_code == 200
    assert response.payload()["candidates"] == [
        {
            "company_id": str(_COMPANY_A_ID),
            "legal_name": "Synthetic Meridian Labs Ltd.",
            "identity_evidence_refs": ["https://synthetic-meridian-a.test/legal/entity"],
        },
        {
            "company_id": str(_COMPANY_B_ID),
            "legal_name": "Synthetic Meridian Labs Ltd.",
            "identity_evidence_refs": ["https://synthetic-meridian-b.test/legal/entity"],
        },
    ]
    assert response.payload()["selection_required"] is True
    assert service.fetch_spy.calls == []
    assert service.search_limits == [20]


def test_company_search_requires_authentication_without_fetch(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    response = _request(
        app,
        "GET",
        "/api/v1/companies/search",
        query={"q": "Synthetic Meridian Labs"},
    )

    assert response.status_code == 401
    assert response.payload()["error"]["code"] == ApiErrorCode.AUTHENTICATION_REQUIRED
    assert service.fetch_spy.calls == []


def test_company_search_rejects_tampered_cursor_without_fetch(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    response = _request(
        app,
        "GET",
        "/api/v1/companies/search",
        query={"q": "Synthetic Meridian Labs", "cursor": "tampered.cursor"},
        headers=_owner_headers(),
    )

    assert response.status_code == 422
    assert response.payload()["error"]["code"] == ApiErrorCode.INVALID_CURSOR
    assert service.fetch_spy.calls == []


def test_company_search_cursor_round_trip_is_filter_bound(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    first = _request(
        app,
        "GET",
        "/api/v1/companies/search",
        query={"q": "Synthetic Meridian Labs", "limit": "1"},
        headers=_owner_headers(),
    )

    assert first.status_code == 200
    first_payload = first.payload()
    assert [candidate["company_id"] for candidate in first_payload["candidates"]] == [
        str(_COMPANY_A_ID)
    ]
    assert isinstance(first_payload["next_cursor"], str)

    second = _request(
        app,
        "GET",
        "/api/v1/companies/search",
        query={
            "q": "Synthetic Meridian Labs",
            "limit": "1",
            "cursor": first_payload["next_cursor"],
        },
        headers=_owner_headers(),
    )
    rebound = _request(
        app,
        "GET",
        "/api/v1/companies/search",
        query={
            "q": "Synthetic Meridian Research",
            "limit": "1",
            "cursor": first_payload["next_cursor"],
        },
        headers=_owner_headers(),
    )

    assert second.status_code == 200
    assert [candidate["company_id"] for candidate in second.payload()["candidates"]] == [
        str(_COMPANY_B_ID)
    ]
    assert second.payload()["next_cursor"] is None
    assert rebound.status_code == 422
    assert rebound.payload()["error"]["code"] == ApiErrorCode.INVALID_CURSOR
    assert service.fetch_spy.calls == []


@pytest.mark.parametrize("phase", ["decode", "encode"])
def test_company_search_sanitizes_cursor_codec_http_exception(
    service: _FakeCompanySourceService,
    phase: str,
) -> None:
    app = _app(
        service,
        cursor_codec=cast(CursorCodec, _RaisingCursorCodec(phase)),
    )
    query = {"q": "Synthetic Meridian Labs", "limit": "1"}
    if phase == "decode":
        query["cursor"] = "opaque-cursor"

    response = _request(
        app,
        "GET",
        "/api/v1/companies/search",
        query=query,
        headers=_owner_headers(),
    )

    assert response.status_code == 503
    assert response.payload()["error"]["code"] == ApiErrorCode.DEPENDENCY_UNAVAILABLE
    assert b"raw-cursor-codec-detail" not in response.body


def test_company_search_rejects_limit_above_configured_maximum_before_service(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    response = _request(
        app,
        "GET",
        "/api/v1/companies/search",
        query={"q": "Synthetic Meridian Labs", "limit": "101"},
        headers=_owner_headers(),
    )

    assert response.status_code == 422
    assert response.payload()["error"]["code"] == ApiErrorCode.INVALID_INPUT
    assert service.search_limits == []


def test_company_search_rejects_invalid_official_domain_before_service(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    response = _request(
        app,
        "GET",
        "/api/v1/companies/search",
        query={
            "q": "Synthetic Meridian Labs",
            "official_domain": f"{'a' * 64}.test",
        },
        headers=_owner_headers(),
    )

    assert response.status_code == 422
    assert response.payload()["error"]["code"] == ApiErrorCode.INVALID_INPUT
    assert service.search_limits == []


def test_company_resolution_rejects_malformed_evidence_url_before_service(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    response = _request(
        app,
        "POST",
        "/api/v1/companies/resolve",
        headers=_owner_headers(**{"Idempotency-Key": "invalid-evidence-url"}),
        body={"identity_evidence_refs": ["https://a..b/legal"]},
    )

    assert response.status_code == 422
    assert response.payload()["error"]["code"] == ApiErrorCode.INVALID_INPUT
    assert service.resolution_count == 0


def test_company_search_fails_closed_without_configured_page_limits(
    service: _FakeCompanySourceService,
) -> None:
    settings = ApiBoundarySettings(
        idempotency_key_max_length=256,
        cursor_max_token_length=1_024,
    )
    factory = cast(Any, api.create_company_source_api)
    app = factory(
        service=service,
        authenticator=_authenticate,
        settings=settings,
        cursor_codec=CursorCodec(
            "contract-test-cursor-secret-at-least-32-bytes",
            settings,
        ),
    )
    response = _request(
        app,
        "GET",
        "/api/v1/companies/search",
        query={"q": "Synthetic Meridian Labs"},
        headers=_owner_headers(),
    )

    assert response.status_code == 503
    assert response.payload()["error"]["code"] == ApiErrorCode.EXECUTION_POLICY_UNCONFIGURED
    assert service.search_limits == []


def test_company_resolution_requires_selection_for_same_legal_name(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    response = _request(
        app,
        "POST",
        "/api/v1/companies/resolve",
        headers=_owner_headers(**{"Idempotency-Key": "resolve-same-name"}),
        body={"name": "Synthetic Meridian Labs Ltd.", "identity_evidence_refs": []},
    )

    assert response.status_code == 200
    assert response.payload() == {
        "resolution": "selection_required",
        "company_id": None,
        "candidates": [
            {
                "company_id": str(_COMPANY_A_ID),
                "legal_name": "Synthetic Meridian Labs Ltd.",
                "identity_evidence_refs": ["https://synthetic-meridian-a.test/legal/entity"],
            },
            {
                "company_id": str(_COMPANY_B_ID),
                "legal_name": "Synthetic Meridian Labs Ltd.",
                "identity_evidence_refs": ["https://synthetic-meridian-b.test/legal/entity"],
            },
        ],
    }


def test_company_resolution_honors_explicit_legal_entity_selection(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    response = _request(
        app,
        "POST",
        "/api/v1/companies/resolve",
        headers=_owner_headers(**{"Idempotency-Key": "resolve-selected-company"}),
        body={
            "name": "Synthetic Meridian Labs Ltd.",
            "selected_company_id": str(_COMPANY_B_ID),
            "identity_evidence_refs": ["synthetic-registry:SMB-2026"],
        },
    )

    assert response.status_code == 200
    assert response.payload()["resolution"] == "resolved"
    assert response.payload()["company_id"] == str(_COMPANY_B_ID)


def test_company_resolution_does_not_transfer_shared_careers_host(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    response = _request(
        app,
        "POST",
        "/api/v1/companies/resolve",
        headers=_owner_headers(**{"Idempotency-Key": "resolve-shared-host"}),
        body={
            "identity_evidence_refs": [
                "https://synthetic-meridian-careers.test/jobs/static-posting"
            ]
        },
    )

    assert response.status_code == 200
    assert response.payload() == {
        "resolution": "selection_required",
        "company_id": None,
        "candidates": [
            {
                "company_id": str(_COMPANY_A_ID),
                "legal_name": "Synthetic Meridian Labs Ltd.",
                "identity_evidence_refs": ["https://synthetic-meridian-a.test/legal/entity"],
            },
            {
                "company_id": str(_SUBSIDIARY_ID),
                "legal_name": "Synthetic Meridian Research Ltd.",
                "identity_evidence_refs": ["https://synthetic-meridian-research.test/legal/entity"],
            },
        ],
    }


def test_company_resolution_requires_idempotency_key_before_service_call(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    response = _request(
        app,
        "POST",
        "/api/v1/companies/resolve",
        headers=_owner_headers(),
        body={"name": "Synthetic Meridian Labs Ltd.", "identity_evidence_refs": []},
    )

    assert response.status_code == 422
    assert response.payload()["error"]["code"] == ApiErrorCode.INVALID_INPUT
    assert service.resolution_count == 0


def test_company_detail_returns_registered_identity_without_external_fetch(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    response = _request(
        app,
        "GET",
        f"/api/v1/companies/{_COMPANY_A_ID}",
        headers=_owner_headers(),
    )

    assert response.status_code == 200
    assert response.payload()["company"]["company_id"] == str(_COMPANY_A_ID)
    assert response.payload()["company"]["official_domains"] == [
        "synthetic-meridian-a.test",
        "synthetic-meridian-careers.test",
    ]
    assert response.payload()["company"]["legal_identifiers"] == {
        "registry": "synthetic-registry",
        "value": "SML-A-001",
        "jurisdiction": "synthetic-jurisdiction-a",
    }
    assert service.fetch_spy.calls == []


@pytest.mark.parametrize("mismatch", ["company", "relationship"])
def test_company_detail_fails_closed_for_unrelated_service_payload(
    monkeypatch: pytest.MonkeyPatch,
    service: _FakeCompanySourceService,
    mismatch: str,
) -> None:
    method_name, payload = _company_service_method_and_response(service, "detail")
    leaked_identifier = str(_COMPANY_B_ID)
    if mismatch == "company":
        payload["company"]["company_id"] = leaked_identifier
    else:
        payload["relationships"][0]["company_id"] = leaked_identifier
        payload["relationships"][0]["related_company_id"] = str(_SUBSIDIARY_ID)

    def return_payload(**_kwargs: object) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(service, method_name, return_payload)

    response = _request_company_endpoint(_app(service), "detail")

    assert response.status_code == 503
    assert response.payload()["error"]["code"] == ApiErrorCode.DEPENDENCY_UNAVAILABLE
    assert leaked_identifier.encode("ascii") not in response.body


@pytest.mark.parametrize(
    "legal_identifiers",
    [
        {"value": "ID-1"},
        {"registration": "ATOMIC-001"},
    ],
)
def test_company_detail_round_trips_open_legal_identifier_mapping(
    monkeypatch: pytest.MonkeyPatch,
    service: _FakeCompanySourceService,
    legal_identifiers: dict[str, str],
) -> None:
    method_name, payload = _company_service_method_and_response(service, "detail")
    payload["company"]["legal_identifiers"] = legal_identifiers

    def return_payload(**_kwargs: object) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(service, method_name, return_payload)

    response = _request_company_endpoint(_app(service), "detail")

    assert response.status_code == 200
    assert response.payload()["company"]["legal_identifiers"] == legal_identifiers


@pytest.mark.parametrize(
    "legal_identifiers",
    [
        {"": "ID-1"},
        {"   ": "ID-1"},
        {"value": ""},
        {"value": 1},
        {"value": {"nested": "ID-1"}},
    ],
)
def test_company_detail_fails_closed_for_malformed_legal_identifier_mapping(
    monkeypatch: pytest.MonkeyPatch,
    service: _FakeCompanySourceService,
    legal_identifiers: dict[str, Any],
) -> None:
    method_name, payload = _company_service_method_and_response(service, "detail")
    payload["company"]["legal_identifiers"] = legal_identifiers

    def return_payload(**_kwargs: object) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(service, method_name, return_payload)

    response = _request_company_endpoint(_app(service), "detail")

    assert response.status_code == 503
    assert response.payload()["error"]["code"] == ApiErrorCode.DEPENDENCY_UNAVAILABLE


def test_company_detail_round_trips_structured_relationship_dates(
    monkeypatch: pytest.MonkeyPatch,
    service: _FakeCompanySourceService,
) -> None:
    known_date = {
        "status": "known",
        "raw_text": "2026-09-10",
        "value": "2026-09-10",
        "precision": "date",
        "timezone": "UTC",
    }
    unknown_date = {
        "status": "unknown",
        "raw_text": "date unavailable",
        "value": None,
        "precision": None,
        "timezone": None,
    }
    method_name, payload = _company_service_method_and_response(service, "detail")
    payload["relationships"][0]["valid_from"] = known_date
    payload["relationships"][0]["valid_to"] = unknown_date

    def return_payload(**_kwargs: object) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(service, method_name, return_payload)

    response = _request_company_endpoint(_app(service), "detail")

    assert response.status_code == 200
    relationship = response.payload()["relationships"][0]
    assert relationship["valid_from"] == known_date
    assert relationship["valid_to"] == unknown_date


@pytest.mark.parametrize(
    ("date_field", "date_value"),
    [
        (
            "valid_from",
            {
                "status": "known",
                "raw_text": "2026-09",
                "value": None,
                "precision": None,
                "timezone": None,
            },
        ),
        (
            "valid_to",
            {
                "status": "unknown",
                "raw_text": "date unavailable",
                "value": None,
                "precision": None,
                "timezone": None,
                "private_date_detail": "private-date-detail",
            },
        ),
    ],
)
def test_company_detail_fails_closed_for_malformed_relationship_date_value(
    monkeypatch: pytest.MonkeyPatch,
    service: _FakeCompanySourceService,
    date_field: str,
    date_value: dict[str, Any],
) -> None:
    method_name, payload = _company_service_method_and_response(service, "detail")
    payload["relationships"][0][date_field] = date_value

    def return_payload(**_kwargs: object) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(service, method_name, return_payload)

    response = _request_company_endpoint(_app(service), "detail")

    assert response.status_code == 503
    assert response.payload()["error"]["code"] == ApiErrorCode.DEPENDENCY_UNAVAILABLE
    assert b"private-date-detail" not in response.body


def test_company_service_http_exception_detail_is_sanitized(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    service.raise_raw_http_exception = True

    response = _request(
        app,
        "GET",
        f"/api/v1/companies/{_COMPANY_A_ID}",
        headers=_owner_headers(),
    )

    assert response.status_code == 503
    assert response.payload()["error"]["code"] == ApiErrorCode.DEPENDENCY_UNAVAILABLE
    assert b"raw-internal-service-detail" not in response.body


@pytest.mark.parametrize(
    ("endpoint", "extra_path"),
    [
        ("search", ("private_debug_token",)),
        ("search", ("candidates", 0, "private_candidate_debug")),
        ("resolve", ("private_debug_token",)),
        ("resolve", ("candidates", 0, "private_candidate_debug")),
        ("detail", ("private_debug_token",)),
        ("detail", ("company", "private_company_debug")),
        ("detail", ("relationships", 0, "private_relationship_debug")),
    ],
)
def test_company_routes_fail_closed_for_unexpected_service_response_fields(
    monkeypatch: pytest.MonkeyPatch,
    service: _FakeCompanySourceService,
    endpoint: str,
    extra_path: tuple[str | int, ...],
) -> None:
    method_name, payload = _company_service_method_and_response(service, endpoint)
    target: Any = payload
    for path_segment in extra_path[:-1]:
        target = target[path_segment]
    target[extra_path[-1]] = "private-response-field"

    def return_payload(**_kwargs: object) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(service, method_name, return_payload)

    response = _request_company_endpoint(_app(service), endpoint)

    assert response.status_code == 503
    assert response.payload()["error"]["code"] == ApiErrorCode.DEPENDENCY_UNAVAILABLE
    assert b"private-response-field" not in response.body


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        (
            "search",
            {
                "candidates": [],
                "selection_required": False,
                "next_cursor": "not-an-internal-offset",
            },
        ),
        (
            "resolve",
            {"resolution": "resolved", "company_id": 101, "candidates": []},
        ),
        (
            "detail",
            {
                "company": {
                    "company_id": str(_COMPANY_A_ID),
                    "legal_name": "Synthetic Meridian Labs Ltd.",
                    "aliases": [],
                    "official_domains": [],
                    "legal_identifiers": {
                        "registry": "synthetic-registry",
                        "value": "SML-A-001",
                        "jurisdiction": "synthetic-jurisdiction-a",
                    },
                    "identity_status": "verified",
                    "identity_evidence": [],
                }
            },
        ),
    ],
)
def test_company_routes_fail_closed_for_malformed_service_success_payload(
    monkeypatch: pytest.MonkeyPatch,
    service: _FakeCompanySourceService,
    endpoint: str,
    payload: dict[str, Any],
) -> None:
    method_name, _ = _company_service_method_and_response(service, endpoint)
    malformed_payload = deepcopy(payload)

    def return_payload(**_kwargs: object) -> dict[str, Any]:
        return malformed_payload

    monkeypatch.setattr(service, method_name, return_payload)

    response = _request_company_endpoint(_app(service), endpoint)

    assert response.status_code == 503
    assert response.payload()["error"]["code"] == ApiErrorCode.DEPENDENCY_UNAVAILABLE


def test_company_search_fails_closed_for_blank_candidate_evidence_reference(
    monkeypatch: pytest.MonkeyPatch,
    service: _FakeCompanySourceService,
) -> None:
    method_name, payload = _company_service_method_and_response(service, "search")
    payload["candidates"][0]["identity_evidence_refs"] = [""]

    def return_payload(**_kwargs: object) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(service, method_name, return_payload)

    response = _request_company_endpoint(_app(service), "search")

    assert response.status_code == 503
    assert response.payload()["error"]["code"] == ApiErrorCode.DEPENDENCY_UNAVAILABLE


@pytest.mark.parametrize(
    ("endpoint", "identifier_path"),
    [
        ("search", ("candidates", 0, "company_id")),
        ("resolve", ("company_id",)),
        ("detail", ("company", "company_id")),
        ("detail", ("relationships", 0, "relationship_id")),
        ("detail", ("relationships", 0, "company_id")),
        ("detail", ("relationships", 0, "related_company_id")),
    ],
)
def test_company_routes_fail_closed_for_non_uuid_service_identifiers(
    monkeypatch: pytest.MonkeyPatch,
    service: _FakeCompanySourceService,
    endpoint: str,
    identifier_path: tuple[str | int, ...],
) -> None:
    method_name, payload = _company_service_method_and_response(service, endpoint)
    if endpoint == "resolve":
        payload["resolution"] = "resolved"
        payload["candidates"] = []

    target: Any = payload
    for path_segment in identifier_path[:-1]:
        target = target[path_segment]
    target[identifier_path[-1]] = "not-a-canonical-uuid"

    def return_payload(**_kwargs: object) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(service, method_name, return_payload)

    response = _request_company_endpoint(_app(service), endpoint)

    assert response.status_code == 503
    assert response.payload()["error"]["code"] == ApiErrorCode.DEPENDENCY_UNAVAILABLE


@pytest.mark.parametrize(
    ("resolution", "has_company_id", "has_candidates"),
    [
        ("resolved", False, False),
        ("resolved", False, True),
        ("resolved", True, True),
        ("selection_required", False, False),
        ("selection_required", True, False),
        ("selection_required", True, True),
        ("unverified", False, True),
        ("unverified", True, False),
        ("unverified", True, True),
    ],
)
def test_company_resolution_fails_closed_for_inconsistent_result_state(
    monkeypatch: pytest.MonkeyPatch,
    service: _FakeCompanySourceService,
    resolution: str,
    has_company_id: bool,
    has_candidates: bool,
) -> None:
    method_name, payload = _company_service_method_and_response(service, "resolve")
    payload["resolution"] = resolution
    payload["company_id"] = str(_COMPANY_A_ID) if has_company_id else None
    payload["candidates"] = [payload["candidates"][0]] if has_candidates else []

    def return_payload(**_kwargs: object) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(service, method_name, return_payload)

    response = _request_company_endpoint(_app(service), "resolve")

    assert response.status_code == 503
    assert response.payload()["error"]["code"] == ApiErrorCode.DEPENDENCY_UNAVAILABLE


def test_source_detail_hides_non_owned_and_unknown_resources_equally(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    non_owned = _request(
        app,
        "GET",
        f"/api/v1/sources/{_OTHER_SOURCE_ID}",
        headers=_owner_headers(),
    )
    unknown = _request(
        app,
        "GET",
        "/api/v1/sources/00000000-0000-4000-8000-000000000499",
        headers=_owner_headers(),
    )

    assert non_owned.status_code == unknown.status_code == 404
    assert _error_without_correlation(non_owned) == _error_without_correlation(unknown)
    assert service.fetch_spy.calls == []


def test_source_registration_returns_202_and_replays_same_idempotency_key(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    request_headers = _owner_headers(**{"Idempotency-Key": "register-source-replay"})
    body = {
        "url": "https://synthetic-meridian-a.test/careers",
        "source_type": "job_posting",
        "analysis_request_id": "analysis-001",
    }
    first = _request(
        app,
        "POST",
        f"/api/v1/companies/{_COMPANY_A_ID}/sources",
        headers=request_headers,
        body=body,
    )
    replay = _request(
        app,
        "POST",
        f"/api/v1/companies/{_COMPANY_A_ID}/sources",
        headers=request_headers,
        body=body,
    )

    assert first.status_code == replay.status_code == 202
    assert replay.payload() == first.payload()
    assert first.payload()["status_url"] == f"/api/v1/jobs/{first.payload()['job_id']}"
    assert service.registration_count == 1


def test_source_registration_rejects_changed_payload_for_reused_idempotency_key(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    request_headers = _owner_headers(**{"Idempotency-Key": "register-source-conflict"})
    first = _request(
        app,
        "POST",
        f"/api/v1/companies/{_COMPANY_A_ID}/sources",
        headers=request_headers,
        body={
            "url": "https://synthetic-meridian-a.test/careers",
            "source_type": "job_posting",
        },
    )
    conflict = _request(
        app,
        "POST",
        f"/api/v1/companies/{_COMPANY_A_ID}/sources",
        headers=request_headers,
        body={
            "url": "https://synthetic-meridian-a.test/careers",
            "source_type": "company_website",
        },
    )

    assert first.status_code == 202
    assert conflict.status_code == 409
    assert conflict.payload()["error"]["code"] == ApiErrorCode.IDEMPOTENCY_CONFLICT
    assert service.registration_count == 1


def test_source_registration_requires_idempotency_key(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    response = _request(
        app,
        "POST",
        f"/api/v1/companies/{_COMPANY_A_ID}/sources",
        headers=_owner_headers(),
        body={
            "url": "https://synthetic-meridian-a.test/careers",
            "source_type": "job_posting",
        },
    )

    assert response.status_code == 422
    assert response.payload()["error"]["code"] == ApiErrorCode.INVALID_INPUT
    assert service.registration_count == 0


@pytest.mark.parametrize(
    "url",
    [
        "http://synthetic-meridian-a.test/careers",
        "https://user:secret@synthetic-meridian-a.test/careers",
    ],
)
def test_source_registration_rejects_invalid_url_before_service(
    app: FastAPI,
    service: _FakeCompanySourceService,
    url: str,
) -> None:
    response = _request(
        app,
        "POST",
        f"/api/v1/companies/{_COMPANY_A_ID}/sources",
        headers=_owner_headers(**{"Idempotency-Key": "invalid-source-url"}),
        body={"url": url, "source_type": "job_posting"},
    )

    assert response.status_code == 422
    assert response.payload()["error"]["code"] == ApiErrorCode.INVALID_INPUT
    assert service.registration_count == 0


def test_source_registration_rejects_unknown_source_type_before_service(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    response = _request(
        app,
        "POST",
        f"/api/v1/companies/{_COMPANY_A_ID}/sources",
        headers=_owner_headers(**{"Idempotency-Key": "invalid-source-type"}),
        body={
            "url": "https://synthetic-meridian-a.test/careers",
            "source_type": "unapproved_source_type",
        },
    )

    assert response.status_code == 422
    assert response.payload()["error"]["code"] == ApiErrorCode.INVALID_INPUT
    assert service.registration_count == 0


def test_source_list_excludes_private_w1_payload_without_external_fetch(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    response = _request(
        app,
        "GET",
        f"/api/v1/companies/{_COMPANY_A_ID}/sources",
        headers=_owner_headers(),
    )

    assert response.status_code == 200
    assert response.payload()["items"][0]["source_id"] == str(_SOURCE_ID)
    assert "private_w1_command" not in json.dumps(response.payload())
    assert "authenticated_owner_ref" not in json.dumps(response.payload())
    assert service.fetch_spy.calls == []


def test_source_detail_exposes_policy_state_but_not_private_w1_payload(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    response = _request(
        app,
        "GET",
        f"/api/v1/sources/{_SOURCE_ID}",
        headers=_owner_headers(),
    )

    assert response.status_code == 200
    source = response.payload()["source"]
    assert source["current_observation"] is None
    assert source["latest_available_version"] is None
    assert source["current_restriction"] == {"state": "not_evaluated"}
    assert source["freshness"] == {"state": "not_collected"}
    assert source["policy"] == {
        "official_status": "verified",
        "access_class": "public",
        "collection_permission": "allowed",
        "excerpt_storage_permission": "allowed",
        "body_storage_permission": "unknown",
        "redistribution_permission": "unknown",
        "revision": 1,
    }
    assert "private_w1_command" not in json.dumps(response.payload())
    assert service.fetch_spy.calls == []


def test_accepted_source_collection_can_end_in_explicit_w1_final_failure(
    app: FastAPI,
    service: _FakeCompanySourceService,
) -> None:
    acceptance = _request(
        app,
        "POST",
        f"/api/v1/companies/{_COMPANY_A_ID}/sources",
        headers=_owner_headers(**{"Idempotency-Key": "register-source-failure"}),
        body={
            "url": "https://synthetic-meridian-a.test/careers",
            "source_type": "job_posting",
        },
    )
    status_url = acceptance.payload()["status_url"]
    service.fail_accepted_job(status_url)
    terminal = service.w1_status(status_url)
    source_response = _request(
        app,
        "GET",
        f"/api/v1/sources/{acceptance.payload()['source_id']}",
        headers=_owner_headers(),
    )

    assert acceptance.status_code == 202
    assert terminal["status"] == "FAILED_FINAL"
    assert terminal["failure"] == {
        "stage": "CONTENT_EXTRACTION",
        "code": "UNSUPPORTED_FORMAT",
        "impact": "Source remains registered without a collected observation.",
        "actions": [{"action": "CHOOSE_ALTERNATIVE_OFFICIAL_SOURCE"}],
    }
    assert source_response.status_code == 200
    source = source_response.payload()["source"]
    assert source["current_observation"] == {
        "acquisition_status": "failed",
        "error_code": "UNSUPPORTED_FORMAT",
    }
    assert source["current_restriction"] == {
        "stage": "CONTENT_EXTRACTION",
        "code": "UNSUPPORTED_FORMAT",
        "impact": "Source remains registered without a collected observation.",
    }
    assert source["actions"] == [{"action": "CHOOSE_ALTERNATIVE_OFFICIAL_SOURCE"}]
