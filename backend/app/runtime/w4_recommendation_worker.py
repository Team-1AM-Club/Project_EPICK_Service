"""Shared W1 policy for the external W4 recommendation worker boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

W4_RECOMMENDATION_MESSAGE_TYPE = "w1.private.w4.recommendation-execution.v1"

_RETRYABLE = frozenset(
    {
        "W1_PRIVATE_TRANSPORT_RETRYABLE",
        "W4_RUN_BUSY",
        "W4_STORE_UNAVAILABLE",
        "W4_TIMEOUT",
    }
)
_TERMINAL = frozenset(
    {
        "W4_RUN_NOT_FOUND",
        "W4_RUN_NOT_ACQUIRABLE",
        "W4_SCHEMA_INVALID",
        "W4_CONTEXT_NOT_CURRENT",
        "W4_POLICY_DENIED",
    }
)


@dataclass(frozen=True)
class W4FailureDisposition:
    action: Literal["RETRY", "ACK_TERMINAL"]
    safe_code: str
    synthetic_fallback_allowed: Literal[False] = False


def classify_w4_failure(code: str) -> W4FailureDisposition:
    """Bound retries without ever converting an ENGINE Run to SYNTHETIC."""

    if code in _RETRYABLE:
        return W4FailureDisposition(action="RETRY", safe_code=code)
    if code in _TERMINAL:
        return W4FailureDisposition(action="ACK_TERMINAL", safe_code=code)
    return W4FailureDisposition(action="RETRY", safe_code="W4_EXECUTION_FAILED")
