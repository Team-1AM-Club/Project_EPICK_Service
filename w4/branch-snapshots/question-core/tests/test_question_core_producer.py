"""Real local SQLite/restart tests with explicit synthetic context and fake queue."""

import hashlib
import json
import runpy
import sqlite3
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from epick_w4.question_core_contract import (
    ContractSource,
    QuestionCoreContract,
    QuestionCoreError,
    canonical_json,
    digest,
)
from epick_w4.question_core_outbox import QuestionCoreOutbox
from epick_w4.question_core_producer import JobContext
from epick_w4.question_core_relay import QuestionCoreRelay, SqsSendOnly, SqsSendSettings
from examples.w4_question_core_local import (
    FIXTURES,
    ROOT,
    LocalAcceptance,
    LocalClock,
    SyntheticContexts,
    SyntheticPolicies,
    local_contract,
    local_producer,
    read,
)


class QuestionCoreProducerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "producer.sqlite3"
        self.clock = LocalClock()
        self.contexts, self.policies = SyntheticContexts(), SyntheticPolicies()
        self.producer = local_producer(
            self.db, contexts=self.contexts, policies=self.policies, clock=self.clock
        )
        self.store = self.producer.store

    def prepare(self, key="submit-1"):
        return self.producer.prepare(submission_key=key, context_key="synthetic-w1-context-1")

    def code(self, expected, callback):
        with self.assertRaises(QuestionCoreError) as error:
            callback()
        self.assertEqual(error.exception.code, expected)
        self.assertEqual(str(error.exception), expected)

    def relay(self, sender=None):
        return QuestionCoreRelay(
            producer=self.producer, sender=sender or LocalAcceptance(), clock=self.clock
        )

    def rows(self):
        with closing(sqlite3.connect(self.db)) as db:
            return tuple(
                db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                for table in ("w4_decisions", "w4_outbox")
            )

    def child(self, action, *args, expected=0):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "examples/w4_question_core_local.py"),
                action,
                "--db",
                str(self.db),
                *map(str, args),
            ],
            cwd=ROOT,
            capture_output=True,
            timeout=45,
            check=False,
        )
        self.assertEqual(
            result.returncode, expected, result.stderr.decode("utf-8", errors="replace")
        )
        return result

    def test_fixture_provenance_and_original_absence_are_explicit(self):
        manifest = read("manifest.json")
        self.assertEqual(manifest["status"], "TEST_ONLY_W1_ORIGINAL_MISSING")
        self.assertFalse(manifest["external_send_allowed"])
        self.assertIsNone(manifest["w1_canonical_schema"])
        for name, expected in manifest["files"].items():
            self.assertEqual(hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest(), expected)

    def test_w2_checksum_evidence_is_not_w1_adoption(self):
        source = read("source-review.json")
        self.assertEqual(source["w2_delivery_sha"], "16a7bd2653873a20a563e6d2f54c24c6dc18c373")
        self.assertGreater(source["w2_checksum_verified_count"], 0)
        self.assertFalse(source["w1_004_schema_approved"])
        self.assertTrue(
            all(
                not found
                for check in source["availability"]
                for found in check["requested_files"].values()
            )
        )
        for item in source["pinned_sources"]:
            self.assertEqual(
                hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest(), item["sha256"]
            )

    def test_fixed_digest_vectors_match_existing_w1_generic_helper(self):
        w1 = runpy.run_path(str(FIXTURES / "upstream/w1_core_decision_binding.py"))
        for vector in read("digest-vectors.json"):
            with self.subTest(case=vector["case_id"]):
                self.assertEqual(canonical_json(vector["value"]).decode(), vector["canonical_utf8"])
                self.assertEqual(digest(vector["value"]), vector["digest"])
                self.assertEqual(
                    w1["core_decision_payload_digest"](vector["value"]), vector["digest"]
                )

    def test_normal_core_and_optional_fixtures(self):
        for name in ("core-required.json", "non-core-optional.json"):
            event = read(name)
            self.assertEqual(local_contract().validate(event), event)

    def test_contract_negative_matrix(self):
        rows = [r for r in read("negative-events.json") if r["layer"] == "CONTRACT"]
        self.assertEqual(len(rows), 18)
        for row in rows:
            with self.subTest(case=row["case_id"]), self.assertRaises(QuestionCoreError):
                local_contract().validate(row["event"])

    def test_context_negative_matrix_even_with_recomputed_digest(self):
        prepared = self.prepare()
        base = read("core-required.json")
        for row in [r for r in read("negative-events.json") if r["layer"] == "CONTEXT"]:
            event = json.loads(prepared.body)
            event.update({k: v for k, v in row["event"].items() if v != base[k]})
            changed = replace(
                prepared, body=canonical_json(event).decode(), body_digest=digest(event)
            )
            with self.subTest(case=row["case_id"]):
                self.code(
                    "CORE_EVENT_CONTEXT_MISMATCH",
                    lambda changed=changed: self.producer.check_current(changed),
                )

    def test_duplicate_json_keys_and_nonfinite_values_rejected(self):
        body = canonical_json(read("core-required.json")).decode()
        self.code(
            "CORE_DUPLICATE_JSON_KEY",
            lambda: local_contract().validate(body[:-1] + ',"producer":"w4"}'),
        )
        for value in ('{"x":NaN}', b"\xff", "[1]", {"x": float("inf")}):
            with (
                self.subTest(value_type=type(value).__name__),
                self.assertRaises(QuestionCoreError),
            ):
                local_contract().validate(value)

    def test_raw_size_limit_includes_whitespace(self):
        self.code("CORE_BODY_TOO_LARGE", lambda: local_contract().validate(" " * 17000 + "{}"))

    def test_missing_or_changed_schema_fails_closed(self):
        self.code(
            "CORE_SCHEMA_UNAVAILABLE",
            lambda: QuestionCoreContract(
                Path(self.temp.name) / "missing.json", source=ContractSource("0" * 64)
            ),
        )
        self.code(
            "CORE_SCHEMA_HASH_MISMATCH",
            lambda: QuestionCoreContract(
                FIXTURES / "test-only.schema.json", source=ContractSource("0" * 64)
            ),
        )

    def test_schema_cannot_fetch_external_refs(self):
        schema = read("test-only.schema.json")
        schema["$defs"] = {"external": {"$ref": "https://invalid.example/private"}}
        path = Path(self.temp.name) / "external.json"
        path.write_bytes(canonical_json(schema))
        self.code(
            "CORE_SCHEMA_EXTERNAL_REFERENCE",
            lambda: QuestionCoreContract(
                path, source=ContractSource(hashlib.sha256(path.read_bytes()).hexdigest())
            ),
        )

    def test_test_contract_cannot_enable_sqs_even_by_status_relabel(self):
        schema = FIXTURES / "test-only.schema.json"
        contract = QuestionCoreContract(
            schema,
            source=ContractSource(
                hashlib.sha256(schema.read_bytes()).hexdigest(), "W1_ADOPTED", "1" * 40
            ),
        )
        self.code("CORE_TRANSPORT_NOT_ADOPTED", contract.require_adopted)
        settings = SqsSendSettings(
            "https://sqs.ap-northeast-2.amazonaws.com/000000000000/w4-main",
            "arn:aws:sqs:ap-northeast-2:000000000000:w4-main",
            "ap-northeast-2",
            True,
        )
        self.code(
            "CORE_TRANSPORT_NOT_ADOPTED", lambda: SqsSendOnly(contract=contract, settings=settings)
        )

    def test_host_owned_job_binding_and_separate_identifiers(self):
        prepared = self.prepare()
        event = json.loads(prepared.body)
        self.assertEqual(event["job_id"], self.contexts.value["job_id"])
        self.assertNotEqual(event["message_id"], event["decision_id"])
        self.assertIsNone(event["company_id"])
        self.assertFalse(
            {"owner_id", "project_id", "collection_company_id", "payload"} & set(event)
        )
        self.assertEqual(self.rows(), (1, 1))

    def test_client_cannot_pass_job_id_to_prepare(self):
        with self.assertRaises(TypeError):
            self.producer.prepare(
                submission_key="submit-1", context_key="synthetic-w1-context-1", job_id="forged"
            )
        self.assertEqual(self.rows(), (0, 0))

    def test_non_core_comes_from_explicit_policy(self):
        self.policies.value.update(
            decision_code="NON_CORE_OPTIONAL", reason_code="SUPPLEMENTARY_CONTEXT"
        )
        self.assertFalse(json.loads(self.prepare().body)["is_core"])

    def test_real_context_is_disabled_before_storage(self):
        self.contexts.value["data_kind"] = "REAL"
        self.code("CORE_REAL_DATA_NOT_ENABLED", self.prepare)
        self.assertEqual(self.rows(), (0, 0))

    def test_context_revocation_flags_block_prepare(self):
        for key, value in (
            ("revoked", True),
            ("processing_allowed", False),
            ("question_current", False),
            ("source_active", False),
        ):
            with self.subTest(key=key):
                self.contexts.value = {**read("context.json"), key: value}
                self.code("CORE_CONTEXT_NOT_CURRENT", self.prepare)
        self.assertEqual(self.rows(), (0, 0))

    def test_expired_context_blocks_prepare(self):
        self.contexts.value["valid_until"] = "2026-09-18T00:00:00Z"
        self.code("CORE_CONTEXT_EXPIRED", self.prepare)

    def test_unapproved_or_inconsistent_policy_blocks_prepare(self):
        self.policies.value["approved"] = False
        self.code("CORE_POLICY_NOT_APPROVED", self.prepare)
        self.policies.value.update(approved=True, reason_code="SUPPLEMENTARY_CONTEXT")
        self.code("CORE_POLICY_REASON_MISMATCH", self.prepare)
        self.assertEqual(self.rows(), (0, 0))

    def test_zero_negative_bool_and_float_policy_revisions_rejected(self):
        for version in (0, -1, True, 3.0):
            with self.subTest(version=version):
                self.policies.value["decision_version"] = version
                self.code("CORE_TRUSTED_CONTEXT_INVALID", self.prepare)

    def test_new_decision_same_or_stale_revision_rejected(self):
        for version in (1, 2):
            with self.subTest(version=version):
                self.policies.value["decision_version"] = version
                self.code("CORE_REVISION_NOT_NEW", self.prepare)

    def test_constructed_model_does_not_bypass_validation(self):
        bad = JobContext.model_construct(**{**self.contexts.value, "processing_allowed": "true"})
        self.contexts.load = lambda key: bad
        self.code("CORE_TRUSTED_CONTEXT_INVALID", self.prepare)

    def test_context_port_errors_are_sanitized(self):
        def fail(key):
            raise RuntimeError("secret-token-and-private-body")

        self.contexts.load = fail
        self.code("CORE_CURRENTNESS_UNAVAILABLE", self.prepare)

    def test_authenticated_context_rejection_does_not_prepare(self):
        def fail(key):
            raise QuestionCoreError("CORE_CONTEXT_AUTH_FAILED")

        self.contexts.load = fail
        self.code("CORE_CONTEXT_AUTH_FAILED", self.prepare)
        self.assertEqual(self.rows(), (0, 0))

    def test_context_port_cannot_smuggle_private_error_code(self):
        def fail(key):
            raise QuestionCoreError("secret-token-and-private-body")

        self.contexts.load = fail
        self.code("CORE_CURRENTNESS_UNAVAILABLE", self.prepare)

    def test_context_expiry_during_policy_lookup_blocks_prepare(self):
        original = self.policies.load

        def delayed(context):
            self.clock.advance(200_000_000)
            return original(context)

        self.policies.load = delayed
        self.code("CORE_CONTEXT_EXPIRED", self.prepare)
        self.assertEqual(self.rows(), (0, 0))

    def test_prepare_retry_preserves_id_time_body_after_cursor_advances(self):
        original = self.prepare()
        self.clock.advance()
        self.contexts.value["current_decision_version"] = 3
        self.assertEqual(self.prepare(), original)
        self.code("CORE_REVISION_NOT_NEW", lambda: self.prepare("new-submit"))

    def test_context_identity_input_and_authorization_changes_block_send(self):
        prepared = self.prepare()
        for key, value in (
            ("job_id", "10000000-0000-4000-8000-000000000009"),
            ("question_version_id", "20000000-0000-4000-8000-000000000009"),
            ("source_id", "30000000-0000-4000-8000-000000000009"),
            ("analysis_input_version", "question-input:v5"),
            ("authorization_revision", "synthetic-auth-v2"),
        ):
            with self.subTest(key=key):
                self.contexts.value = {**read("context.json"), key: value}
                self.code("CORE_CONTEXT_CHANGED", lambda: self.producer.check_current(prepared))

    def test_context_key_cannot_be_redirected(self):
        self.contexts.value["context_key"] = "some-other-key"
        self.code("CORE_CONTEXT_KEY_MISMATCH", self.prepare)

    def test_cursor_regression_and_newer_revision_block_retry(self):
        prepared = self.prepare()
        self.contexts.value["current_decision_version"] = 1
        self.code("CORE_REVISION_REGRESSED", lambda: self.producer.check_current(prepared))
        self.contexts.value["current_decision_version"] = 4
        self.code("CORE_REVISION_STALE", lambda: self.producer.check_current(prepared))

    def test_policy_revision_and_approval_rechecked(self):
        prepared = self.prepare()
        self.policies.value["policy_revision"] = "synthetic-policy-v2"
        self.code("CORE_POLICY_CHANGED", lambda: self.producer.check_current(prepared))
        self.policies.value["approved"] = False
        self.code("CORE_POLICY_NOT_APPROVED", lambda: self.producer.check_current(prepared))

    def test_sqlite_requires_disk_path(self):
        for path in (":memory:", "file:memory?mode=memory", "relative.sqlite3"):
            self.code("CORE_DURABLE_DB_PATH_REQUIRED", lambda path=path: QuestionCoreOutbox(path))

    def test_decision_and_outbox_insert_roll_back_together(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("""CREATE TRIGGER fail_outbox BEFORE INSERT ON w4_outbox
                BEGIN SELECT RAISE(ABORT, 'synthetic forced failure'); END""")
        self.code("CORE_ID_OR_REVISION_CONFLICT", self.prepare)
        self.assertEqual(self.rows(), (0, 0))

    def test_body_and_decision_are_immutable_in_database(self):
        self.prepare()
        with closing(sqlite3.connect(self.db)) as db:
            for sql in (
                "UPDATE w4_outbox SET body='changed'",
                "UPDATE w4_decisions SET decision_version=4",
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(sql)

    def test_same_message_id_different_body_is_not_duplicate(self):
        prepared = self.prepare()
        event = json.loads(prepared.body)
        event["source_id"] = "30000000-0000-4000-8000-000000000009"
        changed = replace(prepared, body=canonical_json(event).decode(), body_digest=digest(event))
        self.code("CORE_ID_OR_REVISION_CONFLICT", lambda: self.store.save(changed))

    def test_same_decision_id_in_another_message_is_rejected(self):
        prepared = self.prepare()
        event = json.loads(prepared.body)
        event["message_id"] = "40000000-0000-4000-8000-000000000009"
        changed = replace(
            prepared,
            submission_key="other-submit",
            message_id=event["message_id"],
            body=canonical_json(event).decode(),
            body_digest=digest(event),
        )
        self.code("CORE_ID_OR_REVISION_CONFLICT", lambda: self.store.save(changed))

    def test_same_revision_on_different_submission_is_rejected(self):
        self.prepare()
        self.code("CORE_ID_OR_REVISION_CONFLICT", lambda: self.prepare("submit-2"))
        self.assertEqual(self.rows(), (1, 1))

    def test_concurrent_same_submission_commits_one_message(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            messages = list(pool.map(lambda _: self.prepare(), range(8)))
        self.assertTrue(all(message == messages[0] for message in messages))
        self.assertEqual(self.rows(), (1, 1))

    def test_concurrent_relays_claim_only_one_live_lease(self):
        self.prepare()
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(
                pool.map(lambda _: self.store.claim(now=self.clock().timestamp()), range(4))
            )
        self.assertEqual(sum(c is not None for c in claims), 1)

    def test_expired_lease_owner_cannot_mark_reclaimed_message_sent(self):
        prepared = self.prepare()
        old = self.store.claim(now=self.clock().timestamp())
        self.clock.advance(61)
        current = self.store.claim(now=self.clock().timestamp())
        self.code(
            "CORE_LEASE_LOST",
            lambda: self.store.mark_sent(
                old, now=self.clock().timestamp(), broker_message_id="old-broker"
            ),
        )
        self.store.mark_sent(current, now=self.clock().timestamp(), broker_message_id="new-broker")
        self.assertEqual(self.store.inspect(prepared.message_id)["attempt_count"], 2)

    def test_ten_response_loss_retries_keep_exact_body_and_ids(self):
        prepared = self.prepare()
        sender = LocalAcceptance(lose_responses=9)
        relay = self.relay(sender)
        for attempt in range(10):
            result = relay.run_once()
            self.assertEqual(result["status"], "RETRY" if attempt < 9 else "SENT")
            self.clock.advance()
        self.assertEqual(sender.bodies, [prepared.body] * 10)
        metadata = self.store.inspect(prepared.message_id)
        self.assertEqual((metadata["state"], metadata["attempt_count"]), ("sent", 10))
        self.assertEqual(result["meaning"], "TRANSPORT_ACCEPTED_ONLY")
        self.assertEqual(self.rows(), (1, 1))
        self.assertEqual(relay.run_once(), {"status": "IDLE"})

    def test_revocation_after_prepare_blocks_without_sending(self):
        prepared = self.prepare()
        self.contexts.value["revoked"] = True
        sender = LocalAcceptance()
        self.assertEqual(self.relay(sender).run_once()["status"], "BLOCKED")
        self.assertEqual(sender.bodies, [])
        self.assertEqual(self.store.inspect(prepared.message_id)["state"], "blocked")

    def test_currentness_unavailable_retries_without_send_or_private_metadata(self):
        prepared = self.prepare()

        def fail(key):
            raise RuntimeError("secret-token-and-private-body")

        self.contexts.load = fail
        sender = LocalAcceptance()
        self.assertEqual(self.relay(sender).run_once()["status"], "RETRY")
        metadata = self.store.inspect(prepared.message_id)
        self.assertEqual(metadata["last_error"], "CORE_CURRENTNESS_UNAVAILABLE")
        self.assertNotIn("secret", json.dumps(metadata))
        self.assertEqual(sender.bodies, [])

    def test_blocked_submission_cannot_be_resurrected_by_prepare_retry(self):
        self.prepare()
        self.contexts.value["revoked"] = True
        self.assertEqual(self.relay().run_once()["status"], "BLOCKED")
        self.contexts.value["revoked"] = False
        self.code("CORE_SUBMISSION_BLOCKED", self.prepare)

    def test_lease_expiring_during_context_lookup_prevents_send(self):
        self.prepare()
        original = self.contexts.load

        def delayed(key):
            self.clock.advance(61)
            return original(key)

        self.contexts.load = delayed
        sender = LocalAcceptance()
        self.assertEqual(self.relay(sender).run_once()["status"], "LEASE_LOST")
        self.assertEqual(sender.bodies, [])

    def test_retry_observes_changed_source_before_second_send(self):
        prepared = self.prepare()
        sender = LocalAcceptance(lose_responses=1)
        relay = self.relay(sender)
        self.assertEqual(relay.run_once()["status"], "RETRY")
        self.clock.advance()
        self.contexts.value["source_id"] = "30000000-0000-4000-8000-000000000009"
        self.assertEqual(relay.run_once()["status"], "BLOCKED")
        self.assertEqual(sender.bodies, [prepared.body])

    def test_process_exit_after_commit_before_send_preserves_message(self):
        self.child("prepare-crash", expected=17)
        stored = self.store.get_submission("synthetic-submit-1")
        self.assertIsNotNone(stored)
        self.assertEqual(self.store.inspect(stored.message_id)["attempt_count"], 0)
        log = Path(self.temp.name) / "accepted.jsonl"
        self.child("relay", "--acceptance-log", log)
        self.assertEqual(log.read_text(encoding="utf-8").splitlines(), [stored.body])
        self.assertEqual(self.store.inspect(stored.message_id)["state"], "sent")

    def test_process_exit_after_fake_acceptance_retries_same_body_after_lease(self):
        self.child("prepare-crash", expected=17)
        stored = self.store.get_submission("synthetic-submit-1")
        log = Path(self.temp.name) / "accepted.jsonl"
        self.child("accept-crash", "--acceptance-log", log, expected=23)
        self.assertEqual(self.store.inspect(stored.message_id)["state"], "prepared")
        self.child("relay", "--acceptance-log", log, "--seconds", 61)
        self.assertEqual(log.read_text(encoding="utf-8").splitlines(), [stored.body, stored.body])
        self.assertEqual(self.store.inspect(stored.message_id)["attempt_count"], 2)

    def test_restart_retry_rechecks_revocation(self):
        self.child("prepare-crash", expected=17)
        stored = self.store.get_submission("synthetic-submit-1")
        log = Path(self.temp.name) / "accepted.jsonl"
        self.child("accept-crash", "--acceptance-log", log, expected=23)
        self.child("relay", "--acceptance-log", log, "--seconds", 61, "--revoke")
        self.assertEqual(log.read_text(encoding="utf-8").splitlines(), [stored.body])
        self.assertEqual(self.store.inspect(stored.message_id)["state"], "blocked")


if __name__ == "__main__":
    unittest.main()
