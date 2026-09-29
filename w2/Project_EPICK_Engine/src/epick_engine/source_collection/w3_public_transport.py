"""Versioned W2 Source-event adapter and concrete W3 C-01 HTTP publisher.

An internal SourceEvent is not a W1 transport envelope.  This module owns the
wire conversion and accepts only W3's committed transport receipt, never an
index-readiness acknowledgement.
"""

from __future__ import annotations

import http.client
import json
import math
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

from epick_engine.source_collection.contracts import SourceEvent
from epick_engine.source_collection.worker import SourceEventAcknowledgement

W3_EVENT_TARGET = "/c01/v1/events"
MAX_EVENT_BYTES = 2_000_000
MAX_RESPONSE_BYTES = 65_536
DEFAULT_TIMEOUT_SECONDS = 5.0
_MAX_BEARER_BYTES = 8 * 1024


class W3PublicTransportError(RuntimeError):
    """Sanitized wire/configuration failure without URL, payload, or credential."""

    def __init__(self, code: str) -> None:
        super().__init__(f"W3 public event transport failed: {code}")
        self.code = code


@dataclass(frozen=True, slots=True)
class W3HTTPResponse:
    status: int
    content_type: str | None
    body: bytes = field(repr=False)


class W3HTTPTransport(Protocol):
    def post(
        self,
        *,
        scheme: str,
        host: str,
        port: int,
        target: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
        ssl_context: ssl.SSLContext | None,
        max_response_bytes: int,
    ) -> W3HTTPResponse: ...


@dataclass(frozen=True, slots=True, repr=False)
class _Endpoint:
    scheme: str
    host: str
    port: int


class _StdlibW3HTTPTransport:
    """One direct request without proxy lookup, redirect following, or retries."""

    def post(
        self,
        *,
        scheme: str,
        host: str,
        port: int,
        target: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout: float,
        ssl_context: ssl.SSLContext | None,
        max_response_bytes: int,
    ) -> W3HTTPResponse:
        return self._request(
            method="POST",
            scheme=scheme,
            host=host,
            port=port,
            target=target,
            headers=headers,
            body=body,
            timeout=timeout,
            ssl_context=ssl_context,
            max_response_bytes=max_response_bytes,
        )

    def get(
        self,
        *,
        scheme: str,
        host: str,
        port: int,
        target: str,
        headers: Mapping[str, str],
        timeout: float,
        ssl_context: ssl.SSLContext | None,
        max_response_bytes: int,
    ) -> W3HTTPResponse:
        return self._request(
            method="GET",
            scheme=scheme,
            host=host,
            port=port,
            target=target,
            headers=headers,
            body=None,
            timeout=timeout,
            ssl_context=ssl_context,
            max_response_bytes=max_response_bytes,
        )

    def _request(
        self,
        *,
        method: str,
        scheme: str,
        host: str,
        port: int,
        target: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
        ssl_context: ssl.SSLContext | None,
        max_response_bytes: int,
    ) -> W3HTTPResponse:
        connection: http.client.HTTPConnection
        if scheme == "https":
            connection = http.client.HTTPSConnection(
                host, port, timeout=timeout, context=ssl_context
            )
        else:
            connection = http.client.HTTPConnection(host, port, timeout=timeout)
        try:
            connection.request(method, target, body=body, headers=dict(headers))
            response = connection.getresponse()
            return W3HTTPResponse(
                status=response.status,
                content_type=response.getheader("Content-Type"),
                body=response.read(max_response_bytes + 1),
            )
        finally:
            connection.close()


def _configuration_error() -> W3PublicTransportError:
    return W3PublicTransportError("INVALID_CONFIGURATION")


