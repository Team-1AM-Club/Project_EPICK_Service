"""Contract tests for W3's read-only W2 Source authority lookup."""

from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from starlette.types import Message, Receive, Scope, Send

from epick_engine.source_collection.persistence import Source

_SOURCE_ID = UUID("8b1148ef-15a5-4f65-8f18-4a1bcad65f10")
_SERVICE_TOKEN = "w3-service-token"
_API_EVENT_LOOP: asyncio.AbstractEventLoop | None = None


@dataclass
class _FakeSession:
    registered_source_ids: set[UUID]
    failure: Exception | None = None
    get_count: int = 0
    requested_models: list[type[Source]] = field(default_factory=list)

    def __enter__(self) -> _FakeSession:
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: object | None,
    ) -> None:
        return None

    def get(self, model: type[Source], source_id: UUID) -> Source | None:
        self.get_count += 1
        self.requested_models.append(model)
        if self.failure is not None:
            raise self.failure
        if model is not Source:
            raise AssertionError("authority lookup must read the Source registry only")
        return cast(Source, object()) if source_id in self.registered_source_ids else None


@dataclass(frozen=True)
class _AsgiResponse:
    status_code: int
    body: bytes

    def json(self) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.body))


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


def _create_app(session: _FakeSession, *, token: str = _SERVICE_TOKEN) -> FastAPI:
    try:
        module = importlib.import_module("epick_engine.source_collection.source_authority_api")
    except ModuleNotFoundError as exc:
        pytest.fail(f"missing W3 source authority API module: {exc}")

    factory = getattr(module, "create_source_authority_api", None)
    assert callable(factory), (
        "create_source_authority_api must expose the internal authority boundary"
    )
    session_factory = cast(Callable[[], _FakeSession], lambda: session)
    return cast(FastAPI, factory(session_factory=session_factory, bearer_token=token))


def _request(app: FastAPI, path: str, *, authorization: str | None = None) -> _AsgiResponse:
    raw_headers = [(b"host", b"testserver")]
    if authorization is not None:
        raw_headers.append((b"authorization", authorization.encode("ascii")))
    scope = cast(
        Scope,
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": raw_headers,
            "client": ("test-client", 1),
            "server": ("testserver", 80),
        },
    )
    sent_request = False
    messages: list[Message] = []

    async def receive() -> Message:
        nonlocal sent_request
        if sent_request:
            return {"type": "http.disconnect"}
        sent_request = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    _complete(app(scope, cast(Receive, receive), cast(Send, send)))
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        cast(bytes, message.get("body", b""))
        for message in messages
        if message["type"] == "http.response.body"
    )
    return _AsgiResponse(status_code=cast(int, start["status"]), body=body)


def _authorized_get(app: FastAPI, source_id: UUID) -> _AsgiResponse:
    return _request(
        app,
        f"/internal/v1/sources/{source_id}/authority",
        authorization=f"Bearer {_SERVICE_TOKEN}",
    )


def test_returns_only_canonical_source_authority_for_registered_source() -> None:
    session = _FakeSession(registered_source_ids={_SOURCE_ID})

    response = _authorized_get(_create_app(session), _SOURCE_ID)

    assert response.status_code == 200
    assert response.json() == {"source_id": str(_SOURCE_ID), "registered": True}
    assert session.requested_models == [Source]


def test_returns_literal_false_for_unknown_source_without_source_metadata() -> None:
    session = _FakeSession(registered_source_ids=set())
    unknown_source_id = uuid4()

    response = _authorized_get(_create_app(session), unknown_source_id)

    assert response.status_code == 200
    assert response.json() == {"source_id": str(unknown_source_id), "registered": False}


@pytest.mark.parametrize("authorization", [None, "Bearer wrong-token", "Basic w3-service-token"])
def test_rejects_missing_or_invalid_service_bearer_before_registry_lookup(
    authorization: str | None,
) -> None:
    session = _FakeSession(registered_source_ids={_SOURCE_ID})

    response = _request(
        _create_app(session),
        f"/internal/v1/sources/{_SOURCE_ID}/authority",
        authorization=authorization,
    )

    assert response.status_code == 401
    assert session.get_count == 0
    assert _SERVICE_TOKEN not in response.body.decode("utf-8")


def test_rejects_noncanonical_or_invalid_source_id_before_registry_lookup() -> None:
    session = _FakeSession(registered_source_ids={_SOURCE_ID})

    response = _request(
        _create_app(session),
        f"/internal/v1/sources/{_SOURCE_ID.hex}/authority",
        authorization=f"Bearer {_SERVICE_TOKEN}",
    )

    assert response.status_code == 422
    assert session.get_count == 0
    assert _SERVICE_TOKEN not in response.body.decode("utf-8")


def test_returns_unavailable_without_database_error_or_token_leak() -> None:
    session = _FakeSession(
        registered_source_ids=set(),
        failure=RuntimeError("database password=not-for-response"),
    )

    response = _authorized_get(_create_app(session), _SOURCE_ID)

    assert response.status_code == 503
    payload = response.json()
    assert payload == {"detail": {"code": "SOURCE_AUTHORITY_UNAVAILABLE"}}
    assert "password" not in response.body.decode("utf-8")
    assert _SERVICE_TOKEN not in response.body.decode("utf-8")


def test_rejects_empty_configured_service_bearer() -> None:
    session = _FakeSession(registered_source_ids=set())

    with pytest.raises(ValueError, match="bearer_token must not be empty"):
        _create_app(session, token="")
