"""Evaluator tests use scripted fixtures. They never measure actual LLM quality."""

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from epick_w4.evaluate import main as cli, report_markdown
from epick_w4.llm_contract import LLMError
from epick_w4.model_eval import (
    EvaluationError, approval_valid, approve_document, build_requests, collect_run,
    digest, document_digest, evaluate_runs, validate_benchmark, validate_policy,
)

ROOT = Path(__file__).resolve().parents[1]


def fixtures():
    base = ROOT / "samples" / "evaluation"
    return tuple(json.loads((base / name).read_text(encoding="utf-8"))
                 for name in ("benchmark.draft.json", "policy.draft.json"))


class FixtureClient:
    provider = "unit-test-fixture"
    simulated = True

    def __init__(self, benchmark, name="fixture-a"):
        self.model = name
        self.requests = []
        cases = {case["case_id"]: case for case in benchmark["cases"]}
        self.answers = {}
        for request in build_requests(benchmark):
            case = cases[request["case_id"]]
            answers = case["question_answers"] if request["axis"] == "question" else case["matching"]["answers"]
            self.answers[(request["stage"], digest(request["payload"]))] = copy.deepcopy(answers[0])

    def complete_json(self, *, stage, system_prompt, payload):
        self.requests.append({"stage": stage, "system_prompt": system_prompt, "payload": copy.deepcopy(payload)})
        return copy.deepcopy(self.answers[(stage, digest(payload))])


def fixture_run(benchmark, policy, name="fixture-a"):
    return collect_run(benchmark, policy, FixtureClient(benchmark, name), candidate_id=name,
                       generation_config={"fixture": True})


def recorded_fixture(run):
    # Only in tests: emulate a human-reviewed imported API artifact. No actual
    # endpoint is used and these objects are never written as model evidence.
    value = copy.deepcopy(run)
    value["provenance"] = "IMPORTED"
    return approve_document(value, "unit-test-import-review")


class ModelEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.benchmark, self.policy = fixtures()
        self.a = fixture_run(self.benchmark, self.policy)
        self.b = fixture_run(self.benchmark, self.policy, "fixture-b")

    def reviewed(self):
        return (approve_document(self.benchmark, "unit-test-human"),
                approve_document(self.policy, "unit-test-human"))

    def record(self, run, case="paraphrase", axis="question", repeat=0):
        return next(r for r in run["records"] if (r["case_id"], r["axis"], r["repeat"]) == (case, axis, repeat))

    def score(self, *runs):
        return evaluate_runs(self.benchmark, self.policy, list(runs or (self.a, self.b)))

    def test_draft_perfect_fixture_is_preview_not_recommendation(self):
        result = self.score()
        self.assertEqual(result["status"], "PREVIEW_ONLY")
        self.assertEqual(result["recommendation"]["candidate_ids"], [])
        self.assertEqual(result["results"][0]["scores"], {"question": 100, "matching": 100})
        self.assertEqual(result["results"][0]["scheduled"], {"question": 24, "matching": 21})

    def test_reviewed_references_still_cannot_recommend_simulation(self):
        result = evaluate_runs(*self.reviewed(), [self.a, self.b])
        self.assertEqual(result["recommendation"]["status"], "WITHHELD")
        self.assertIn("SIMULATED_RUN:fixture-a", result["recommendation"]["blockers"])

    def test_hand_calculated_weighted_score_includes_every_repeat(self):
        # One question miss / 24 and one matching miss / 21.
        self.record(self.a)["error"] = "TIMEOUT"
        self.record(self.a)["response"] = None
        self.record(self.a, axis="matching")["error"] = "TIMEOUT"
        self.record(self.a, axis="matching")["response"] = None
        self.policy["weights"] = {"question": 3, "matching": 1}
        row = next(r for r in self.score()["results"] if r["candidate_id"] == "fixture-a")
        self.assertAlmostEqual(row["scores"]["question"], 100 * 23 / 24, places=5)
        self.assertAlmostEqual(row["scores"]["matching"], 100 * 20 / 21, places=5)
        self.assertAlmostEqual(row["total_score"], (3 * 100 * 23 / 24 + 100 * 20 / 21) / 4, places=5)

    def test_missing_records_score_zero_and_block_comparison(self):
        self.a["records"].pop()
        result = evaluate_runs(*self.reviewed(), [recorded_fixture(self.a), recorded_fixture(self.b)])
        self.assertIn("INCOMPLETE_RUN:fixture-a", result["recommendation"]["blockers"])
        row = next(r for r in result["results"] if r["candidate_id"] == "fixture-a")
        self.assertEqual(row["scheduled"]["matching"], 21)
        self.assertEqual(row["correct"]["matching"], 20)

    def test_duplicate_or_unknown_case_is_not_silently_skipped(self):
        for change in (lambda r: r["records"].append(copy.deepcopy(r["records"][0])),
                       lambda r: r["records"][0].update(case_id="unregistered")):
            value = copy.deepcopy(self.a)
            change(value)
            with self.assertRaises(EvaluationError):
                self.score(value, self.b)

    def test_actual_request_hash_must_match_not_only_run_header(self):
        self.a["records"][0]["request_sha256"] = "wrong"
        with self.assertRaisesRegex(EvaluationError, "REQUEST_MISMATCH"):
            self.score()

    def test_different_benchmark_protocol_or_repeat_count_rejected(self):
        for key, wrong in (("benchmark_sha256", "wrong"), ("protocol_sha256", "wrong"), ("repetitions", 1)):
            value = copy.deepcopy(self.a)
            value[key] = wrong
            with self.subTest(key=key), self.assertRaises(EvaluationError):
                self.score(value, self.b)

    def test_modified_approved_weights_require_new_review(self):
        benchmark, policy = self.reviewed()
        policy["weights"]["question"] = 60
        with self.assertRaisesRegex(EvaluationError, "HUMAN_REVIEW_REQUIRED"):
            evaluate_runs(benchmark, policy, [recorded_fixture(self.a), recorded_fixture(self.b)])

    def test_unapproved_references_cannot_even_score_imported_model_outputs(self):
        with self.assertRaisesRegex(EvaluationError, "HUMAN_REVIEW_REQUIRED"):
            self.score(recorded_fixture(self.a), recorded_fixture(self.b))

    def test_modified_gold_or_prompt_invalidates_policy_binding(self):
        benchmark = copy.deepcopy(self.benchmark)
        benchmark["cases"][0]["review_note"] += " changed"
        with self.assertRaisesRegex(EvaluationError, "BENCHMARK_CHANGED"):
            validate_policy(benchmark, self.policy)
        with patch("epick_w4.model_eval.QUESTION_PROMPT", "different prompt"):
            with self.assertRaisesRegex(EvaluationError, "PROTOCOL_CHANGED"):
                validate_policy(self.benchmark, self.policy)

    def test_review_metadata_is_not_content_but_bad_review_is_not_valid(self):
        approved = approve_document(self.benchmark, "unit-test-human")
        self.assertEqual(document_digest(approved), document_digest(self.benchmark))
        self.assertTrue(approval_valid(approved))
        for change in (lambda r: r.update(reviewer=""), lambda r: r.update(reviewed_at="2026-09-08"),
                       lambda r: r.update(content_sha256="wrong"), lambda r: r.update(status="DRAFT")):
            broken = copy.deepcopy(approved)
            change(broken["review"])
            self.assertFalse(approval_valid(broken))

    def test_unapproved_real_client_is_not_called(self):
        client = FixtureClient(self.benchmark)
        client.simulated = False
        with self.assertRaisesRegex(EvaluationError, "HUMAN_REVIEW_REQUIRED"):
            collect_run(self.benchmark, self.policy, client, candidate_id="a", generation_config={"temperature": 0})
        self.assertEqual(client.requests, [])

    def test_export_does_not_leak_answer_keys_notes_or_policy(self):
        requests = build_requests(self.benchmark)
        exported = json.dumps(requests, ensure_ascii=False)
        for forbidden in ("question_answers", '"answers"', "review_note", "weights", "reviewer"):
            self.assertNotIn(forbidden, exported)
        for request in requests:
            if request["axis"] == "question":
                self.assertEqual(set(request["payload"]), {"question_text"})

    def test_failed_question_does_not_skip_or_contaminate_matching(self):
        client = FixtureClient(self.benchmark)
        method = client.complete_json
        def call(**kwargs):
            if kwargs["stage"] == "question":
                raise LLMError("TIMEOUT", "question")
            return method(**kwargs)
        client.complete_json = call
        run = collect_run(self.benchmark, self.policy, client, candidate_id="a", generation_config={"fixture": True})
        result = self.score(run, self.b)
        row = next(r for r in result["results"] if r["candidate_id"] == "a")
        self.assertEqual(row["scores"], {"question": 0, "matching": 100})
        self.assertEqual(len(client.requests), 21)

    def test_runtime_rule_repair_cannot_hide_model_omitted_requirements(self):
        self.record(self.a)["response"]["required_facts"] = []
        row = next(r for r in self.score()["results"] if r["candidate_id"] == "fixture-a")
        detail = next(d for d in row["details"] if d["case_id"] == "paraphrase" and d["axis"] == "question" and d["repeat"] == 0)
        self.assertFalse(detail["correct"])
        self.assertIn(("required_fact", "ROLE"), detail["missing"])

    def test_multiple_approved_answers_accept_valid_quote_variants(self):
        case = self.benchmark["cases"][0]
        alternative = copy.deepcopy(case["question_answers"][0])
        alternative["criteria"][0]["question_quote"] = case["question_text"]
        case["question_answers"].append(alternative)
        self.policy["benchmark_sha256"] = document_digest(self.benchmark)
        self.policy["protocol_sha256"] = digest(build_requests(self.benchmark))
        a = fixture_run(self.benchmark, self.policy)
        self.record(a)["response"] = alternative
        self.assertEqual(self.score(a)["results"][0]["scores"]["question"], 100)

    def test_custom_label_meaning_is_not_ignored(self):
        self.record(self.a, case="custom-criterion")["response"]["criteria"][0]["label"] = "무관한 판매 실적"
        row = next(r for r in self.score()["results"] if r["candidate_id"] == "fixture-a")
        self.assertEqual(row["correct"]["question"], 23)

    def test_cosmetic_known_label_and_json_order_do_not_change_score(self):
        answer = self.record(self.a)["response"]
        answer["criteria"][0]["label"] = "협력"
        answer["required_facts"].reverse()
        answer = self.record(self.a, axis="matching")["response"]
        answer["candidates"].reverse()
        for item in answer["candidates"]:
            item["fact_checks"].reverse()
        self.assertEqual(self.score()["results"][0]["total_score"], 100)

    def test_unusable_or_cross_episode_reference_is_invalid_not_partial_credit(self):
        item = self.record(self.a, axis="matching")["response"]["candidates"][0]
        item["matches"][0]["fact_ids"] = ["episode-solo-fix-fact-2"]
        row = next(r for r in self.score()["results"] if r["candidate_id"] == "fixture-a")
        detail = next(d for d in row["details"] if d["case_id"] == "paraphrase" and d["axis"] == "matching" and d["repeat"] == 0)
        self.assertEqual(detail["error"], "INVALID_MODEL_RESPONSE")
        self.assertEqual(row["correct"]["matching"], 20)

    def test_schema_failure_and_model_score_injection_are_zero(self):
        for answer in (None, [], {"score": 100}, '{"criteria":[]',
                       '{"criteria":[],"criteria":[],"required_facts":[],"needs_confirmation":true}'):
            a = copy.deepcopy(self.a)
            self.record(a)["response"] = answer
            row = next(r for r in self.score(a, self.b)["results"] if r["candidate_id"] == "fixture-a")
            self.assertEqual(row["correct"]["question"], 23)

    def test_ties_return_all_best_candidates_without_name_tiebreak(self):
        result = evaluate_runs(*self.reviewed(), [recorded_fixture(self.b), recorded_fixture(self.a)])
        self.assertEqual(result["recommendation"]["status"], "TIED")
        self.assertEqual(set(result["recommendation"]["candidate_ids"]), {"fixture-a", "fixture-b"})
        self.assertEqual([r["rank"] for r in result["results"]], [1, 1])

    def test_better_observed_score_wins_regardless_of_name(self):
        self.record(self.a)["response"]["required_facts"] = []
        result = evaluate_runs(*self.reviewed(), [recorded_fixture(self.a), recorded_fixture(self.b)])
        self.assertEqual(result["recommendation"]["candidate_ids"], ["fixture-b"])
        self.assertEqual(result["recommendation"]["status"], "HIGHEST_OBSERVED_SCORE")

    def test_unreviewed_or_changed_import_is_not_measured_evidence(self):
        a = recorded_fixture(self.a)
        a["model"] = "altered"
        result = evaluate_runs(*self.reviewed(), [a, recorded_fixture(self.b)])
        self.assertIn("IMPORTED_RUN_NOT_REVIEWED:fixture-a", result["recommendation"]["blockers"])

    def test_single_candidate_cannot_support_comparative_recommendation(self):
        result = evaluate_runs(*self.reviewed(), [recorded_fixture(self.a)])
        self.assertIn("AT_LEAST_TWO_CANDIDATES_REQUIRED", result["recommendation"]["blockers"])

    def test_same_model_configuration_cannot_be_duplicated_under_another_name(self):
        self.b["model"] = self.a["model"]
        with self.assertRaisesRegex(EvaluationError, "DUPLICATE_MODEL_CONFIGURATION"):
            self.score()

    def test_all_failures_cannot_recommend_a_model(self):
        for run in (self.a, self.b):
            for record in run["records"]:
                record.update(response=None, error="TIMEOUT")
        result = evaluate_runs(*self.reviewed(), [recorded_fixture(self.a), recorded_fixture(self.b)])
        self.assertIn("NO_CORRECT_ANSWERS", result["recommendation"]["blockers"])

    def test_no_implicit_invalid_or_zero_dimension_weights(self):
        for weights in ({"question": 1}, {"question": 0, "matching": 1},
                        {"question": True, "matching": 1}, {"question": float("nan"), "matching": 1}):
            policy = copy.deepcopy(self.policy)
            policy["weights"] = weights
            with self.subTest(weights=weights), self.assertRaises(EvaluationError):
                validate_policy(self.benchmark, policy)

    def test_invalid_gold_and_duplicate_cases_are_rejected_before_collecting(self):
        for change in (lambda b: b["cases"].append(copy.deepcopy(b["cases"][0])),
                       lambda b: b["cases"][0]["question_answers"][0]["criteria"][0].update(question_quote="없는 원문"),
                       lambda b: b.update(data_kind="REAL")):
            benchmark = copy.deepcopy(self.benchmark)
            change(benchmark)
            with self.assertRaises((EvaluationError, LLMError)):
                validate_benchmark(benchmark)

    def test_call_failure_does_not_persist_exception_text_or_retry(self):
        client = FixtureClient(self.benchmark)
        count = 0
        def fail(**_):
            nonlocal count
            count += 1
            raise RuntimeError("PRIVATE TEST MARKER")
        client.complete_json = fail
        run = collect_run(self.benchmark, self.policy, client, candidate_id="fail", generation_config={"fixture": True})
        self.assertEqual(count, 45)
        self.assertNotIn("PRIVATE TEST MARKER", json.dumps(run))
        self.assertTrue(all(r["error"] == "CLIENT_CALL_FAILED" for r in run["records"]))

    def test_evaluation_is_readonly_and_markdown_marks_preview(self):
        before = copy.deepcopy((self.benchmark, self.policy, self.a, self.b))
        result = self.score()
        self.assertEqual(before, (self.benchmark, self.policy, self.a, self.b))
        self.assertIn("PREVIEW_ONLY", report_markdown(result))
        self.assertIn("SIMULATED", report_markdown(result))

    def test_cli_refuses_overwrite_before_reading_inputs(self):
        self.assertEqual(cli(["score", "--benchmark", "missing.json", "--policy", "missing.json",
                              "--run", "missing.json", "--output", str(ROOT / "README.md")]), 2)


if __name__ == "__main__":
    unittest.main()
