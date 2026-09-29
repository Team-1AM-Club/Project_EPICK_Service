import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from uuid import uuid4

from epick_w4.w1_bridge import BridgeError
from epick_w4.w1_runtime import W4RecommendationWorker, configured_worker

DISPATCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["owner_user_id", "run_id"],
    "properties": {
        "owner_user_id": {"type": "string", "format": "uuid"},
        "run_id": {"type": "string", "format": "uuid"},
    },
}


class _Queue:
    def __init__(self, body):
        self.body = body
        self.deleted = []
        self.visibility = []

    def receive_message(self, **kwargs):
        del kwargs
        return {
            "Messages": [
                {
                    "Body": self.body,
                    "ReceiptHandle": "receipt-1",
                    "Attributes": {"ApproximateReceiveCount": "1"},
                }
            ]
        }

    def delete_message(self, **kwargs):
        self.deleted.append(kwargs["ReceiptHandle"])

    def change_message_visibility(self, **kwargs):
        self.visibility.append(kwargs["ReceiptHandle"])


class _Adapter:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error


class W4RecommendationWorkerTests(unittest.TestCase):
    def test_configured_entrypoint_loads_the_packaged_bootstrap_without_aws(self):
        queue = Mock()
        queue.receive_message.return_value = {"Messages": []}
        with TemporaryDirectory() as directory:
            schema = Path(directory) / "dispatch.schema.json"
            schema.write_text(json.dumps(DISPATCH_SCHEMA), encoding="utf-8")
            with patch.dict(os.environ, {
                "W4_RECOMMENDATION_BOOTSTRAP": "epick_w4.acceptance_bootstrap:build_acceptance_adapter",
                "W1_RECOMMENDATION_PRIVATE_BASE_URL": "https://w1.invalid",
                "W1_RECOMMENDATION_PRIVATE_BEARER": "SYNTHETIC-NOT-A-SECRET",
                "W1_RECOMMENDATION_PRIVATE_AUDIENCE": "epick-w4-recommendation-worker",
                "W4_RECOMMENDATION_SYNTHETIC_ACCEPTANCE": "YES",
                "W4_RECOMMENDATION_REAL_DATA_ENABLED": "false",
                "W4_RECOMMENDATION_DISPATCH_SCHEMA_PATH": str(schema),
                "AWS_DEFAULT_REGION": "ap-northeast-2",
                "W4_RECOMMENDATION_EXECUTION_QUEUE_URL": "https://sqs.invalid/synthetic",
            }, clear=True), patch("boto3.client", return_value=queue):
                worker = configured_worker()
                result = worker.drain_once(wait_seconds=0)
            self.assertEqual(result.received, 0)
            queue.delete_message.assert_not_called()

    @staticmethod
    def _body():
        return f'{{"owner_user_id":"{uuid4()}","run_id":"{uuid4()}"}}'

    def test_success_deletes_only_after_adapter_completion(self):
        queue = _Queue(self._body())
        adapter = _Adapter()
        result = W4RecommendationWorker(
            sqs=queue,
            queue_url="https://sqs.example/main",
            adapter=adapter,
            dispatch_schema=DISPATCH_SCHEMA,
            visibility_seconds=600,
        ).drain_once(wait_seconds=0)

        self.assertEqual(result.acknowledged, 1)
        self.assertEqual(queue.visibility, ["receipt-1"])
        self.assertEqual(queue.deleted, ["receipt-1"])
        self.assertEqual(len(adapter.calls), 1)

    def test_retryable_bridge_failure_leaves_message_for_redrive(self):
        queue = _Queue(self._body())
        adapter = _Adapter(BridgeError("W4_STORE_UNAVAILABLE"))
        result = W4RecommendationWorker(
            sqs=queue,
            queue_url="https://sqs.example/main",
            adapter=adapter,
            dispatch_schema=DISPATCH_SCHEMA,
            visibility_seconds=600,
        ).drain_once(wait_seconds=0)

        self.assertEqual(result.retry_scheduled, 1)
        self.assertEqual(queue.deleted, [])

    def test_invalid_or_terminal_delivery_is_acknowledged_without_retry(self):
        queue = _Queue("{not-json")
        adapter = _Adapter()
        result = W4RecommendationWorker(
            sqs=queue,
            queue_url="https://sqs.example/main",
            adapter=adapter,
            dispatch_schema=DISPATCH_SCHEMA,
            visibility_seconds=600,
        ).drain_once(wait_seconds=0)

        self.assertEqual(result.terminal_rejected, 1)
        self.assertEqual(result.retry_scheduled, 0)
        self.assertEqual(queue.deleted, ["receipt-1"])
        self.assertEqual(adapter.calls, [])


if __name__ == "__main__":
    unittest.main()
