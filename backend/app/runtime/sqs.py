from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class SqsDeliveryError(RuntimeError):
    """A classified SQS delivery outcome safe to store as an outbox error code."""

    def __init__(self, code: str) -> None:
        if not code or len(code) > 64:
            raise ValueError("SQS delivery error codes must be between 1 and 64 characters")
        super().__init__(code)
        self.code = code


class SqsRetryableError(SqsDeliveryError):
    """The relay cannot know whether SQS accepted the message, so it must retry."""


class SqsFinalDeliveryError(SqsDeliveryError):
    """The relay has a durable configuration or route error that cannot self-heal."""


class SqsPort(Protocol):
    """Narrow broker boundary used by the outbox relay and its isolated tests."""

    def send_message(
        self,
        *,
        queue_url: str,
        body: str,
        message_attributes: Mapping[str, str],
    ) -> str:
        """Send one Standard SQS message and return the broker-assigned message ID."""


@dataclass(frozen=True)
class SentSqsMessage:
    queue_url: str
    body: str
    message_attributes: dict[str, str]
    broker_message_id: str


class InMemorySqsPort:
    """Deterministic SQS fake.  It never implements queue-worker semantics."""

    def __init__(self, *, failures: Iterable[Exception] = ()) -> None:
        self._failures: deque[Exception] = deque(failures)
        self.sent_messages: list[SentSqsMessage] = []

    def send_message(
        self,
        *,
        queue_url: str,
        body: str,
        message_attributes: Mapping[str, str],
    ) -> str:
        if self._failures:
            raise self._failures.popleft()
        broker_message_id = f"in-memory-{len(self.sent_messages) + 1}"
        self.sent_messages.append(
            SentSqsMessage(
                queue_url=queue_url,
                body=body,
                message_attributes=dict(message_attributes),
                broker_message_id=broker_message_id,
            )
        )
        return broker_message_id


class Boto3SqsPort:
    """Production SQS adapter.  boto3 is imported only when the relay process starts."""

    _RETRYABLE_CODES = frozenset(
        {
            "InternalError",
            "RequestThrottled",
            "RequestTimeout",
            "ServiceUnavailable",
            "Throttling",
            "ThrottlingException",
        }
    )

    def __init__(self, *, client: Any | None = None, region_name: str | None = None) -> None:
        if client is None:
            try:
                import boto3
            except ImportError as error:  # pragma: no cover
                # boto3 is a required dependency in production runtime images.
                raise RuntimeError(
                    "boto3 must be installed to use the production SQS adapter"
                ) from error
            client = boto3.client("sqs", region_name=region_name)
        self._client = client

    def send_message(
        self,
        *,
        queue_url: str,
        body: str,
        message_attributes: Mapping[str, str],
    ) -> str:
        try:
            response = self._client.send_message(
                QueueUrl=queue_url,
                MessageBody=body,
                MessageAttributes={
                    name: {"DataType": "String", "StringValue": value}
                    for name, value in message_attributes.items()
                },
            )
        except Exception as error:
            # The adapter converts AWS SDK details into a stable domain code.
            raise self._classify_error(error) from error
        message_id = response.get("MessageId")
        if not isinstance(message_id, str) or not message_id:
            raise SqsRetryableError("SQS_MISSING_MESSAGE_ID")
        return message_id

    @classmethod
    def _classify_error(cls, error: Exception) -> SqsDeliveryError:
        response = getattr(error, "response", None)
        error_info = response.get("Error", {}) if isinstance(response, dict) else {}
        error_code = error_info.get("Code") if isinstance(error_info, dict) else None
        if isinstance(error_code, str) and error_code in cls._RETRYABLE_CODES:
            return SqsRetryableError(f"SQS_{error_code.upper()}")
        if isinstance(error_code, str) and error_code:
            return SqsFinalDeliveryError(f"SQS_{error_code.upper()}")
        return SqsRetryableError("SQS_TRANSPORT_FAILURE")
