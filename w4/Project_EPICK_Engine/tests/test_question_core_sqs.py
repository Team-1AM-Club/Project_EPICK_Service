"""Real botocore request/response models, Stubber only; no AWS requests."""

import hashlib
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

import boto3
from botocore.config import Config
from botocore.stub import Stubber

from epick_w4.question_core_contract import (
    ContractSource,
    QuestionCoreContract,
    QuestionCoreError,
    canonical_json,
)
from epick_w4.question_core_relay import SqsSendOnly, SqsSendSettings
from examples.w4_question_core_local import local_contract, read


class QuestionCoreSqsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        schema = read("test-only.schema.json")
        # This temporary assumed-adoption fixture only drives a stubbed SDK client.
        # It is not written to the repository or evidence as an adopted W1 schema.
        schema["properties"]["schema_version"]["const"] = "synthetic-assumed-adoption/0"
        path = Path(self.temp.name) / "schema.json"
        path.write_bytes(canonical_json(schema))
        self.contract = QuestionCoreContract(
            path,
            source=ContractSource(
                hashlib.sha256(path.read_bytes()).hexdigest(), "W1_ADOPTED", "1" * 40
            ),
        )
        self.settings = SqsSendSettings(
            "https://sqs.ap-northeast-2.amazonaws.com/000000000000/w4-main",
            "arn:aws:sqs:ap-northeast-2:000000000000:w4-main",
            "ap-northeast-2",
            True,
        )
        self.client = boto3.client(
            "sqs",
            region_name="ap-northeast-2",
            aws_access_key_id="SYNTHETIC",
            aws_secret_access_key="SYNTHETIC",
            config=Config(retries={"total_max_attempts": 1, "mode": "standard"}),
        )
        self.addCleanup(self.client.close)
        self.stub = Stubber(self.client)
        self.stub.activate()
        self.addCleanup(self.stub.deactivate)
        self.sender = SqsSendOnly(
            contract=self.contract, settings=self.settings, client=self.client
        )
        event = read("core-required.json")
        event["schema_version"] = "synthetic-assumed-adoption/0"
        self.body = canonical_json(event).decode()
        self.params = {"QueueUrl": self.settings.main_queue_url, "MessageBody": self.body}
        self.response = {
            "MessageId": "synthetic-broker-id",
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "MD5OfMessageBody": hashlib.md5(self.body.encode(), usedforsecurity=False).hexdigest(),
        }

    def code(self, expected, callback):
        with self.assertRaises(QuestionCoreError) as error:
            callback()
        self.assertEqual(str(error.exception), expected)

    def test_only_send_message_with_exact_canonical_body(self):
        self.stub.add_response("send_message", self.response, self.params)
        self.assertEqual(self.sender.send(self.body), "synthetic-broker-id")
        self.stub.assert_no_pending_responses()
        for method in ("receive_messages", "delete_message", "purge_queue", "get_queue_attributes"):
            self.assertFalse(hasattr(self.sender, method))

    def test_sdk_retries_are_disabled(self):
        self.assertEqual(self.client.meta.config.retries["total_max_attempts"], 1)
        self.client.meta.config.retries["total_max_attempts"] = 2
        self.code(
            "CORE_SQS_CLIENT_INVALID",
            lambda: SqsSendOnly(contract=self.contract, settings=self.settings, client=self.client),
        )

    def test_live_send_is_disabled_by_default(self):
        self.code(
            "CORE_SEND_DISABLED",
            lambda: SqsSendOnly(
                contract=self.contract,
                settings=replace(self.settings, enabled=False),
                client=self.client,
            ),
        )

    def test_local_test_contract_is_rejected_before_sdk(self):
        self.code(
            "CORE_TRANSPORT_NOT_ADOPTED",
            lambda: SqsSendOnly(
                contract=local_contract(), settings=self.settings, client=self.client
            ),
        )

    def test_wrong_region_or_queue_arn_is_rejected(self):
        for change in (
            {"region": "us-east-1"},
            {"main_queue_arn": "arn:aws:sqs:ap-northeast-2:000000000000:w4-dlq"},
            {"main_queue_url": "https://untrusted.example/w4-main"},
        ):
            self.code(
                "CORE_QUEUE_CONFIG_INVALID",
                lambda change=change: SqsSendOnly(
                    contract=self.contract,
                    settings=replace(self.settings, **change),
                    client=self.client,
                ),
            )

    def test_success_without_body_checksum_is_unconfirmed(self):
        response = {k: v for k, v in self.response.items() if k != "MD5OfMessageBody"}
        self.stub.add_response("send_message", response, self.params)
        self.code("CORE_SEND_UNCONFIRMED", lambda: self.sender.send(self.body))

    def test_custom_sdk_endpoint_is_rejected(self):
        other = boto3.client(
            "sqs",
            region_name="ap-northeast-2",
            endpoint_url="https://invalid.example",
            aws_access_key_id="SYNTHETIC",
            aws_secret_access_key="SYNTHETIC",
            config=Config(retries={"total_max_attempts": 1}),
        )
        self.addCleanup(other.close)
        self.code(
            "CORE_SQS_CLIENT_INVALID",
            lambda: SqsSendOnly(contract=self.contract, settings=self.settings, client=other),
        )

    def test_wrong_body_checksum_is_unconfirmed(self):
        self.stub.add_response(
            "send_message", {**self.response, "MD5OfMessageBody": "0" * 32}, self.params
        )
        self.code("CORE_SEND_UNCONFIRMED", lambda: self.sender.send(self.body))

    def test_success_without_message_id_is_unconfirmed(self):
        response = {k: v for k, v in self.response.items() if k != "MessageId"}
        self.stub.add_response("send_message", response, self.params)
        self.code("CORE_SEND_UNCONFIRMED", lambda: self.sender.send(self.body))

    def test_non_success_status_is_unconfirmed(self):
        response = {**self.response, "ResponseMetadata": {"HTTPStatusCode": 503}}
        self.stub.add_response("send_message", response, self.params)
        self.code("CORE_SEND_UNCONFIRMED", lambda: self.sender.send(self.body))

    def test_sdk_private_error_is_not_exposed(self):
        self.stub.add_client_error(
            "send_message",
            service_error_code="AccessDenied",
            service_message="synthetic-secret-token-and-body",
            expected_params=self.params,
        )
        self.code("CORE_SEND_UNCONFIRMED", lambda: self.sender.send(self.body))


if __name__ == "__main__":
    unittest.main()
