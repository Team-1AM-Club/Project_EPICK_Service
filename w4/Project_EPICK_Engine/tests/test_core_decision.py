"""Boundary regressions for the synthetic W4 Core Decision proposal."""
from copy import deepcopy
import unittest

from epick_w4.core_decision import (
    CoreDecisionError, check_current, prepare_decision, receipt_outcome, validate_message,
)
from epick_w4.synthetic_policy import content_hash
from examples.w4_core_decision_demo import (
    company_binding_probes, prepared_fixture, read, run_demo, verify_upstream_bytes,
)


class CoreDecisionTests(unittest.TestCase):
    def setUp(self):
        self.context = read("context.json")
        self.owner = self.context["owner_id"]
        self.prepared = prepared_fixture()
        self.message = read("core-required.json")

    def assert_code(self, expected, callback):
        with self.assertRaises(CoreDecisionError) as raised:
            callback()
        self.assertEqual(raised.exception.code, expected)
        self.assertEqual(str(raised.exception), expected)

    def check(self, context=None, prepared=None, owner=None):
        return check_current(
            self.prepared if prepared is None else prepared,
            current_context=self.context if context is None else context,
            authenticated_owner_id=self.owner if owner is None else owner,
        )

    def test_eight_pinned_artifacts_are_exact(self):
        self.assertEqual(verify_upstream_bytes(), 8)

    def test_non_core_reproduces_w1_question_fixture(self):
        self.assertEqual(prepared_fixture("non-core.json")["message"], read("upstream/question-decision.json"))

    def test_required_message_is_private_question_scoped(self):
        self.assertEqual(self.check(), self.message)
        self.assertIsNone(self.message["payload"]["company_id"])
        self.assertIsNotNone(self.prepared["context"]["collection_company_id"])
        self.assertNotIn("owner_id", self.message["payload"])
        self.assertNotIn("project_id", self.message["payload"])

    def test_negative_wire_matrix(self):
        cases = read("negative-messages.json")
        self.assertEqual(len(cases), 20)
        for case in cases:
            with self.subTest(case=case["case_id"]):
                self.assert_code(case["expected_error"], lambda: validate_message(case["message"]))

    def test_non_object_and_non_json_values_fail_closed(self):
        for value in (None, [], True, "private-text", {"private": object()}, {"private": float("nan")}):
            with self.subTest(kind=type(value).__name__):
                self.assert_code("CORE_DECISION_CONTRACT_INVALID", lambda: validate_message(value))

    def test_oversized_envelope_is_rejected_without_private_error(self):
        value = {**self.message, "raw": "private" * 20_000}
        self.assert_code("CORE_DECISION_CONTRACT_INVALID", lambda: validate_message(value))

    def test_prepare_requires_authorized_owner(self):
        value = read("core-required.json")
        self.assert_code("CORE_OWNER_MISMATCH", lambda: prepare_decision(
            self.context, authenticated_owner_id="20000000-0000-4000-8000-000000000003",
            message_id=value["message_id"], decision_id=value["payload"]["decision_id"],
            occurred_at=value["occurred_at"], decision_version=3,
            decision_code="CORE_REQUIRED", reason_code="QUESTION_EVIDENCE_REQUIRED"))

    def test_recheck_requires_authorized_owner(self):
        self.assert_code("CORE_OWNER_MISMATCH", lambda: self.check(owner="20000000-0000-4000-8000-000000000003"))

    def test_real_input_is_not_enabled(self):
        self.assert_code("CORE_REAL_DATA_NOT_ENABLED", lambda: self.check(context={**self.context, "data_kind": "REAL"}))

    def test_context_flags_are_mandatory(self):
        for name in ("processing_allowed", "question_current", "source_active"):
            value = deepcopy(self.context)
            value.pop(name)
            with self.subTest(field=name):
                self.assert_code("CORE_CONTEXT_INVALID", lambda: self.check(context=value))

    def test_false_context_flags_stop_recheck(self):
        for name in ("processing_allowed", "question_current", "source_active"):
            with self.subTest(field=name):
                self.assert_code("CORE_CONTEXT_NOT_CURRENT", lambda: self.check(context={**self.context, name: False}))

    def test_context_has_strict_types(self):
        for name, value in (("current_decision_version", True), ("current_decision_version", 1.0),
                            ("current_decision_version", -1), ("analysis_input_version", 4),
                            ("analysis_input_version", "   "), ("source_active", "true"),
                            ("question_version_id", "question"), ("unapproved_field", True)):
            with self.subTest(field=name, value=value):
                self.assert_code("CORE_CONTEXT_INVALID", lambda: self.check(context={**self.context, name: value}))

    def test_new_context_identity_or_authorization_invalidates_prepared(self):
        changes = {
            "project_id": "20000000-0000-4000-8000-000000000010",
            "question_version_id": "20000000-0000-4000-8000-000000000009",
            "source_id": "20000000-0000-4000-8000-000000000005",
            "collection_company_id": "20000000-0000-4000-8000-000000000004",
            "analysis_input_version": "question-input:v5", "authorization_revision": "authorization-v2",
        }
        for name, value in changes.items():
            with self.subTest(field=name):
                self.assert_code("CORE_CONTEXT_CHANGED", lambda: self.check(context={**self.context, name: value}))

    def test_owner_change_is_rejected(self):
        self.assert_code("CORE_OWNER_MISMATCH", lambda: self.check(
            context={**self.context, "owner_id": "20000000-0000-4000-8000-000000000003"}))

    def test_new_decision_must_exceed_known_cursor(self):
        for cursor in (3, 4):
            with self.subTest(cursor=cursor):
                self.assert_code("CORE_DECISION_VERSION_NOT_NEW", lambda: prepared_fixture(
                    context={**self.context, "current_decision_version": cursor}))

    def test_lost_ack_retry_preserves_ids_time_and_body(self):
        original = deepcopy(self.prepared)
        for cursor in (1, 2, 3):
            with self.subTest(cursor=cursor):
                self.assertEqual(self.check(context={**self.context, "current_decision_version": cursor}), self.message)
        self.assertEqual(self.prepared, original)

    def test_higher_current_cursor_blocks_retry(self):
        self.assert_code("CORE_DECISION_STALE", lambda: self.check(context={**self.context, "current_decision_version": 4}))

    def test_current_cursor_cannot_roll_back_within_same_input(self):
        self.assert_code("CORE_DECISION_CURSOR_REGRESSED", lambda: self.check(
            context={**self.context, "current_decision_version": 0}))

    def test_prepared_metadata_is_not_optional_or_adopted(self):
        for key, value in (("schema_version", "old"), ("w1_pin", "wrong"), ("status", "ADOPTED")):
            with self.subTest(key=key):
                self.assert_code("CORE_PREPARED_INVALID", lambda: self.check(prepared={**self.prepared, key: value}))
        self.assert_code("CORE_PREPARED_INVALID", lambda: self.check(prepared={**self.prepared, "extra": 1}))

    def test_prepared_context_changed_without_hash_is_rejected(self):
        value = deepcopy(self.prepared)
        value["context"]["analysis_input_version"] = "question-input:v5"
        self.assert_code("CORE_CONTEXT_CHANGED", lambda: self.check(prepared=value))

    def test_prepared_message_changed_without_hash_is_rejected(self):
        for changes in ({"message_id": "40000000-0000-4000-8000-000000000003"},
                        {"occurred_at": "2026-09-18T03:00:01Z"}):
            value = deepcopy(self.prepared)
            value["message"].update(changes)
            with self.subTest(changes=changes):
                self.assert_code("CORE_PREPARED_MESSAGE_CHANGED", lambda: self.check(prepared=value))

    def test_hash_alone_does_not_replace_payload_context_binding(self):
        value = deepcopy(self.prepared)
        value["message"]["payload"]["source_id"] = "20000000-0000-4000-8000-000000000005"
        value["message_sha256"] = content_hash(value["message"])
        self.assert_code("CORE_INPUT_BINDING_MISMATCH", lambda: self.check(prepared=value))

    def test_returned_message_is_not_shared_mutable_state(self):
        value = self.check()
        value["payload"]["decision_version"] = 99
        self.assertEqual(self.check(), self.message)

    def test_six_receipts_never_establish_result_visibility(self):
        receipts = read("receipts.json")
        self.assertEqual(len(receipts), 6)
        for receipt in receipts:
            with self.subTest(outcome=receipt["outcome"]):
                result = receipt_outcome(receipt, message=self.message)
                self.assertEqual(result["outcome"], receipt["outcome"])
                self.assertEqual(result["retry_same_message"], receipt["outcome"] == "RETRYABLE_INFRA_FAILURE")
                self.assertEqual(result["collection_completion"], "NOT_ESTABLISHED_BY_RECEIPT")
                self.assertEqual(result["current_result_visibility"], "NOT_ESTABLISHED_BY_RECEIPT")

    def test_receipt_is_bound_to_message_and_consumer(self):
        receipt = read("receipts.json")[0]
        for changes in ({"consumer": "w1.collection-result"},
                        {"message_id": "40000000-0000-4000-8000-000000000003"}):
            with self.subTest(changes=changes):
                self.assert_code("CORE_RECEIPT_BINDING_MISMATCH", lambda: receipt_outcome(
                    {**receipt, **changes}, message=self.message))

    def test_receipt_retry_and_error_are_not_inferred_from_outcome_only(self):
        receipts = read("receipts.json")
        invalid = [{**receipts[0], "retryable": True}, {**receipts[0], "error_code": "PRIVATE"},
                   {**receipts[1], "error_code": None}, {**receipts[-1], "retryable": False},
                   {**receipts[-1], "error_code": ""}, {**receipts[0], "outcome": "REJECTED"}]
        for value in invalid:
            with self.subTest(outcome=value["outcome"]):
                self.assert_code("CORE_DECISION_CONTRACT_INVALID", lambda: receipt_outcome(value, message=self.message))

    def test_w2_commit_gate_ack_is_not_a_core_receipt(self):
        self.assert_code("CORE_DECISION_CONTRACT_INVALID", lambda: receipt_outcome(
            {"schema_version": "w2.commit-gate-ack.v1", "outcome": "APPLIED"}, message=self.message))

    def test_company_scope_control_passes_both_contracts(self):
        control = company_binding_probes()[0]
        self.assertTrue(control["w2_schema_valid"])
        self.assertTrue(control["w1_binding_valid"])

    def test_three_question_company_mismatches_are_reproduced(self):
        rows = company_binding_probes()[1:]
        self.assertEqual([(row["w2_schema_valid"], row["w1_binding_valid"]) for row in rows],
                         [(True, False), (False, True), (True, False)])
        self.assertTrue(all(row["expected_reproduced"] for row in rows))

    def test_full_offline_demo_distinguishes_pass_from_adoption(self):
        result = run_demo()
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(result["proposal_status"], "W4_UNADOPTED_PROPOSAL_TRANSPORT_DISCONNECTED")
        self.assertEqual(result["external_messages_sent"], 0)
        self.assertEqual(result["w1_live_queue"], "NOT_RUN")
        self.assertEqual(result["ct12"], "NOT_RUN")


if __name__ == "__main__":
    unittest.main()
