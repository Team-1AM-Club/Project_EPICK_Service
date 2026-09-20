"""Allowlist-only serialization for non-secret W1/W3 integration evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_FORBIDDEN_FIELDS = {
    "access_key",
    "access_key_id",
    "analysis_plan",
    "authorization",
    "bearer",
    "database_url",
    "db_url",
    "deletion_epoch",
    "owner_id",
    "password",
    "queue_url",
    "role_arn",
    "role_id",
    "secret",
    "secret_access_key",
    "sender_id",
    "session_token",
    "source_body",
    "token",
}
_SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"https://sqs\.[^\s]+", re.IGNORECASE),
    re.compile(r"(?:postgresql|postgres)(?:\+[^:]+)?://", re.IGNORECASE),
    re.compile(r"\barn:aws(?:-[a-z]+)?:", re.IGNORECASE),
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
)


class EvidenceRedactionError(ValueError):
    """Raised when evidence contains a field or value that must remain private."""


def sanitize_public_evidence(
    payload: Mapping[str, Any], *, allowed_fields: set[str]
) -> dict[str, Any]:
    if not allowed_fields:
        raise EvidenceRedactionError("an explicit evidence allowlist is required")
    return _sanitize_mapping(payload, allowed_fields=allowed_fields, path="$")


def build_count_snapshot(**counts: int) -> dict[str, int]:
    for name, count in counts.items():
        if not name or type(count) is not int or count < 0:
            raise EvidenceRedactionError("counts must be non-negative integers")
    return dict(sorted(counts.items()))


def _sanitize_mapping(
    payload: Mapping[str, Any], *, allowed_fields: set[str], path: str
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or key not in allowed_fields:
            raise EvidenceRedactionError(f"evidence field is not allowlisted at {path}")
        if key.lower() in _FORBIDDEN_FIELDS:
            raise EvidenceRedactionError(f"evidence field is forbidden at {path}.{key}")
        result[key] = _sanitize_value(value, allowed_fields=allowed_fields, path=f"{path}.{key}")
    return result


def _sanitize_value(value: Any, *, allowed_fields: set[str], path: str) -> Any:
    if isinstance(value, Mapping):
        return _sanitize_mapping(value, allowed_fields=allowed_fields, path=path)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _sanitize_value(item, allowed_fields=allowed_fields, path=f"{path}[]")
            for item in value
        ]
    if isinstance(value, str):
        if any(pattern.search(value) for pattern in _SENSITIVE_VALUE_PATTERNS):
            raise EvidenceRedactionError(f"evidence contains a sensitive value at {path}")
        return value
    if value is None or type(value) in {bool, int, float}:
        return value
    raise EvidenceRedactionError(f"unsupported evidence value at {path}")
