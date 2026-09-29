"""Pinned W1 wire adoption and unchanged transport/policy boundaries; no AWS calls."""

import hashlib
import json
import runpy
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import boto3
from botocore.config import Config
from botocore.stub import Stubber
from jsonschema import Draft202012Validator, FormatChecker

from epick_w4.question_core_contract import (
    QuestionCoreContract,
    QuestionCoreError,
    canonical_json,
    digest,
)
from epick_w4.question_core_relay import QuestionCoreRelay, SqsSendOnly, SqsSendSettings
from examples.w4_question_core_local import (
    ADOPTED_FIXTURES,
    CANDIDATE_FIXTURES,
    ROOT,
    LocalAcceptance,
    LocalClock,
    SyntheticContexts,
    SyntheticPolicies,
    adopted_contract,
    candidate_contract,
    local_producer,
)


def read(name):
    return json.loads((ADOPTED_FIXTURES / name).read_bytes())


class QuestionCoreW1AdoptedTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.clock = LocalClock()
        self.contexts, self.policies = SyntheticContexts(), SyntheticPolicies()
        self.contract = adopted_contract()
        self.producer = local_producer(
            self.path / "adopted.sqlite3",
            contexts=self.contexts,
            policies=self.policies,
            clock=self.clock,
            contract=self.contract,
        )
        self.settings = SqsSendSettings(
            "https://sqs.ap-northeast-2.amazonaws.com/000000000000/w4-main",
            "arn:aws:sqs:ap-northeast-2:000000000000:w4-main",
            "ap-northeast-2",
            True,
        )

    def prepare(self):
        return self.producer.prepare(
            submission_key="adopted-submit", context_key="synthetic-w1-context-1"
        )

    def test_upstream_bytes_and_wire_semantics_are_preserved(self):
        provenance = read("source-provenance.json")
        self.assertEqual(provenance["w1_full_sha"], self.contract.source.w1_full_sha)
        self.assertFalse(provenance["runtime_approved"])
        for name, source in provenance["files"].items():
            self.assertEqual(
                hashlib.sha256((ADOPTED_FIXTURES / name).read_bytes()).hexdigest(),
                source["sha256"],
            )
        original = read("w4-question-core-decision.event.candidate.schema.json")
        adopted = read("question-core-decision.event.schema.json")
        self.assertEqual(original.pop("title"), "W4 private W1 question Core Decision candidate")
        self.assertEqual(adopted.pop("title"), "W4 private W1 question Core Decision")
        self.assertEqual(original, adopted)
        self.assertEqual(
            (
                ADOPTED_FIXTURES / "w4-question-core-decision.event.candidate.schema.json"
            ).read_bytes(),
            (
                CANDIDATE_FIXTURES / "upstream/w4-question-core-decision.event.schema.json"
            ).read_bytes(),
        )

    def test_w1_valid_and_negative_fixtures_match_schema_and_codec(self):
        validator = Draft202012Validator(
            read("question-core-decision.event.schema.json"), format_checker=FormatChecker()
        )
        for name in ("valid-core.json", "valid-non-core.json"):
            value = read(name)
            validator.validate(value)
            self.assertEqual(self.contract.validate(value), value)
        for case in read("negative-events.json"):
            with self.subTest(case=case["case_id"]):
                self.assertFalse(validator.is_valid(case["event"]))
                with self.assertRaises(QuestionCoreError):
                    self.contract.validate(case["event"])

    def test_w1_canonical_digest_vectors_match(self):
        for vector in read("digest-vectors.json"):
            value = read(vector["fixture"]) if "fixture" in vector else vector["value"]
            self.assertEqual(digest(value), vector["digest"])

    def test_exact_pin_accepts_w1_wire_without_rewriting_version(self):
        self.contract.require_adopted()
        event = json.loads(self.prepare().body)
        self.assertEqual(event["schema_version"], "w4.private.question-core-decision/0.1-candidate")
        self.assertEqual(self.contract.validate(event), event)

    def test_unreviewed_status_or_commit_is_rejected_before_sdk(self):
        for source in (
            replace(self.contract.source, w1_full_sha=None),
            replace(self.contract.source, w1_full_sha=[]),
            replace(self.contract.source, w1_full_sha="1" * 40),
            replace(self.contract.source, status="W1_CANDIDATE"),
            replace(self.contract.source, status="TEST_ONLY"),
        ):
            with self.subTest(source=source), patch("boto3.client") as sdk:
                contract = QuestionCoreContract(
                    ADOPTED_FIXTURES / "question-core-decision.event.schema.json", source=source
                )
                with self.assertRaisesRegex(QuestionCoreError, "^CORE_TRANSPORT_NOT_ADOPTED$"):
                    SqsSendOnly(contract=contract, settings=self.settings)
                sdk.assert_not_called()

    def test_original_candidate_and_reformatted_copy_are_not_pinned_adoption(self):
        reformatted = self.path / "schema.json"
        reformatted.write_bytes(canonical_json(read("question-core-decision.event.schema.json")))
        for path in (
            ADOPTED_FIXTURES / "w4-question-core-decision.event.candidate.schema.json",
            reformatted,
        ):
            contract = QuestionCoreContract(
                path,
                source=replace(
                    self.contract.source,
                    schema_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                ),
            )
            with self.subTest(path=path.name), patch("boto3.client") as sdk:
                with self.assertRaisesRegex(QuestionCoreError, "^CORE_TRANSPORT_NOT_ADOPTED$"):
                    SqsSendOnly(contract=contract, settings=self.settings)
                sdk.assert_not_called()

    def test_wire_adoption_does_not_enable_transport_by_default(self):
        with patch("boto3.client") as sdk:
            with self.assertRaisesRegex(QuestionCoreError, "^CORE_SEND_DISABLED$"):
                SqsSendOnly(contract=self.contract, settings=replace(self.settings, enabled=False))
            sdk.assert_not_called()

    def test_stubbed_uncertain_send_retries_identical_adopted_body(self):
        prepared = self.prepare()
        client = boto3.client(
            "sqs",
            region_name=self.settings.region,
            aws_access_key_id="SYNTHETIC",
            aws_secret_access_key="SYNTHETIC",
            config=Config(retries={"total_max_attempts": 1, "mode": "standard"}),
        )
        self.addCleanup(client.close)
        params = {"QueueUrl": self.settings.main_queue_url, "MessageBody": prepared.body}
        response = {
            "MessageId": "synthetic-adopted-message",
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "MD5OfMessageBody": hashlib.md5(
                prepared.body.encode(), usedforsecurity=False
            ).hexdigest(),
        }
        with Stubber(client) as stub:
            stub.add_client_error(
                "send_message", service_error_code="RequestTimeout", expected_params=params
            )
            stub.add_response("send_message", response, params)
            sender = SqsSendOnly(contract=self.contract, settings=self.settings, client=client)
            relay = QuestionCoreRelay(producer=self.producer, sender=sender, clock=self.clock)
            self.assertEqual(relay.run_once()["status"], "RETRY")
            self.clock.advance()
            self.assertEqual(relay.run_once()["status"], "SENT")
            stub.assert_no_pending_responses()
        self.assertEqual(self.producer.store.get_submission("adopted-submit").body, prepared.body)
        self.assertEqual(self.producer.store.inspect(prepared.message_id)["attempt_count"], 2)

    def test_real_and_unapproved_policy_remain_blocked(self):
        self.contexts.value["data_kind"] = "REAL"
        with self.assertRaisesRegex(QuestionCoreError, "^CORE_REAL_DATA_NOT_ENABLED$"):
            self.prepare()
        self.contexts.value["data_kind"] = "SYNTHETIC"
        self.policies.value["approved"] = False
        with self.assertRaisesRegex(QuestionCoreError, "^CORE_POLICY_NOT_APPROVED$"):
            self.prepare()
        self.assertIsNone(self.producer.store.get_submission("adopted-submit"))

    def test_adopted_outbox_rechecks_revocation_and_input_before_send(self):
        for changes in ({"revoked": True}, {"analysis_input_version": "stale-input:v99"}):
            with self.subTest(changes=changes):
                contexts = SyntheticContexts()
                producer = local_producer(
                    self.path / (next(iter(changes)) + ".sqlite3"),
                    contexts=contexts,
                    clock=self.clock,
                    contract=self.contract,
                )
                prepared = producer.prepare(
                    submission_key="revocation", context_key="synthetic-w1-context-1"
                )
                contexts.value.update(changes)
                sender = LocalAcceptance()
                relay = QuestionCoreRelay(producer=producer, sender=sender, clock=self.clock)
                self.assertEqual(relay.run_once()["status"], "BLOCKED")
                self.assertEqual(sender.bodies, [])
                self.assertEqual(producer.store.inspect(prepared.message_id)["state"], "blocked")

    def test_candidate_outbox_is_blocked_without_silent_schema_migration(self):
        path = self.path / "candidate.sqlite3"
        old = local_producer(path, contract=candidate_contract(), clock=self.clock)
        prepared = old.prepare(submission_key="old-submit", context_key="synthetic-w1-context-1")
        new = local_producer(path, contract=self.contract, clock=self.clock)
        sender = LocalAcceptance()
        result = QuestionCoreRelay(producer=new, sender=sender, clock=self.clock).run_once()
        self.assertEqual(result, {"status": "BLOCKED", "error_code": "CORE_SCHEMA_CHANGED"})
        self.assertEqual(sender.bodies, [])
        self.assertEqual(new.store.get_submission("old-submit"), prepared)
        self.assertEqual(new.store.inspect(prepared.message_id)["state"], "blocked")

    def test_pinned_adopted_wire_survives_local_process_restart(self):
        verifier = runpy.run_path(str(ROOT / "scripts/verify-question-core-producer.py"))
        report = verifier["restart_evidence"]("w1-adopted")
        self.assertEqual(report["status"], "PASSED")
        self.assertTrue(report["identical_bodies"])
        self.assertEqual(report["actual_sqs_requests"], 0)
        self.assertEqual(report["temporary_database_teardown"], "REMOVED")


if __name__ == "__main__":
    unittest.main()
