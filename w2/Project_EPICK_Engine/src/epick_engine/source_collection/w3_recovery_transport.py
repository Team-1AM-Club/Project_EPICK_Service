"""Fail-closed W2 client for W3 C-01 recovery endpoints."""

from __future__ import annotations

import json
import math
import re
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from epick_engine.source_collection.w3_public_transport import (
    _MAX_BEARER_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_EVENT_BYTES,
    MAX_RESPONSE_BYTES,
    W3HTTPResponse,
    W3PublicTransportError,
    _parse_endpoint,
    _StdlibW3HTTPTransport,
)

_SCHEMA_VERSION = "w3-c01/0.2-candidate"
_MAX_CURSOR = 2**63 - 1
_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_STATUS_KEYS = frozenset(
    {
        "schema_version",
        "source_id",
        "event_cursor",
        "required_event_cursor",
        "restriction_revision",
        "required_restriction_revision",
        "generation",
        "restriction_scope",
        "reason",
        "index_ack",
        "index_key",
        "history_complete",
    }
)
_INDEX_KEY_KEYS = frozenset(
    {
        "source_version_id",
        "extraction_revision_id",
        "representation",
        "normalization_version",
    }
)
_REASONS = frozenset(
    {
        "UNKNOWN_SOURCE",
        "CONFLICT",
        "EVENT_GAP",
        "RESTRICTION_GAP",
        "RESTRICTED",
        "OBSERVATION_BLOCKED",
        "POLICY_BLOCKED",
        "VERSION_UNAVAILABLE",
        "INDEX_PENDING",
        "INDEX_MISMATCH",
        "INDEX_FAILED",
        "BODY_EXPIRED",
        "READY",
    }
)
_REPLAY_OUTCOMES = frozenset({"INCOMPLETE", "REPLAYED"})
_SNAPSHOT_OUTCOMES = frozenset({"SNAPSHOT_APPLIED"})


class W3RecoveryTransportError(RuntimeError):
    """Sanitized recovery transport failure without endpoint or body details."""

    def __init__(self, code: str) -> None:
        super().__init__(f"W3 recovery transport failed: {code}")
        self.code = code


@dataclass(frozen=True, slots=True)
class W3RecoveryStatus:
    """Validated W3 durable state; index acknowledgement remains metadata only."""

    source_id: UUID
    event_cursor: int
    required_event_cursor: int
    restriction_revision: int
    required_restriction_revision: int
    outcome: str | None
    reason: str
    history_complete: bool
    index_ack: bool


class W3RecoveryHTTPTransport(Protocol):
    """Only the GET/POST surface recovery needs; publisher fakes stay POST-only."""

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
    ) -> W3HTTPResponse: ...

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


def _configuration_error() -> W3RecoveryTransportError:
    return W3RecoveryTransportError("INVALID_CONFIGURATION")


def _invalid_response() -> W3RecoveryTransportError:
    return W3RecoveryTransportError("INVALID_RESPONSE")


