from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4


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
    """Narrow broker boundary shared by the relay and W1 queue workers."""

    def send_message(
        self,
        *,
        queue_url: str,
        body: str,
        message_attributes: Mapping[str, str],
    ) -> str:
        """Send one Standard SQS message and return the broker-assigned message ID."""

    def receive_messages(
        self,
        *,
        queue_url: str,
        max_messages: int,
        visibility_timeout_seconds: int,
        wait_time_seconds: int,
    ) -> list[ReceivedSqsMessage]:
        """Receive currently visible messages without acknowledging them."""

    def delete_message(self, *, queue_url: str, receipt_handle: str) -> None:
        """Acknowledge a successfully handled delivery."""

    def change_message_visibility(
        self,
        *,
        queue_url: str,
        receipt_handle: str,
        visibility_timeout_seconds: int,
    ) -> None:
        """Extend one in-flight delivery while the DB lease remains current."""

    def get_queue_attributes(
        self,
        *,
        queue_url: str,
        attribute_names: Iterable[str],
    ) -> dict[str, str]:
        """Read queue metadata for mutation-free runtime preflight checks."""


@dataclass(frozen=True)
class SentSqsMessage:
    queue_url: str
    body: str
    message_attributes: dict[str, str]
    broker_message_id: str


@dataclass(frozen=True)
class ReceivedSqsMessage:
    """The safe subset of an SQS delivery required by W1 consumers."""

    message_id: str
    receipt_handle: str
    body: str
    message_attributes: dict[str, str]
    receive_count: int
    sender_id: str | None = None


@dataclass
class _InMemoryQueuedMessage:
    queue_url: str
    message_id: str
    body: str
    message_attributes: dict[str, str]
    sender_id: str | None = None
    receipt_handle: str | None = None
    receive_count: int = 0
    visible: bool = True


