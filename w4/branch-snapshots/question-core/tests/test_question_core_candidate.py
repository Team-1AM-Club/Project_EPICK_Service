"""Received W1 candidate compatibility; synthetic host and no external transport."""

import hashlib
import json
import re
import runpy
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

from epick_w4.question_core_contract import (
    ContractSource,
    QuestionCoreContract,
    QuestionCoreError,
    canonical_json,
    digest,
)
from epick_w4.question_core_relay import QuestionCoreRelay, SqsSendOnly, SqsSendSettings
from examples.w4_question_core_local import (
    CANDIDATE_FIXTURES,
    ROOT,
    LocalAcceptance,
    LocalClock,
    SyntheticContexts,
    SyntheticPolicies,
    candidate_contract,
    local_producer,
)


def candidate_read(name):
    return json.loads((CANDIDATE_FIXTURES / name).read_bytes())


class QuestionCoreCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = LocalClock()
        self.contexts, self.policies = SyntheticContexts(), SyntheticPolicies()
        self.contract = candidate_contract()
        self.producer = local_producer(
            Path(self.temp.name) / "candidate.sqlite3",
            contexts=self.contexts,
            policies=self.policies,
            clock=self.clock,
            contract=self.contract,
        )
        self.schema = candidate_read("upstream/w4-question-core-decision.event.schema.json")
        self.validator = Draft202012Validator(self.schema, format_checker=FormatChecker())

    def prepare(self):
        return self.producer.prepare(
            submission_key="candidate-submit", context_key="synthetic-w1-context-1"
        )

    def test_embedded_originals_and_all_pinned_source_hashes_match(self):
        provenance = candidate_read("source-provenance.json")
        self.assertEqual(provenance["candidate_originals_verified"], 2)
        self.assertFalse(provenance["contract_adopted"])
        self.assertIsNone(provenance["candidate_commit_sha"])
        for name, expected in provenance["files"].items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected)
        blocks = dict(
            re.findall(
                rb"(?m)^````(markdown|json)\r?\n(.*?)^````\r?$",
                (ROOT / provenance["source_document"]).read_bytes(),
                re.S,
            )
        )
        for kind, name in (
            (b"markdown", "w4-adoption.md"),
            (b"json", "w4-question-core-decision.event.schema.json"),
        ):
            self.assertEqual(blocks[kind], (CANDIDATE_FIXTURES / "upstream" / name).read_bytes())
        for name, expected in candidate_read("manifest.json")["files"].items():
            self.assertEqual(
                hashlib.sha256((CANDIDATE_FIXTURES / name).read_bytes()).hexdigest(), expected
            )

    def test_actual_producer_uses_received_candidate_without_schema_changes(self):
        event = json.loads(self.prepare().body)
        self.validator.validate(event)
        self.assertEqual(event["schema_version"], "w4.private.question-core-decision/0.1-candidate")
        self.assertEqual(event["message_type"], "w4.private.w1.question-core-decision")
        self.assertEqual(event["job_id"], self.contexts.value["job_id"])
        self.assertIsNone(event["company_id"])
        self.assertEqual(self.schema["properties"]["company_id"], {"type": "null"})
        self.assertNotEqual(event["message_id"], event["decision_id"])
        self.assertFalse(
            {"payload", "owner_id", "project_id", "collection_company_id"} & set(event)
        )

    def test_valid_fixtures_and_negative_matrix_separate_schema_from_codec(self):
        for name in ("core-required.json", "non-core-optional.json"):
            event = candidate_read(name)
            self.validator.validate(event)
            self.assertEqual(self.contract.validate(event), event)
        rows = candidate_read("negative-events.json")
        self.assertEqual(len(rows), 25)
        for row in rows:
            with self.subTest(case=row["case_id"]):
                self.assertEqual(self.validator.is_valid(row["event"]), row["schema_valid"])
                if row["layer"] == "CODEC":
                    with self.assertRaises(QuestionCoreError):
                        self.contract.validate(row["event"])
                else:
                    self.contract.validate(row["event"])

    def test_candidate_valid_ids_still_require_exact_context_binding(self):
        prepared = self.prepare()
        base = candidate_read("core-required.json")
        for row in candidate_read("negative-events.json"):
            if row["layer"] != "CONTEXT":
                continue
            event = json.loads(prepared.body)
            event.update({k: v for k, v in row["event"].items() if v != base[k]})
            changed = replace(
                prepared, body=canonical_json(event).decode(), body_digest=digest(event)
            )
            with (
                self.subTest(case=row["case_id"]),
                self.assertRaisesRegex(QuestionCoreError, "^CORE_EVENT_CONTEXT_MISMATCH$"),
            ):
                self.producer.check_current(changed)

    def test_candidate_reason_grammar_does_not_approve_unknown_policy(self):
        boundary = candidate_read("policy-boundary.json")
        event = boundary["wire_valid_but_unapproved_reason"]
        self.validator.validate(event)
        self.contract.validate(event)
        self.policies.value["reason_code"] = event["reason_code"]
        with self.assertRaisesRegex(QuestionCoreError, "^CORE_TRUSTED_CONTEXT_INVALID$"):
            self.prepare()
        self.assertIsNone(self.producer.store.get_submission("candidate-submit"))

    def test_core_and_optional_reason_pair_remains_a_policy_check(self):
        self.policies.value["reason_code"] = "SUPPLEMENTARY_CONTEXT"
        with self.assertRaisesRegex(QuestionCoreError, "^CORE_POLICY_REASON_MISMATCH$"):
            self.prepare()
        self.policies.value["decision_code"] = "NON_CORE_OPTIONAL"
        event = json.loads(self.prepare().body)
        self.validator.validate(event)
        self.assertFalse(event["is_core"])

    def test_candidate_cannot_enable_sqs_even_when_relabelled_adopted(self):
        path = CANDIDATE_FIXTURES / "upstream/w4-question-core-decision.event.schema.json"
        settings = SqsSendSettings(
            "https://sqs.ap-northeast-2.amazonaws.com/000000000000/w4-main",
            "arn:aws:sqs:ap-northeast-2:000000000000:w4-main",
            "ap-northeast-2",
            True,
        )
        for status, pin in (
            ("W1_CANDIDATE", None),
            ("W1_ADOPTED", "bb27a692cf002100a3f773df7e434e90fe03f83f"),
        ):
            contract = QuestionCoreContract(
                path, source=ContractSource(self.contract.source.schema_sha256, status, pin)
            )
            with self.subTest(status=status), patch("boto3.client") as sdk:
                with self.assertRaisesRegex(QuestionCoreError, "^CORE_TRANSPORT_NOT_ADOPTED$"):
                    SqsSendOnly(contract=contract, settings=settings)
                sdk.assert_not_called()

    def test_supporting_type_null_does_not_allow_nullable_company_values(self):
        for prop in ({"type": ["string", "null"]}, {"type": "string"}):
            schema = {
                **self.schema,
                "properties": {**self.schema["properties"], "company_id": prop},
            }
            path = Path(self.temp.name) / "changed.schema.json"
            path.write_bytes(canonical_json(schema))
            with self.assertRaisesRegex(QuestionCoreError, "^CORE_SCHEMA_UNSUPPORTED$"):
                QuestionCoreContract(
                    path,
                    source=ContractSource(
                        hashlib.sha256(path.read_bytes()).hexdigest(), "W1_CANDIDATE"
                    ),
                )

    def test_changed_candidate_bytes_are_rejected_against_received_hash(self):
        path = Path(self.temp.name) / "changed.schema.json"
        path.write_bytes(canonical_json(self.schema))
        with self.assertRaisesRegex(QuestionCoreError, "^CORE_SCHEMA_HASH_MISMATCH$"):
            QuestionCoreContract(path, source=self.contract.source)

    def test_ten_candidate_retries_preserve_received_version_and_exact_body(self):
        prepared = self.prepare()
        sender = LocalAcceptance(lose_responses=9)
        relay = QuestionCoreRelay(producer=self.producer, sender=sender, clock=self.clock)
        for number in range(10):
            self.assertEqual(relay.run_once()["status"], "RETRY" if number < 9 else "SENT")
            self.clock.advance()
        self.assertEqual(sender.bodies, [prepared.body] * 10)
        self.assertEqual(self.producer.store.inspect(prepared.message_id)["attempt_count"], 10)

    def test_candidate_revocation_blocks_relay_before_send(self):
        prepared = self.prepare()
        self.contexts.value["revoked"] = True
        sender = LocalAcceptance()
        relay = QuestionCoreRelay(producer=self.producer, sender=sender, clock=self.clock)
        self.assertEqual(relay.run_once()["status"], "BLOCKED")
        self.assertEqual(sender.bodies, [])
        self.assertEqual(self.producer.store.inspect(prepared.message_id)["state"], "blocked")

    def test_candidate_real_or_unapproved_policy_is_not_prepared(self):
        self.contexts.value["data_kind"] = "REAL"
        with self.assertRaisesRegex(QuestionCoreError, "^CORE_REAL_DATA_NOT_ENABLED$"):
            self.prepare()
        self.contexts.value["data_kind"] = "SYNTHETIC"
        self.policies.value["approved"] = False
        with self.assertRaisesRegex(QuestionCoreError, "^CORE_POLICY_NOT_APPROVED$"):
            self.prepare()
        self.assertIsNone(self.producer.store.get_submission("candidate-submit"))

    def test_candidate_digest_vectors_match_existing_generic_w1_helper(self):
        helper = runpy.run_path(
            str(ROOT / "samples/question-core-local/upstream/w1_core_decision_binding.py")
        )
        for vector in candidate_read("digest-vectors.json"):
            self.assertEqual(digest(vector["value"]), vector["digest"])
            self.assertEqual(
                helper["core_decision_payload_digest"](vector["value"]), vector["digest"]
            )

    def test_candidate_survives_real_process_exit_and_restart(self):
        verifier = runpy.run_path(str(ROOT / "scripts/verify-question-core-producer.py"))
        report = verifier["restart_evidence"]("w1-candidate")
        self.assertEqual(report["status"], "PASSED")
        self.assertEqual(
            report["synthetic_event"]["schema_version"],
            self.schema["properties"]["schema_version"]["const"],
        )
        self.assertTrue(report["identical_bodies"])
        self.assertEqual(report["actual_sqs_requests"], 0)
        self.assertEqual(report["temporary_database_teardown"], "REMOVED")


if __name__ == "__main__":
    unittest.main()
