from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any

from app.api.errors import ApiFieldError, InvalidInputError

_HMAC_SHA256_SIZE = hashlib.sha256().digest_size


class CursorCodec:
    """Signs opaque cursors so a client cannot alter a page boundary."""

    def __init__(self, signing_key: str) -> None:
        if not signing_key.strip():
            raise ValueError("cursor signing key must not be empty")
        self._signing_key = signing_key.encode("utf-8")

    def encode(self, payload: Mapping[str, Any]) -> str:
        canonical_payload = json.dumps(
            dict(payload), separators=(",", ":"), sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
        signature = hmac.new(self._signing_key, canonical_payload, hashlib.sha256).digest()
        return _urlsafe_encode(canonical_payload + b"." + signature)

    def decode(self, cursor: str) -> dict[str, Any]:
        try:
            raw = _urlsafe_decode(cursor)
            payload_and_separator = raw[:-_HMAC_SHA256_SIZE]
            signature = raw[-_HMAC_SHA256_SIZE:]
            if not payload_and_separator.endswith(b"."):
                raise ValueError
            encoded_payload = payload_and_separator[:-1]
        except (ValueError, UnicodeError):
            raise _invalid_cursor_error() from None

        expected_signature = hmac.new(
            self._signing_key, encoded_payload, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise _invalid_cursor_error()

        try:
            decoded = json.loads(encoded_payload)
        except (TypeError, ValueError, UnicodeError):
            raise _invalid_cursor_error() from None
        if not isinstance(decoded, dict):
            raise _invalid_cursor_error()
        return decoded


def validate_page_limit(limit: int) -> int:
    if not 1 <= limit <= 100:
        raise InvalidInputError(
            fields=(ApiFieldError(field="limit", reason="MUST_BE_BETWEEN_1_AND_100"),)
        )
    return limit


def _urlsafe_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _urlsafe_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _invalid_cursor_error() -> InvalidInputError:
    return InvalidInputError(
        fields=(ApiFieldError(field="cursor", reason="INVALID_CURSOR"),)
    )