class InMemorySqsPort:
    """Deterministic at-least-once SQS fake for relay and consumer integration tests."""

    def __init__(self, *, failures: Iterable[Exception] = ()) -> None:
        self._failures: deque[Exception] = deque(failures)
        self.sent_messages: list[SentSqsMessage] = []
        self._queued_messages: list[_InMemoryQueuedMessage] = []
        self.deleted_receipt_handles: list[str] = []
        self.visibility_extensions: list[tuple[str, str, int]] = []

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

    def inject_message(
        self,
        *,
        queue_url: str,
        body: str,
        message_attributes: Mapping[str, str] | None = None,
        message_id: str | None = None,
        sender_id: str | None = None,
    ) -> str:
        """Insert a test delivery.  It becomes visible on the next receive call."""

        assigned_message_id = message_id or str(uuid4())
        self._queued_messages.append(
            _InMemoryQueuedMessage(
                queue_url=queue_url,
                message_id=assigned_message_id,
                body=body,
                message_attributes=dict(message_attributes or {}),
                sender_id=sender_id,
            )
        )
        return assigned_message_id

    def receive_messages(
        self,
        *,
        queue_url: str,
        max_messages: int,
        visibility_timeout_seconds: int,
        wait_time_seconds: int,
    ) -> list[ReceivedSqsMessage]:
        del visibility_timeout_seconds, wait_time_seconds
        if max_messages <= 0:
            raise ValueError("max_messages must be positive")
        deliveries: list[ReceivedSqsMessage] = []
        for queued in self._queued_messages:
            if queued.queue_url != queue_url or not queued.visible:
                continue
            queued.receive_count += 1
            queued.receipt_handle = f"in-memory-receipt-{uuid4()}"
            queued.visible = False
            deliveries.append(
                ReceivedSqsMessage(
                    message_id=queued.message_id,
                    receipt_handle=queued.receipt_handle,
                    body=queued.body,
                    message_attributes=dict(queued.message_attributes),
                    receive_count=queued.receive_count,
                    sender_id=queued.sender_id,
                )
            )
            if len(deliveries) == max_messages:
                break
        return deliveries

    def delete_message(self, *, queue_url: str, receipt_handle: str) -> None:
        for index, queued in enumerate(self._queued_messages):
            if queued.queue_url == queue_url and queued.receipt_handle == receipt_handle:
                self.deleted_receipt_handles.append(receipt_handle)
                del self._queued_messages[index]
                return
        raise SqsFinalDeliveryError("SQS_RECEIPT_HANDLE_UNKNOWN")

    def change_message_visibility(
        self,
        *,
        queue_url: str,
        receipt_handle: str,
        visibility_timeout_seconds: int,
    ) -> None:
        if visibility_timeout_seconds <= 0:
            raise ValueError("visibility_timeout_seconds must be positive")
        for queued in self._queued_messages:
            if queued.queue_url == queue_url and queued.receipt_handle == receipt_handle:
                self.visibility_extensions.append(
                    (queue_url, receipt_handle, visibility_timeout_seconds)
                )
                return
        raise SqsFinalDeliveryError("SQS_RECEIPT_HANDLE_UNKNOWN")

    def redeliver_all(self) -> None:
        """Make unacknowledged deliveries visible again to model Standard-SQS retries."""

        for queued in self._queued_messages:
            queued.visible = True

    def get_queue_attributes(
        self,
        *,
        queue_url: str,
        attribute_names: Iterable[str],
    ) -> dict[str, str]:
        del attribute_names
        return {
            "QueueArn": f"arn:aws:sqs:ap-northeast-2:000000000000:{queue_url.rsplit('/', 1)[-1]}"
        }


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

    def receive_messages(
        self,
        *,
        queue_url: str,
        max_messages: int,
        visibility_timeout_seconds: int,
        wait_time_seconds: int,
    ) -> list[ReceivedSqsMessage]:
        try:
            response = self._client.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=max_messages,
                VisibilityTimeout=visibility_timeout_seconds,
                WaitTimeSeconds=wait_time_seconds,
                MessageSystemAttributeNames=["ApproximateReceiveCount", "SenderId"],
                MessageAttributeNames=["All"],
            )
        except Exception as error:
            raise self._classify_error(error) from error
        raw_messages = response.get("Messages", [])
        if not isinstance(raw_messages, list):
            raise SqsRetryableError("SQS_INVALID_RECEIVE_RESPONSE")
        deliveries: list[ReceivedSqsMessage] = []
        for raw in raw_messages:
            if not isinstance(raw, dict):
                raise SqsRetryableError("SQS_INVALID_RECEIVE_MESSAGE")
            message_id = raw.get("MessageId")
            receipt_handle = raw.get("ReceiptHandle")
            body = raw.get("Body")
            if not all(
                isinstance(value, str) and value
                for value in (message_id, receipt_handle, body)
            ):
                raise SqsRetryableError("SQS_INVALID_RECEIVE_MESSAGE")
            attributes = raw.get("MessageAttributes", {})
            parsed_attributes: dict[str, str] = {}
            if isinstance(attributes, dict):
                for name, value in attributes.items():
                    if not isinstance(name, str) or not isinstance(value, dict):
                        continue
                    string_value = value.get("StringValue")
                    if isinstance(string_value, str):
                        parsed_attributes[name] = string_value
            system_attributes = raw.get("Attributes", {})
            receive_count = 1
            sender_id = None
            if isinstance(system_attributes, dict):
                raw_count = system_attributes.get("ApproximateReceiveCount")
                if isinstance(raw_count, str) and raw_count.isdigit():
                    receive_count = max(1, int(raw_count))
                raw_sender_id = system_attributes.get("SenderId")
                if isinstance(raw_sender_id, str) and raw_sender_id:
                    sender_id = raw_sender_id
            deliveries.append(
                ReceivedSqsMessage(
                    message_id=message_id,
                    receipt_handle=receipt_handle,
                    body=body,
                    message_attributes=parsed_attributes,
                    receive_count=receive_count,
                    sender_id=sender_id,
                )
            )
        return deliveries

    def delete_message(self, *, queue_url: str, receipt_handle: str) -> None:
        try:
            self._client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
        except Exception as error:
            raise self._classify_error(error) from error

    def change_message_visibility(
        self,
        *,
        queue_url: str,
        receipt_handle: str,
        visibility_timeout_seconds: int,
    ) -> None:
        try:
            self._client.change_message_visibility(
                QueueUrl=queue_url,
                ReceiptHandle=receipt_handle,
                VisibilityTimeout=visibility_timeout_seconds,
            )
        except Exception as error:
            raise self._classify_error(error) from error

    def get_queue_attributes(
        self,
        *,
        queue_url: str,
        attribute_names: Iterable[str],
    ) -> dict[str, str]:
        try:
            response = self._client.get_queue_attributes(
                QueueUrl=queue_url,
                AttributeNames=list(attribute_names),
            )
        except Exception as error:
            raise self._classify_error(error) from error
        attributes = response.get("Attributes")
        if not isinstance(attributes, dict) or not all(
            isinstance(name, str) and isinstance(value, str)
            for name, value in attributes.items()
        ):
            raise SqsRetryableError("SQS_INVALID_ATTRIBUTES_RESPONSE")
        return dict(attributes)

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