def _strict_cursor(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_CURSOR:
        raise ValueError
    return value


def _strict_uuid(value: object) -> UUID:
    if not isinstance(value, str) or _UUID_PATTERN.fullmatch(value) is None:
        raise ValueError
    return UUID(value)


def _validate_index_key(value: object) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != _INDEX_KEY_KEYS:
        raise ValueError
    _strict_uuid(value["source_version_id"])
    _strict_uuid(value["extraction_revision_id"])
    if any(
        not isinstance(value[key], str) or not value[key]
        for key in ("representation", "normalization_version")
    ):
        raise ValueError


class W3RecoveryClient:
    """Send only fixed C-01 recovery routes and accept only exact W3 receipts."""

    def __init__(
        self,
        *,
        endpoint: str,
        bearer_token: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        ssl_context: ssl.SSLContext | None = None,
        transport: W3RecoveryHTTPTransport | None = None,
    ) -> None:
        try:
            parsed = _parse_endpoint(endpoint)
        except W3PublicTransportError:
            raise _configuration_error() from None
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

    def status(self, *, source_id: UUID) -> W3RecoveryStatus:
        source_id = self._validate_source_id(source_id)
        response = self._get(target=f"/c01/v1/status/{source_id}")
        return self._parse_status(response, source_id=source_id, outcomes=None)

    def replay(self, *, source_id: UUID, batch: dict[str, object]) -> W3RecoveryStatus:
        return self._post_recovery(
            source_id=source_id,
            payload=batch,
            target="/c01/v1/replay",
            outcomes=_REPLAY_OUTCOMES,
        )

    def snapshot(self, *, source_id: UUID, payload: dict[str, object]) -> W3RecoveryStatus:
        return self._post_recovery(
            source_id=source_id,
            payload=payload,
            target="/c01/v1/snapshot",
            outcomes=_SNAPSHOT_OUTCOMES,
        )

    @staticmethod
    def _validate_source_id(source_id: UUID) -> UUID:
        if not isinstance(source_id, UUID):
            raise W3RecoveryTransportError("INVALID_SOURCE")
        return source_id

    def _get(self, *, target: str) -> W3HTTPResponse:
        try:
            return self._transport.get(
                scheme=self._endpoint.scheme,
                host=self._endpoint.host,
                port=self._endpoint.port,
                target=target,
                headers={
                    "Authorization": f"Bearer {self._bearer_token}",
                    "Accept": "application/json",
                },
                timeout=self._timeout,
                ssl_context=self._ssl_context,
                max_response_bytes=MAX_RESPONSE_BYTES,
            )
        except Exception:
            raise W3RecoveryTransportError("REQUEST_FAILED") from None

    def _post_recovery(
        self,
        *,
        source_id: UUID,
        payload: dict[str, object],
        target: str,
        outcomes: frozenset[str],
    ) -> W3RecoveryStatus:
        source_id = self._validate_source_id(source_id)
        body = self._serialize_payload(payload)
        self._validate_payload_source_id(payload, source_id=source_id)
        try:
            response = self._transport.post(
                scheme=self._endpoint.scheme,
                host=self._endpoint.host,
                port=self._endpoint.port,
                target=target,
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
            raise W3RecoveryTransportError("REQUEST_FAILED") from None
        return self._parse_status(response, source_id=source_id, outcomes=outcomes)

    @staticmethod
    def _serialize_payload(payload: dict[str, object]) -> bytes:
        if not isinstance(payload, dict):
            raise W3RecoveryTransportError("INVALID_PAYLOAD")
        try:
            body = json.dumps(
                payload,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, UnicodeError, ValueError):
            raise W3RecoveryTransportError("INVALID_PAYLOAD") from None
        if len(body) > MAX_EVENT_BYTES:
            raise W3RecoveryTransportError("REQUEST_TOO_LARGE")
        return body

    @staticmethod
    def _validate_payload_source_id(payload: dict[str, object], *, source_id: UUID) -> None:
        try:
            payload_source_id = _strict_uuid(payload["source_id"])
        except (KeyError, TypeError, ValueError):
            raise W3RecoveryTransportError("INVALID_PAYLOAD") from None
        if payload_source_id != source_id:
            raise W3RecoveryTransportError("SOURCE_MISMATCH")

    @staticmethod
    def _json_object(response: object) -> dict[str, object]:
        if not isinstance(response, W3HTTPResponse) or type(response.status) is not int:
            raise _invalid_response()
        if response.status != 200:
            raise W3RecoveryTransportError("REMOTE_REJECTED")
        if (
            not isinstance(response.content_type, str)
            or response.content_type.split(";", 1)[0].strip().lower() != "application/json"
            or not isinstance(response.body, bytes)
            or len(response.body) > MAX_RESPONSE_BYTES
        ):
            raise _invalid_response()
        try:
            value = json.loads(response.body)
        except (UnicodeError, ValueError, TypeError):
            raise _invalid_response() from None
        if not isinstance(value, dict):
            raise _invalid_response()
        return value

    def _parse_status(
        self,
        response: object,
        *,
        source_id: UUID,
        outcomes: frozenset[str] | None,
    ) -> W3RecoveryStatus:
        value = self._json_object(response)
        expected_keys = _STATUS_KEYS if outcomes is None else _STATUS_KEYS | {"outcome"}
        if set(value) != expected_keys:
            raise _invalid_response()
        try:
            response_source_id = _strict_uuid(value["source_id"])
            event_cursor = _strict_cursor(value["event_cursor"])
            required_event_cursor = _strict_cursor(value["required_event_cursor"])
            restriction_revision = _strict_cursor(value["restriction_revision"])
            required_restriction_revision = _strict_cursor(value["required_restriction_revision"])
            _strict_cursor(value["generation"])
            _validate_index_key(value["index_key"])
        except (KeyError, TypeError, ValueError):
            raise _invalid_response() from None
        if (
            value["schema_version"] != _SCHEMA_VERSION
            or response_source_id != source_id
            or value["restriction_scope"] != "version"
            or not isinstance(value["reason"], str)
            or value["reason"] not in _REASONS
            or type(value["index_ack"]) is not bool
            or type(value["history_complete"]) is not bool
            or value["index_ack"] != (value["reason"] == "READY")
            or (
                value["index_ack"]
                and (
                    value["index_key"] is None
                    or event_cursor != required_event_cursor
                    or restriction_revision != required_restriction_revision
                )
            )
        ):
            raise _invalid_response()
        outcome: str | None = None
        if outcomes is not None:
            candidate = value["outcome"]
            if not isinstance(candidate, str) or candidate not in outcomes:
                raise _invalid_response()
            outcome = candidate
        return W3RecoveryStatus(
            source_id=response_source_id,
            event_cursor=event_cursor,
            required_event_cursor=required_event_cursor,
            restriction_revision=restriction_revision,
            required_restriction_revision=required_restriction_revision,
            outcome=outcome,
            reason=value["reason"],
            history_complete=value["history_complete"],
            index_ack=value["index_ack"],
        )
