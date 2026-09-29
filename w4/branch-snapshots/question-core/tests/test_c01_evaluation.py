"""The small C01 pilot must not hide missing calls or reward invented support."""

from copy import deepcopy
import importlib.util
from pathlib import Path
import unittest

from examples.w4_c01_demo import C01DemoClient

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("c01_evaluation", ROOT / "scripts/run-c01-evaluation.py")
EVALUATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVALUATION)


class C01EvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, _, _, cls.company, cls.gold, cls.cases = EVALUATION.protocol()

    def records(self):
        model = C01DemoClient("judgment")
        return [{"case_id": case["case_id"], "response": model.complete_json(
            stage="c01_company_details", system_prompt=EVALUATION.PROMPT, payload=case["payload"]),
            "error": None} for case in self.cases]

    def score(self, rows):
        return EVALUATION.score_judgment(rows, self.cases, self.company, self.gold)

    def test_expected_fiction_scores_without_double_counting_sources(self):
        result = self.score(self.records())
        self.assertTrue(result["demo_gate_passed"])
        self.assertTrue(all(score == 100 for score in result["scores"].values()))
        self.assertEqual(result["counts"]["leaf_status"], [18, 18])
        self.assertEqual(result["counts"]["question_status"], [12, 12])

    def test_missing_call_keeps_the_full_denominator(self):
        result = self.score(self.records()[:-1])
        self.assertEqual(result["scheduled_requests"], 6)
        self.assertEqual(result["valid_requests"], 5)
        self.assertEqual(result["counts"]["leaf_status"][1], 18)
        self.assertFalse(result["demo_gate_passed"])

    def test_duplicate_or_unknown_call_is_not_silently_counted(self):
        rows = self.records()
        with self.assertRaises(ValueError):
            self.score(rows + [rows[0]])
        rows[0]["case_id"] = "unknown"
        with self.assertRaises(ValueError):
            self.score(rows)

    def test_one_source_disagreement_fails_the_unique_condition(self):
        rows = self.records()
        row = next(r for r in rows if r["case_id"] == "expertise/v4-raw-study")
        leaf = row["response"]["requirement_checks"][1]["nodes"][0]
        leaf.update(status="NOT_SHOWN", evidence_ids=[])
        result = self.score(rows)
        self.assertEqual(result["valid_requests"], 6)
        self.assertEqual(result["counts"]["leaf_status"], [17, 18])
        self.assertFalse(result["demo_gate_passed"])

    def test_correct_status_without_sufficient_source_is_not_evidence_accuracy(self):
        rows = self.records()
        row = next(r for r in rows if r["case_id"] == "expertise/v4-raw-study")
        row["response"]["checks"][1]["evidence_ids"] = ["u3"]
        result = self.score(rows)
        self.assertEqual(result["scores"]["question_status"], 100)
        self.assertLess(result["scores"]["evidence_sufficiency"], 100)
        self.assertFalse(result["demo_gate_passed"])

    def test_invented_documentation_is_a_false_positive(self):
        rows = self.records()
        row = next(r for r in rows if r["case_id"] == "expertise/v4-raw-study")
        leaf = row["response"]["requirement_checks"][0]["nodes"][2]
        leaf.update(status="SUPPORTED", evidence_ids=["u3"])
        result = self.score(rows)
        self.assertEqual(result["false_positive_company_support"], 1)
        self.assertFalse(result["demo_gate_passed"])

    def test_malformed_response_cannot_be_repaired_into_partial_success(self):
        rows = self.records()
        rows[0]["response"]["requirement_checks"].pop()
        result = self.score(rows)
        self.assertEqual(result["valid_requests"], 5)
        self.assertTrue(all(value[0] == 0 for value in result["details"][0]["counts"].values()))

    def test_transport_failure_never_earns_a_score_from_leftover_response(self):
        rows = deepcopy(self.records())
        rows[0]["error"] = "LLM_NETWORK_ERROR"
        result = self.score(rows)
        self.assertEqual(result["valid_requests"], 5)
        self.assertTrue(all(value[0] == 0 for value in result["details"][0]["counts"].values()))

    def test_own_role_context_may_accompany_actual_action(self):
        rows = self.records()
        row = next(r for r in rows if r["case_id"] == "job_experience/v4-raw-study")
        row["response"]["checks"][0]["evidence_ids"] = ["u1", "u3"]
        for requirement in row["response"]["requirement_checks"]:
            requirement["nodes"][0]["evidence_ids"] = ["u1", "u3"]
        self.assertTrue(self.score(rows)["demo_gate_passed"])

    def test_role_context_alone_does_not_replace_action(self):
        rows = self.records()
        row = next(r for r in rows if r["case_id"] == "job_experience/v4-raw-study")
        row["response"]["checks"][0]["evidence_ids"] = ["u1"]
        result = self.score(rows)
        self.assertEqual(result["scores"]["question_status"], 100)
        self.assertLess(result["scores"]["evidence_sufficiency"], 100)
        self.assertFalse(result["demo_gate_passed"])

    def test_rescoring_cannot_change_captured_input_or_models(self):
        _, raw, reference, _, _, cases = EVALUATION.protocol()
        manifest = {"models": [{"candidate_id": "example"}]}
        configuration = {"models": manifest, "prompt_version": EVALUATION.PROMPT_VERSION, "fit_target_mib": 6144}
        protocol = {"raw": raw, "extraction_reference": reference, "cases": cases}
        EVALUATION.validate_capture(protocol, configuration, raw=raw, reference=reference,
                                    cases=cases, manifest=manifest, fit_target=6144)
        changed = deepcopy(protocol)
        changed["cases"][0]["payload"]["source_units"][0]["text"] = "changed"
        with self.assertRaises(ValueError):
            EVALUATION.validate_capture(changed, configuration, raw=raw, reference=reference,
                                        cases=cases, manifest=manifest, fit_target=6144)

    def test_successful_api_result_still_fails_for_false_company_connection(self):
        from epick_w4.c01_consumer import C01Consumer
        from epick_w4.service_adapter import execute_service
        from examples.w4_c01_demo import C01DemoBackend, FixtureTransport
        from examples.w4_service_demo import read
        import tempfile

        backend = C01DemoBackend()
        with tempfile.TemporaryDirectory() as folder:
            consumer = C01Consumer(Path(folder) / "test.sqlite", transport=FixtureTransport(backend.context),
                                   max_cache_ttl_seconds=300)
            try:
                consumer.drain()
                result = execute_service(read("request.json"), user_id="user-demo", backend=backend,
                    extraction_factory=lambda: C01DemoClient("extraction"),
                    judgment_factory=lambda: C01DemoClient("judgment"), c01_consumer=consumer)
            finally:
                consumer.close()
        self.assertTrue(EVALUATION.audit_demo_result(result, self.gold)["quality_passed"])
        positive = next(c for c in result["candidates"] if c["episode_id"] == "v4-raw-study")
        negative = next(c for c in result["candidates"] if c["episode_id"] == "v4-raw-challenge")
        negative["company_support"].append(deepcopy(positive["company_support"][0]))
        audit = EVALUATION.audit_demo_result(result, self.gold)
        self.assertFalse(audit["quality_passed"])
        self.assertEqual(audit["failures"][0]["code"], "FALSE_POSITIVE_COMPANY_SUPPORT")

    def test_final_demo_cannot_hide_missing_candidates(self):
        audit = EVALUATION.audit_demo_result({"candidates": []}, self.gold)
        self.assertFalse(audit["quality_passed"])
        self.assertEqual(audit["failures"], [{"code": "CANDIDATE_COVERAGE_MISMATCH"}])


if __name__ == "__main__":
    unittest.main()
