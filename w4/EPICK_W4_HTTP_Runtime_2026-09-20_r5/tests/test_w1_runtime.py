import unittest
from uuid import uuid4

from epick_w4.w1_bridge import BridgeError
from epick_w4.w1_runtime import W4RecommendationWorker

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
