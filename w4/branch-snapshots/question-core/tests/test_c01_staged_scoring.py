"""Scorer checks are separate from evidence about any actual model's quality."""
from copy import deepcopy
import importlib.util
import unittest

from epick_w4.c01_adapter import consume_c01
from epick_w4.detail_contract import question_spec
from epick_w4.evidence_extraction import extract_evidence
from examples.w4_c01_fresh_demo import cases, protocol
from tests.test_c01_domain_evaluation import ROOT, EVAL

SPEC = importlib.util.spec_from_file_location("staged_scoring", ROOT / "scripts/run-c01-staged-evaluation.py")
STAGED = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STAGED)


class StagedScoringTests(unittest.TestCase):
    def setUp(self):
        context, self.raw, reference = protocol()
        self.gold = {"cases": cases()}
        self.company = consume_c01(context["company_knowledge"], context["question_scope_id"])
        self.question = question_spec({"scope_id": context["question_scope_id"], "question_id": "expertise"})
        class Reference:
            simulated = True
            provider = model = "test-only-reference"
            def complete_json(self, *, payload, **kwargs):
                return deepcopy(reference["answers"][payload["episode_id"]])
        self.extraction = extract_evidence(self.raw, user_id="user-demo", llm=Reference())
        self.records = []
        for case in self.gold["cases"]:
            def row(key, value, expected):
                return {key: value, "status": expected["statuses"][0], "evidence_ids": expected["required_units"][:]}
            response = {"checks": [row("check_id", k, e) for k, e in case["checks"].items()],
                        "company_links": [], "requirement_checks": []}
            for criterion in self.company.criteria:
                if criterion["kind"] == "CLAIM":
                    if "SUPPORTED" in case["claim"]["statuses"]:
                        response["company_links"].append({"id": criterion["id"], "kind": "CLAIM",
                                                         "evidence_ids": case["claim"]["required_units"][:]})
                else:
                    response["requirement_checks"].append({"id": criterion["id"],
                        "nodes": [row("node_id", key, expected) for key, expected in case["leaves"].items()]})
            self.records.append({"case_id": case["case_id"], "response": response, "error": None})

    def score(self):
        result = EVAL.score_records(self.records, self.extraction, self.company, self.question, self.gold)
        return result, STAGED.metrics(result, self.records, self.gold)

    def test_reference_and_broader_relevant_context_pass(self):
        self.records[0]["response"]["checks"][0]["evidence_ids"] = ["u1", "u2", "u3"]
        result, extra = self.score()
        self.assertTrue(result["frozen_case_gate_passed"])
        self.assertTrue(extra["demo_gate_passed"])

    def test_ambiguous_omission_is_separate_from_false_affirmative(self):
        row = self.records[3]["response"]["checks"][0]
        row.update(status="AMBIGUOUS", evidence_ids=["u2"])
        result, extra = self.score()
        self.assertTrue(extra["demo_gate_passed"])
        self.assertEqual(extra["uncertain_negative_question_checks"], 1)
        row.update(status="SUPPORTED")
        result, extra = self.score()
        self.assertFalse(extra["demo_gate_passed"])
        self.assertEqual(extra["raw_question_false_support"], 1)

    def test_omission_is_not_contradiction(self):
        self.records[2]["response"]["requirement_checks"][0]["nodes"][0].update(
            status="CONTRADICTED", evidence_ids=["u3"])
        result, extra = self.score()
        self.assertFalse(extra["demo_gate_passed"])
        self.assertEqual(result["false_positive_company_support"], 0)

    def test_missing_positive_case_keeps_denominator(self):
        self.records.pop(0)
        result, extra = self.score()
        self.assertEqual(extra["positive_question_checks"], 9)
        self.assertEqual(extra["recalled_positive_question_checks"], 6)
        self.assertFalse(extra["demo_gate_passed"])

    def test_status_alone_does_not_replace_necessary_citation(self):
        self.records[0]["response"]["checks"][0]["evidence_ids"] = ["u3"]
        result, extra = self.score()
        self.assertEqual(result["scores"]["question_status"], 100)
        self.assertLess(result["scores"]["evidence_sufficiency"], 100)
        self.assertFalse(extra["demo_gate_passed"])


if __name__ == "__main__":
    unittest.main()
