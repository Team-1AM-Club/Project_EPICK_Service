"""Shared bounded retry classification for private integration HTTP calls."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RetryDecision(str, Enum):
    SUCCESS = "success"
    RETRY = "retry"
    TERMINAL = "terminal"


def classify_http_status(status: int) -> RetryDecision:
    if 200 <= status < 300:
        return RetryDecision.SUCCESS
    if status in {408, 425, 429} or 500 <= status < 600:
        return RetryDecision.RETRY
    return RetryDecision.TERMINAL


def classify_queue_failure(code: str) -> RetryDecision:
    if code in {
        "Throttling",
        "ThrottlingException",
        "RequestThrottled",
        "RequestTimeout",
        "ServiceUnavailable",
        "InternalError",
    }:
        return RetryDecision.RETRY
    return RetryDecision.TERMINAL


@dataclass(frozen=True, slots=True)
class PrivateCallTimeouts:
    connect_seconds: float = 1.0
    read_seconds: float = 2.0
    total_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not 0 < self.connect_seconds <= self.read_seconds < self.total_seconds <= 30:
            raise ValueError("private call timeout bounds are unsafe")


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 5
    base_seconds: int = 2
    cap_seconds: int = 60

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 20:
            raise ValueError("retry attempts must be between 1 and 20")
        if self.base_seconds < 1 or self.cap_seconds < self.base_seconds:
            raise ValueError("retry backoff bounds are invalid")

    def should_retry(self, decision: RetryDecision, *, attempt: int) -> bool:
        if attempt < 1:
            raise ValueError("attempt must be positive")
        return decision is RetryDecision.RETRY and attempt < self.max_attempts

    def delay_seconds(self, attempt: int) -> int:
        if not 1 <= attempt < self.max_attempts:
            raise ValueError("delay is only defined before a remaining retry")
        return min(self.cap_seconds, self.base_seconds * 2 ** (attempt - 1))
