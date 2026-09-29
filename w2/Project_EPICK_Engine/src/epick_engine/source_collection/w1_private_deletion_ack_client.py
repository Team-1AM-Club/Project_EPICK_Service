"""One-shot HTTPS client for W1's atomic private purge-and-ACK callback."""

from __future__ import annotations

import ssl
from urllib.parse import urlsplit

from epick_engine.source_collection.private_deletion_v2 import PrivateDeletionAckV2
from epick_engine.source_collection.w1_lookup_client import (
    DEFAULT_LOOKUP_TIMEOUT_SECONDS,
    LookupHTTPTransport,
    W1LookupClientError,
    _LookupEndpoint,
    _post_bounded_json,
    _require_verified_tls,
    _StdlibHTTPSLookupTransport,
    _validate_bearer,
    _validate_timeout,
)
from epick_engine.source_collection.worker import WorkerContractViolation

ACK_TARGET = "/internal/v1/w2-private/deletion/ack"
_ACKNOWLEDGED_RESPONSE_KEYS = frozenset({"status"})


def validate_private_deletion_ack_endpoint(value: object) -> _LookupEndpoint:
    """Accept only the exact protected W1 ACK HTTPS target."""

    if not isinstance(value, str) or any(
        ord(character) < 0x21 or ord(character) > 0x7E for character in value
    ):
        raise W1LookupClientError("INVALID_CONFIGURATION")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        raise W1LookupClientError("INVALID_CONFIGURATION") from None
    if (
        parsed.scheme.lower() != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != ACK_TARGET
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise W1LookupClientError("INVALID_CONFIGURATION")
    return _LookupEndpoint(host=host, port=443 if port is None else port)


class W1PrivateDeletionAckClient:
    """Send exactly one authenticated v2 ACK to W1 without redirects or retries."""

    __slots__ = ("_bearer", "_endpoint", "_ssl_context", "_timeout", "_transport")

    def __init__(
        self,
        *,
        endpoint: str,
        bearer: str,
        ssl_context: ssl.SSLContext,
        timeout_seconds: float = DEFAULT_LOOKUP_TIMEOUT_SECONDS,
        transport: LookupHTTPTransport | None = None,
    ) -> None:
        self._endpoint = validate_private_deletion_ack_endpoint(endpoint)
        self._bearer = _validate_bearer(bearer)
        self._ssl_context = _require_verified_tls(ssl_context)
        self._timeout = _validate_timeout(timeout_seconds)
        self._transport = _StdlibHTTPSLookupTransport() if transport is None else transport

    def __repr__(self) -> str:
        return "W1PrivateDeletionAckClient(endpoint=<redacted>, bearer=<redacted>)"

    def acknowledge(self, *, acknowledgement: PrivateDeletionAckV2) -> None:
        """POST one exact ACK; only W1's exact acknowledged response is success."""

        if type(acknowledgement) is not PrivateDeletionAckV2:
            raise W1LookupClientError("INVALID_ACKNOWLEDGEMENT")
        try:
            validated = PrivateDeletionAckV2.from_mapping(acknowledgement.to_mapping())
        except (TypeError, ValueError, WorkerContractViolation):
            raise W1LookupClientError("INVALID_ACKNOWLEDGEMENT") from None

        http_status, decoded = _post_bounded_json(
            endpoint=self._endpoint,
            bearer=self._bearer,
            ssl_context=self._ssl_context,
            transport=self._transport,
            target=ACK_TARGET,
            request_payload=validated.to_mapping(),
            timeout=self._timeout,
        )
        if http_status != 200:
            raise W1LookupClientError(f"HTTP_{http_status}")
        if (
            type(decoded) is not dict
            or frozenset(decoded) != _ACKNOWLEDGED_RESPONSE_KEYS
            or decoded["status"] != "ACKNOWLEDGED"
        ):
            raise W1LookupClientError("INVALID_SUCCESS_RESPONSE")
