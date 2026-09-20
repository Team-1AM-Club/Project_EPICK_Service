"""Authenticated Standard-SQS worker for W1→W3 lifecycle commands."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .core_runtime import CoreRuntime, SqsTransport
from .lifecycle import parse_lifecycle_command


@dataclass(frozen=True, slots=True)
class LifecycleWorkerResult:
    received: int = 0
    acknowledged: int = 0
    retry_scheduled: int = 0
    applied: int = 0
    duplicate: int = 0
    stale: int = 0
    terminal_rejected: int = 0
    receipt_relayed: int = 0


class LifecycleSqsWorker:
    def __init__(
        self,
        *,
        runtime: CoreRuntime,
        client,
        command_queue_url: str,
        receipt_queue_url: str,
        expected_sender_id: str,
        visibility_timeout_seconds: int = 120,
        wait_time_seconds: int = 20,
    ):
        if not command_queue_url.startswith("https://") or command_queue_url.endswith(".fifo"):
            raise ValueError("STANDARD_COMMAND_QUEUE_REQUIRED")
        if not receipt_queue_url.startswith("https://") or receipt_queue_url.endswith(".fifo"):
            raise ValueError("STANDARD_RECEIPT_QUEUE_REQUIRED")
        if not expected_sender_id.strip() or ":" in expected_sender_id:
            raise ValueError("STABLE_W1_ROLE_ID_REQUIRED")
        if not 1 <= visibility_timeout_seconds <= 43_200:
            raise ValueError("INVALID_VISIBILITY_TIMEOUT")
        if not 0 <= wait_time_seconds <= 20:
            raise ValueError("INVALID_WAIT_TIME")
        self.runtime = runtime
        self.client = client
        self.command_queue_url = command_queue_url
        self.receipt_transport = SqsTransport(client, receipt_queue_url)
        self.expected_sender_id = expected_sender_id
        self.visibility_timeout_seconds = visibility_timeout_seconds
        self.wait_time_seconds = wait_time_seconds

    def drain_once(self, *, now: float) -> LifecycleWorkerResult:
        try:
            unfinished = self.runtime.next_unfinished_lifecycle_receipt()
            if unfinished is not None:
                handoff = self.runtime.relay_lifecycle_receipt(
                    unfinished, self.receipt_transport, now=now
                )
                if handoff in {"TRANSPORT_HANDOFF", "ALREADY_HANDOFF"}:
                    return LifecycleWorkerResult(receipt_relayed=1)
                return LifecycleWorkerResult(retry_scheduled=1)
        except (sqlite3.Error, OSError, TimeoutError):
            return LifecycleWorkerResult(retry_scheduled=1)

        try:
            response = self.client.receive_message(
                QueueUrl=self.command_queue_url,
                MaxNumberOfMessages=1,
                VisibilityTimeout=self.visibility_timeout_seconds,
                WaitTimeSeconds=self.wait_time_seconds,
                MessageSystemAttributeNames=["ApproximateReceiveCount", "SenderId"],
                MessageAttributeNames=["All"],
            )
        except Exception:
            return LifecycleWorkerResult(retry_scheduled=1)
        messages = response.get("Messages", [])
        if not isinstance(messages, list) or not messages:
            return LifecycleWorkerResult()
        message = messages[0]
        if not isinstance(message, dict):
            return LifecycleWorkerResult(received=1, retry_scheduled=1)
        body = message.get("Body")
        receipt_handle = message.get("ReceiptHandle")
        attributes = message.get("Attributes", {})
        sender_id = attributes.get("SenderId") if isinstance(attributes, dict) else None
        if not isinstance(receipt_handle, str) or not receipt_handle:
            return LifecycleWorkerResult(received=1, retry_scheduled=1)
        if (
            not isinstance(sender_id, str)
            or sender_id.split(":", maxsplit=1)[0] != self.expected_sender_id
        ):
            return self._terminal_reject(receipt_handle)
        try:
            command = parse_lifecycle_command(body)
            result = self.runtime.apply_lifecycle_command(command, now=now)
        except ValueError:
            return self._terminal_reject(receipt_handle)
        except (sqlite3.Error, OSError, TimeoutError):
            return LifecycleWorkerResult(received=1, retry_scheduled=1)

        try:
            handoff = self.runtime.relay_lifecycle_receipt(
                command.command_id, self.receipt_transport, now=now
            )
        except (sqlite3.Error, OSError, TimeoutError):
            return LifecycleWorkerResult(received=1, retry_scheduled=1)
        counts = {
            "applied": int(result.receipt.outcome == "APPLIED"),
            "duplicate": int(result.receipt.outcome == "DUPLICATE"),
            "stale": int(result.receipt.outcome == "STALE"),
        }
        if handoff not in {"TRANSPORT_HANDOFF", "ALREADY_HANDOFF"}:
            return LifecycleWorkerResult(received=1, retry_scheduled=1, **counts)
        try:
            self.client.delete_message(
                QueueUrl=self.command_queue_url, ReceiptHandle=receipt_handle
            )
        except Exception:
            return LifecycleWorkerResult(received=1, retry_scheduled=1, **counts)
        return LifecycleWorkerResult(received=1, acknowledged=1, **counts)

    def _terminal_reject(self, receipt_handle: str) -> LifecycleWorkerResult:
        try:
            self.client.delete_message(
                QueueUrl=self.command_queue_url, ReceiptHandle=receipt_handle
            )
        except Exception:
            return LifecycleWorkerResult(received=1, retry_scheduled=1, terminal_rejected=1)
        return LifecycleWorkerResult(received=1, acknowledged=1, terminal_rejected=1)
