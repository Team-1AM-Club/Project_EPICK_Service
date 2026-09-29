"""Focused checks for independent quote/context/meaning and rejected-output diagnostics."""

from copy import deepcopy
import unittest

from epick_w4.evaluation_diagnostics import score_question_components
from epick_w4.stage_eval import COMPONENT_SCORER_VERSION, collect_stage_run, evaluate_stage_runs
from tests.test_stage_eval import Fixture, read


class ComponentEvaluationTests(unittest.TestCase):
    def custom(self):
        case = deepcopy(next(c for c in read("samples/evaluation/benchmark.v3.draft.json")["cases"]
                             if c["case_id"] == "custom-criterion"))
        case["criterion_label_aliases"] = {"custom_1": ["사용자 관점의 제안"]}
        return case, deepcopy(case["question_answers"][0])

    def test_frozen_alias_accepts_wording_without_ignoring_meaning(self):
        case, answer = self.custom()
        answer["criteria"][0]["label"] = "사용자 관점의 제안"
        self.assertTrue(score_question_components(case, answer)["correct"])
        answer["criteria"][0]["label"] = "리더십"
        d = score_question_components(case, answer)
        self.assertTrue(d["literal_quote_valid"] and d["context_preserved"])
        self.assertFalse(d["criteria_correct"] or d["correct"])

    def test_cropped_negation_has_literal_quote_but_loses_context(self):
        case, answer = self.custom()
        case["question_text"] = "사용자 불편을 무시하지 않고 제안한 경험"
        for ref in case["question_answers"]:
            ref["criteria"][0]["question_quote"] = "사용자 불편을 무시하지 않고 제안"
        answer["criteria"][0]["question_quote"] = "제안"
        d = score_question_components(case, answer)
        self.assertTrue(d["literal_quote_valid"] and d["criteria_correct"])
        self.assertFalse(d["context_preserved"])

    def test_invented_quote_and_missing_criteria_do_not_pass_source_check(self):
        case, answer = self.custom()
        answer["criteria"][0]["question_quote"] = "원문에 없는 설명"
        self.assertFalse(score_question_components(case, answer)["literal_quote_valid"])
        answer["criteria"] = []
        self.assertFalse(score_question_components(case, answer)["literal_quote_valid"])

    def make_run(self):
        b = read("samples/evaluation/benchmark.v3.draft.json")
        p = read("samples/evaluation/policy.v3.draft.json")
        p.update(schema_version="w4-stage-policy/0.3", scorer_version=COMPONENT_SCORER_VERSION,
                 gates={"contract_valid_percent": 100, "literal_quote_valid_percent": 100,
                        "context_preserved_percent": 100, "unsafe_fact_admissions": 0,
                        "unsafe_fact_references": 0})
        return b, p, collect_stage_run(b, p, Fixture(b), candidate_id="fixture")

    def test_invalid_reference_does_not_hide_another_unsafe_fact(self):
        b, p, run = self.make_run()
        record = next(r for r in run["records"] if r["case_id"] == "instruction-in-source"
                      and r["episode_id"] == "source-command")
        candidate = record["response"]["candidates"][0]
        candidate["fact_checks"][0].update(usable=True, issue=None)
        candidate["matches"] = [{"criterion_id": "collaboration", "fact_ids": ["unknown-fact"]}]
        report = evaluate_stage_runs(b, p, [run])
        row = report["results"][0]
        d = next(d for d in row["details"] if d["case_id"] == record["case_id"] and d["episode_id"] == record["episode_id"])
        self.assertFalse(d["contract_valid"])
        self.assertEqual(d["safety_diagnostics"]["reported_unsafe_fact_ids"], ["source-command-1"])
        self.assertEqual(d["safety_diagnostics"]["reported_unsafe_reference_ids"], ["unknown-fact"])
        self.assertIn("UNSAFE_FACT_ADMITTED", row["gate_failures"])
        self.assertIn("UNSAFE_FACT_REFERENCED", row["gate_failures"])
        self.assertEqual(report["recommendation"]["status"], "WITHHELD")

    def test_duplicate_or_missing_fact_checks_show_incomplete_coverage(self):
        b, p, run = self.make_run()
        record = next(r for r in run["records"] if r["axis"] == "matching")
        checks = record["response"]["candidates"][0]["fact_checks"]
        checks[1] = deepcopy(checks[0])
        row = evaluate_stage_runs(b, p, [run])["results"][0]
        d = row["dimensions"]["matching"]
        self.assertLess(d["diagnosed_fact_checks"], d["scheduled_fact_checks"])
        self.assertFalse(row["eligible"])

    def test_legacy_report_is_unchanged_by_component_scorer(self):
        b, p, run = self.make_run()
        legacy = read("samples/evaluation/policy.v3.draft.json")
        old = evaluate_stage_runs(b, legacy, [run])
        self.assertEqual(old["scorer_version"], "w4-separated-grounding/0.2")
        self.assertNotIn("literal_quote_valid", old["results"][0]["dimensions"]["question"])
        self.assertEqual(old["results"][0]["scores"], {"question": 100, "matching": 100})


if __name__ == "__main__":
    unittest.main()
