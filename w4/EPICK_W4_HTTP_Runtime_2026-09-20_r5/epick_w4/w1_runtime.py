"""SQS trigger for W1-owned recommendation Runs.

The queue contains references only. Context and publication always traverse the
authenticated W1 private HTTP boundary, and failures never invoke a synthetic
W1 fallback.
"""

from __future__ import annotations

import importlib
import json
import os
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker

from .w1_bridge import BridgeError, W1ExecutionAdapter
from .w1_http_run_store import W1HttpRunStore


class SqsClient(Protocol):
    def receive_message(self, **kwargs): ...
    def delete_message(self, **kwargs): ...
    def change_message_visibility(self, **kwargs): ...


@dataclass(frozen=True)
class WorkerResult:
    received: int
    acknowledged: int
    retry_scheduled: int
    terminal_rejected: int


class W4RecommendationWorker:
    def __init__(
        self,
        *,
        sqs: SqsClient,
        queue_url: str,
        adapter: W1ExecutionAdapter,
        dispatch_schema: dict,
        visibility_seconds: int,
    ) -> None:
        self.sqs = sqs
        self.queue_url = queue_url
        self.adapter = adapter
        self.validator = Draft202012Validator(
            dispatch_schema,
            format_checker=FormatChecker(),
        )
        self.visibility_seconds = visibility_seconds

    def drain_once(self, *, wait_seconds: int = 20) -> WorkerResult:
        response = self.sqs.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=wait_seconds,
            VisibilityTimeout=self.visibility_seconds,
            AttributeNames=["ApproximateReceiveCount"],
        )
        received = acknowledged = retry_scheduled = terminal_rejected = 0
        for message in response.get("Messages", []):
            received += 1
            receipt = message["ReceiptHandle"]
            try:
                body = json.loads(message["Body"])
                if list(self.validator.iter_errors(body)):
                    raise ValueError("invalid dispatch")
                self.sqs.change_message_visibility(
                    QueueUrl=self.queue_url,
                    ReceiptHandle=receipt,
                    VisibilityTimeout=self.visibility_seconds,
                )
                self.adapter.execute(
                    owner_user_id=UUID(body["owner_user_id"]),
                    run_id=UUID(body["run_id"]),
                )
            except (ValueError, KeyError, json.JSONDecodeError):
                self.sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=receipt)
                acknowledged += 1
                terminal_rejected += 1
            except BridgeError as error:
                if str(error) in {
                    "W1_PRIVATE_TRANSPORT_RETRYABLE",
                    "W4_RUN_BUSY",
                    "W4_STORE_UNAVAILABLE",
                }:
                    retry_scheduled += 1
                else:
                    self.sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=receipt)
                    acknowledged += 1
                    terminal_rejected += 1
            else:
                self.sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=receipt)
                acknowledged += 1
        return WorkerResult(received, acknowledged, retry_scheduled, terminal_rejected)


def configured_worker() -> W4RecommendationWorker:
    import boto3

    bootstrap_ref = os.environ["W4_RECOMMENDATION_BOOTSTRAP"]
    module_name, callable_name = bootstrap_ref.split(":", 1)
    bootstrap = getattr(importlib.import_module(module_name), callable_name)
    store = W1HttpRunStore(
        base_url=os.environ["W1_RECOMMENDATION_PRIVATE_BASE_URL"],
        bearer_token=os.environ["W1_RECOMMENDATION_PRIVATE_BEARER"],
        service_principal=os.environ["W1_RECOMMENDATION_PRIVATE_AUDIENCE"],
        timeout_seconds=float(os.environ.get("W1_RECOMMENDATION_HTTP_TIMEOUT_SECONDS", "30")),
    )
    adapter = bootstrap(store=store)
    if not isinstance(adapter, W1ExecutionAdapter):
        raise TypeError("W4 recommendation bootstrap must return W1ExecutionAdapter")
    schema_path = os.environ["W4_RECOMMENDATION_DISPATCH_SCHEMA_PATH"]
    with open(schema_path, encoding="utf-8") as stream:
        schema = json.load(stream)
    return W4RecommendationWorker(
        sqs=boto3.client("sqs", region_name=os.environ["AWS_DEFAULT_REGION"]),
        queue_url=os.environ["W4_RECOMMENDATION_EXECUTION_QUEUE_URL"],
        adapter=adapter,
        dispatch_schema=schema,
        visibility_seconds=int(os.environ.get("W4_RECOMMENDATION_VISIBILITY_SECONDS", "600")),
    )


def main() -> None:
    worker = configured_worker()
    while True:
        result = worker.drain_once()
        print(json.dumps(result.__dict__, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
