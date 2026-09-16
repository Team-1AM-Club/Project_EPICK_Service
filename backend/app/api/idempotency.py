from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from app.api.errors import ApiFieldError, InvalidInputError


def require_idempotency_key(value: str | None) -> str:
    if value is None or not value.strip():
        raise InvalidInputError(
            fields=(ApiFieldError(field="Idempotency-Key", reason="REQUIRED"),)
        )
    key = value.strip()
    if len(key) > 255:
        raise InvalidInputError(
            fields=(ApiFieldError(field="Idempotency-Key", reason="MAX_LENGTH_255"),)
        )
    return key


def canonical_request_hash(payload: Mapping[str, Any]) -> str:
    """Hash a request body deterministically before it is reserved in the DB ledger."""

    canonical = json.dumps(
        dict(payload), separators=(",", ":"), sort_keys=True, ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def parse_if_match(value: str | None) -> int:
    if value is None:
        raise InvalidInputError(fields=(ApiFieldError(field="If-Match", reason="REQUIRED"),))
    normalized = value.strip()
    if len(normalized) >= 3 and normalized.startswith('"') and normalized.endswith('"'):
        normalized = normalized[1:-1]
    if not normalized.isdecimal() or int(normalized) < 1:
        raise InvalidInputError(
            fields=(ApiFieldError(field="If-Match", reason="MUST_BE_A_POSITIVE_VERSION"),)
        )
    return int(normalized)
