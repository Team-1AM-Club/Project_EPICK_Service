"""Read-only, fail-closed HTTP client for W2's authoritative Source registry."""

from __future__ import annotations

import http.client
import json
import math
import os
import ssl
import time
from urllib.parse import urlsplit
from uuid import UUID

from .authority import SourceAuthorityUnavailable

MAX_RESPONSE_BYTES = 4096


def _invalid_configuration() -> ValueError:
    return ValueError("SOURCE_AUTHORITY_CONFIGURATION_INVALID")


def _positive_seconds(value: object) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        raise _invalid_configuration() from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise _invalid_configuration()
    return seconds


class HTTPSourceAuthority:
    """Request one canonical Source ID; only W2's literal true permits use."""

    def __init__(
        self,
        endpoint: str,
        bearer_token: str,
        *,
        connect_timeout_seconds: float = 2.0,
        read_timeout_seconds: float = 2.0,
        total_timeout_seconds: float = 5.0,
        ssl_context: ssl.SSLContext | None = None,
    ):
        if not isinstance(endpoint, str) or any(
            ord(character) < 0x21 or ord(character) > 0x7E for character in endpoint
        ):
            raise _invalid_configuration()
        try:
            parsed = urlsplit(endpoint)
            host, port = parsed.hostname, parsed.port
        except (TypeError, ValueError):
            raise _invalid_configuration() from None
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or (parsed.scheme == "http" and host not in {"127.0.0.1", "::1"})
            or (port is not None and not 1 <= port <= 65535)
            or not isinstance(bearer_token, str)
            or not bearer_token
            or len(bearer_token.encode("utf-8")) > 8192
            or any(ord(character) < 0x21 or ord(character) > 0x7E for character in bearer_token)
            or (ssl_context is not None and not isinstance(ssl_context, ssl.SSLContext))
        ):
            raise _invalid_configuration()
        self._scheme = parsed.scheme
        self._host = host
        self._port = port or (443 if parsed.scheme == "https" else 80)
        self._token = bearer_token
        self._connect_timeout = _positive_seconds(connect_timeout_seconds)
        self._read_timeout = _positive_seconds(read_timeout_seconds)
        self._total_timeout = _positive_seconds(total_timeout_seconds)
        self._ssl_context = ssl_context

    def is_registered(self, source_id: UUID) -> bool:
        if not isinstance(source_id, UUID):
            raise SourceAuthorityUnavailable("SOURCE_AUTHORITY_UNAVAILABLE")
        deadline = time.monotonic() + self._total_timeout
        connection: http.client.HTTPConnection
        if self._scheme == "https":
            connection = http.client.HTTPSConnection(
                self._host,
                self._port,
                timeout=min(self._connect_timeout, self._total_timeout),
                context=self._ssl_context,
            )
        else:
            connection = http.client.HTTPConnection(
                self._host,
                self._port,
                timeout=min(self._connect_timeout, self._total_timeout),
            )
        try:
            connection.connect()
            sock = connection.sock
            self._set_timeout(sock, deadline)
            connection.request(
                "GET",
                f"/internal/v1/sources/{source_id}/authority",
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/json",
                },
            )
            self._set_timeout(sock, deadline)
            response = connection.getresponse()
            if response.status != 200 or (
                response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
                != "application/json"
            ):
                raise SourceAuthorityUnavailable("SOURCE_AUTHORITY_UNAVAILABLE")
            chunks = []
            size = 0
            while True:
                if response.fp is None or response.length == 0:
                    break
                self._set_timeout(sock, deadline)
                chunk = response.read1(min(1024, MAX_RESPONSE_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise SourceAuthorityUnavailable("SOURCE_AUTHORITY_UNAVAILABLE")
                chunks.append(chunk)
            body = b"".join(chunks)
            value = json.loads(body)
            if (
                not isinstance(value, dict)
                or set(value) != {"source_id", "registered"}
                or value["source_id"] != str(source_id)
                or type(value["registered"]) is not bool
            ):
                raise SourceAuthorityUnavailable("SOURCE_AUTHORITY_UNAVAILABLE")
            return value["registered"]
        except Exception:
            raise SourceAuthorityUnavailable("SOURCE_AUTHORITY_UNAVAILABLE") from None
        finally:
            connection.close()

    def _set_timeout(self, sock, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or sock is None:
            raise SourceAuthorityUnavailable("SOURCE_AUTHORITY_UNAVAILABLE")
        sock.settimeout(min(self._read_timeout, remaining))


def create_source_authority() -> HTTPSourceAuthority:
    """Factory for the C-01 --source-authority module:factory boundary."""

    try:
        ca_file = os.environ.get("W3_SOURCE_AUTHORITY_CA_FILE")
        context = ssl.create_default_context(cafile=ca_file) if ca_file else None
        return HTTPSourceAuthority(
            os.environ.get("W3_SOURCE_AUTHORITY_ENDPOINT", ""),
            os.environ.get("W3_SOURCE_AUTHORITY_TOKEN", ""),
            connect_timeout_seconds=os.environ.get(
                "W3_SOURCE_AUTHORITY_CONNECT_TIMEOUT_SECONDS", "2"
            ),
            read_timeout_seconds=os.environ.get("W3_SOURCE_AUTHORITY_READ_TIMEOUT_SECONDS", "2"),
            total_timeout_seconds=os.environ.get("W3_SOURCE_AUTHORITY_TOTAL_TIMEOUT_SECONDS", "5"),
            ssl_context=context,
        )
    except (OSError, ssl.SSLError, ValueError):
        raise _invalid_configuration() from None
