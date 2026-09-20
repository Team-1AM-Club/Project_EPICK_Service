import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from epick_w4.w1_http_run_store import W1HttpRunStore


class _Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.value).encode()


class W1HttpRunStoreTests(unittest.TestCase):
    def setUp(self):
        self.owner = uuid4()
        self.run = uuid4()
        self.project = uuid4()
        self.question = uuid4()
        self.question_version = uuid4()
        self.snapshot = uuid4()
        self.lease = uuid4()
        self.episode_version = uuid4()
        self.store = W1HttpRunStore(
            base_url="https://w1.internal",
            bearer_token="private-test-token",
            service_principal="epick-w4-recommendation-worker",
        )

    def test_acquire_converts_strict_wire_binding_and_sends_workload_headers(self):
        payload = {
            "completed": False,
            "binding": {
                "owner_user_id": str(self.owner),
                "run_id": str(self.run),
                "project_id": str(self.project),
                "question_id": str(self.question),
                "question_version_id": str(self.question_version),
                "snapshot_id": str(self.snapshot),
                "lease_token": str(self.lease),
                "context_sha256": "a" * 64,
                "request": {"schema_version": "w4-service-input/0.1"},
                "episode_versions": [
                    {
                        "episode_id": "synthetic-episode",
                        "version": 1,
                        "episode_version_id": str(self.episode_version),
                    }
                ],
            },
        }
        with patch(
            "epick_w4.w1_http_run_store.urlopen",
            return_value=_Response(payload),
        ) as send:
            binding = self.store.acquire(owner_user_id=self.owner, run_id=self.run)

        self.assertEqual(binding.run_id, self.run)
        self.assertEqual(binding.episode_versions[0].episode_id, "synthetic-episode")
        request = send.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer private-test-token")
        self.assertEqual(
            request.get_header("X-epick-service-principal"),
            "epick-w4-recommendation-worker",
        )

    def test_completed_acquire_is_idempotent(self):
        with patch(
            "epick_w4.w1_http_run_store.urlopen",
            return_value=_Response({"completed": True, "binding": None}),
        ):
            self.assertIsNone(
                self.store.acquire(owner_user_id=self.owner, run_id=self.run)
            )


if __name__ == "__main__":
    unittest.main()
