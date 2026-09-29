"""Scripted tests of the new scorer; these are never saved as model measurements."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from epick_w4.extraction_eval import collect_extraction_run, evaluate_extraction_runs
from epick_w4.model_eval import EvaluationError, digest, approve_document
from epick_w4.output_schemas import schema_for
from epick_w4.stage_eval import build_stage_requests, collect_stage_run, evaluate_stage_runs, score_question
from epick_w4.synthetic_policy import check_sample_payload

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


class Fixture:
    provider = "scripted-test"
    model = "fixture"
    simulated = True
    generation_config = {"fixture": True}

    def __init__(self, benchmark):
        self.answers = {}
        cases = {c["case_id"]: c for c in benchmark["cases"]}
        for r in build_stage_requests(benchmark):
            case = cases[r["case_id"]]
            answer = (case["question_answers"][0] if r["axis"] == "question" else
                      {"candidates": [c for c in case["matching"]["answers"][0]["candidates"]
                                      if c["episode_id"] == r["episode_id"]]})
            self.answers[digest(r["payload"])] = deepcopy(answer)

    def complete_json(self, **kwargs):
        return deepcopy(self.answers[digest(kwargs["payload"])])


class StageEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.b = read("samples/evaluation/benchmark.v2.draft.json")
        self.p = read("samples/evaluation/policy.v2.draft.json")
        self.run = collect_stage_run(self.b, self.p, Fixture(self.b), candidate_id="test")

    def row(self):
        return evaluate_stage_runs(self.b, self.p, [self.run])["results"][0]

    def test_serving_boundary_and_fixed_fictional_transmission(self):
        requests = build_stage_requests(self.b)
        self.assertEqual(len(requests), 44)
        self.assertEqual(sum(c["split"] == "holdout" for c in self.b["cases"]), 8)
        for request in requests:
            if request["axis"] == "matching":
                self.assertEqual(len(request["payload"]["candidates"]), 1)
            check_sample_payload(**{k: request[k] for k in ("stage", "system_prompt", "payload")})
        self.assertNotIn("question_answers", json.dumps(requests))

    def test_perfect_draft_has_separate_dimensions_and_cannot_select(self):
        report = evaluate_stage_runs(self.b, self.p, [self.run])
        self.assertEqual(report["recommendation"]["status"], "WITHHELD")
        row = report["results"][0]
        self.assertEqual(row["scores"], {"question": 100, "matching": 100})
        self.assertTrue(row["eligible"])
        self.assertEqual(row["dimensions"]["matching"]["scheduled"], 28)
        self.assertEqual(row["split_scores"]["holdout"]["question"], 100)

    def test_longer_valid_quote_is_accepted_without_editing_reference(self):
        case = self.b["cases"][0]
        answer = deepcopy(case["question_answers"][0])
        answer["criteria"][0]["question_quote"] = case["question_text"]
        self.assertTrue(score_question(case, answer)["correct"])

    def test_cropped_complete_anchor_fails_even_when_criterion_is_correct(self):
        case = deepcopy(self.b["cases"][0])
        case["question_text"] = "갈등을 피하지 않고 조율한 경험을 설명하세요."
        gold = case["question_answers"][0]
        gold["criteria"][0]["question_quote"] = "갈등을 피하지 않고 조율"
        prediction = deepcopy(gold)
        prediction["criteria"][0]["question_quote"] = "조율"
        detail = score_question(case, prediction)
        self.assertTrue(detail["criteria_correct"])
        self.assertFalse(detail["question_quote_valid"])

    def test_json_format_criteria_and_grounding_are_independent(self):
        self.run["records"][0]["response"]["criteria"][0]["question_quote"] = "invented source"
        detail = self.row()["details"][0]
        self.assertTrue(detail["json_valid"] and detail["schema_valid"])
        self.assertTrue(detail["criteria_correct"])
        self.assertFalse(detail["question_quote_valid"] or detail["contract_valid"])

    def test_missing_one_experience_does_not_erase_other_scores(self):
        record = next(r for r in self.run["records"] if r["axis"] == "matching")
        record["response"] = {"candidates": []}
        row = self.row()
        self.assertAlmostEqual(row["scores"]["matching"], 100 * 27 / 28, places=5)
        self.assertFalse(row["eligible"])
        self.assertIn("INVALID_OR_MISSING_OUTPUT", row["gate_failures"])

    def test_team_or_negated_action_admission_fails_required_gate(self):
        record = next(r for r in self.run["records"] if r["case_id"] == "holdout-team-attribution"
                      and r["episode_id"] and r["episode_id"].endswith("-2"))
        record["response"]["candidates"][0]["fact_checks"][1].update(usable=True, issue=None)
        row = self.row()
        self.assertEqual(row["dimensions"]["matching"]["schema_valid"], 100)
        self.assertEqual(row["dimensions"]["matching"]["unsafe_fact_admissions"], 1)
        self.assertIn("UNSAFE_FACT_ADMITTED", row["gate_failures"])

    def test_missing_response_stays_in_denominator_and_wrong_request_is_rejected(self):
        self.run["records"].pop()
        self.assertLess(self.row()["scores"]["question"], 100)
        self.run["records"][0]["request_sha256"] = "different"
        with self.assertRaisesRegex(EvaluationError, "REQUEST_MISMATCH"):
            self.row()

    def test_input_bound_schema_does_not_supply_semantic_judgments(self):
        request = next(r for r in build_stage_requests(self.b) if r["axis"] == "matching")
        schema = schema_for("candidates", request["payload"])
        array = schema["properties"]["candidates"]
        fields = array["items"]["anyOf"][0]["properties"]
        self.assertEqual(array["minItems"], 1)
        self.assertEqual(array["maxItems"], 1)
        self.assertEqual(fields["episode_id"]["const"], request["episode_id"])
        self.assertEqual(fields["relevance"]["enum"], ["RELATED", "UNCERTAIN"])
        self.assertEqual(array["items"]["anyOf"][1]["properties"]["relevance"], {"const": "UNRELATED"})
        checks = fields["fact_checks"]["items"]["anyOf"]
        self.assertEqual([c["properties"]["usable"]["const"] for c in checks], [True, False])

    def test_production_selector_accepts_v2_only_after_review_and_checks(self):
        from epick_w4.model_selection import select_evaluated_client
        from epick_w4.llm_contract import LLMError
        with self.assertRaisesRegex(LLMError, "LLM_EVALUATION_NOT_APPROVED"):
            select_evaluated_client(self.b, self.p, [self.run], client_factories={})
        a, b = deepcopy(self.run), deepcopy(self.run)
        for run, name in ((a, "a"), (b, "b")):
            run.update(candidate_id=name, model=name, provenance="IMPORTED")
        b["records"][0]["response"]["required_facts"] = []
        a, b = approve_document(a, "test-only"), approve_document(b, "test-only")
        class Selected(Fixture):
            simulated = False
            model = "a"
        selected = select_evaluated_client(approve_document(self.b, "test-only"),
                                           approve_document(self.p, "test-only"), [a, b],
                                           client_factories={"a": lambda: Selected(self.b)})
        self.assertEqual(selected.model, "a")


class ExtractionSafetyTests(unittest.TestCase):
    def test_misattributed_and_planned_facts_are_separate_critical_errors(self):
        source = read("samples/extraction/raw-experiences.v2.synthetic.json")
        reference = read("samples/extraction/benchmark.v2.draft.json")
        class ExtractFixture:
            provider, model, simulated = "fixture", "fixture", True
            generation_config = {"fixture": True}
            def complete_json(self, **kwargs):
                return deepcopy(reference["answers"][kwargs["payload"]["episode_id"]])
        run = collect_extraction_run(source, reference, ExtractFixture(), candidate_id="test", repetitions=1)
        planned = next(r for r in run["records"] if r["case_id"] == "holdout-raw-future")
        planned["response"]["units"][2]["assertion"] = "AFFIRMED"
        attribution = next(r for r in run["records"] if r["case_id"] == "holdout-raw-attribution")
        attribution["response"]["units"][1]["subject"] = "SELF"
        row = evaluate_extraction_runs(source, reference, [run])["results"][0]
        self.assertEqual(row["valid_requests"], 8)
        self.assertEqual(row["unsafe_fact_admissions"], 2)
        self.assertFalse(row["required_checks_passed"])


if __name__ == "__main__":
    unittest.main()