def _parse_endpoint(value: object) -> _Endpoint:
    if not isinstance(value, str) or any(
        ord(character) < 0x21 or ord(character) > 0x7E for character in value
    ):
        raise _configuration_error()
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        raise _configuration_error() from None
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != W3_EVENT_TARGET
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
        or (parsed.scheme == "http" and host not in {"127.0.0.1", "::1"})
    ):
        raise _configuration_error()
    return _Endpoint(parsed.scheme, host, port or (443 if parsed.scheme == "https" else 80))


def source_event_to_w3_wire(source_event: SourceEvent) -> dict[str, object]:
    """Map one immutable W2 event to W1 envelope 1.0 + W2 payload v1."""

    if not isinstance(source_event, SourceEvent):
        raise W3PublicTransportError("INVALID_EVENT")
    payload = source_event.payload.model_dump(mode="json")
    if payload.get("schema_version", "w2.source.v1") != "w2.source.v1":
        raise W3PublicTransportError("INVALID_EVENT")
    payload["schema_version"] = "w2.source.v1"
    return {
        "schema_version": "1.0",
        "event_id": str(source_event.event_id),
        "event_type": source_event.event_type.value,
        "producer": "w2",
        "occurred_at": source_event.occurred_at.isoformat(),
        "aggregate_type": "source",
        "aggregate_id": str(source_event.aggregate_id),
        "revision": source_event.aggregate_revision,
        "payload": payload,
    }


class W3PublicEventPublisher:
    """Publish to W3's exact event endpoint and require its exact commit receipt."""

    def __init__(
        self,
        *,
        endpoint: str,
        bearer_token: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        ssl_context: ssl.SSLContext | None = None,
        transport: W3HTTPTransport | None = None,
    ) -> None:
        parsed = _parse_endpoint(endpoint)
        if (
            not isinstance(bearer_token, str)
            or not bearer_token
            or len(bearer_token.encode("utf-8")) > _MAX_BEARER_BYTES
            or any(ord(character) < 0x21 or ord(character) > 0x7E for character in bearer_token)
            or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or (ssl_context is not None and not isinstance(ssl_context, ssl.SSLContext))
        ):
            raise _configuration_error()
        self._endpoint = parsed
        self._bearer_token = bearer_token
        self._timeout = float(timeout_seconds)
        self._ssl_context = ssl_context
        self._transport = transport or _StdlibW3HTTPTransport()

    def publish(self, source_event: SourceEvent) -> SourceEventAcknowledgement:
        wire = source_event_to_w3_wire(source_event)
        body = json.dumps(wire, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(body) > MAX_EVENT_BYTES:
            raise W3PublicTransportError("EVENT_TOO_LARGE")
        try:
            response = self._transport.post(
                scheme=self._endpoint.scheme,
                host=self._endpoint.host,
                port=self._endpoint.port,
                target=W3_EVENT_TARGET,
                headers={
                    "Authorization": f"Bearer {self._bearer_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                body=body,
                timeout=self._timeout,
                ssl_context=self._ssl_context,
                max_response_bytes=MAX_RESPONSE_BYTES,
            )
        except Exception:
            raise W3PublicTransportError("REQUEST_FAILED") from None
        if response.status != 200:
            raise W3PublicTransportError("RECEIPT_NOT_COMMITTED")
        if (
            not isinstance(response.content_type, str)
            or response.content_type.split(";", 1)[0].strip().lower() != "application/json"
            or len(response.body) > MAX_RESPONSE_BYTES
        ):
            raise W3PublicTransportError("INVALID_RESPONSE")
        try:
            value = json.loads(response.body)
            if not isinstance(value, dict):
                raise ValueError
            event_id = value.get("event_id")
            acknowledged_id = UUID(event_id) if isinstance(event_id, str) else None
        except (UnicodeError, ValueError, TypeError):
            raise W3PublicTransportError("INVALID_RESPONSE") from None
        if value.get("receipt") != "COMMITTED" or acknowledged_id != source_event.event_id:
            raise W3PublicTransportError("RECEIPT_NOT_COMMITTED")
        return SourceEventAcknowledgement(event_id=source_event.event_id)
