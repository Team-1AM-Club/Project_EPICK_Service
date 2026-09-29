import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from epick_w4 import recommend
from epick_w4.candidate_retriever import Candidate
from epick_w4.candidate_validator import CandidateValidator
from epick_w4.contracts import ContractError, Request
from epick_w4.question_analyzer import QuestionAnalyzer

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples"


def load_sample(name="w4_collaboration.json"):
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def candidate(result, episode_id):
    return next(item for item in result["candidates"] if item["episode_id"] == episode_id)


def rewrite_episode(episode):
    """Build a different valid source, not a mutated assertion over the old source."""
    cursor = 0
    episode["raw_text"] = "\n".join(fact["text"] for fact in episode["facts"])
    for fact in episode["facts"]:
        fact["evidence"]["start"] = cursor
        fact["evidence"]["end"] = cursor + len(fact["text"])
        cursor += len(fact["text"]) + 1


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.payload = load_sample()

    def run_pipeline(self):
        return recommend(self.payload, user_id="user-demo")

    def test_collaboration_end_to_end_has_three_distinct_states(self):
        result = self.run_pipeline()
        self.assertEqual([
            (item["episode_id"], item["status"]) for item in result["candidates"]
        ], [
            ("episode-team-api", "DIRECT_MATCH"),
            ("episode-solo-debug", "PARTIAL_MATCH"),
            ("episode-team-unclear", "NEEDS_CONFIRMATION"),
        ])
        self.assertEqual(result["question_analysis"]["character_limit"], 700)
        self.assertEqual(result["eligibility_assessment"]["status"], "NOT_ASSESSED")

    def test_question_change_changes_best_candidate(self):
        result = recommend(load_sample("w4_learning.json"), user_id="user-demo")
        self.assertEqual(result["candidates"][0]["episode_id"], "episode-learning")
        self.assertEqual(result["candidates"][0]["status"], "DIRECT_MATCH")
        self.assertEqual(result["candidates"][0]["company_context"], [])

    def test_every_explanation_reference_roundtrips_to_its_original(self):
        result = self.run_pipeline()
        originals = {item["episode_id"]: item for item in self.payload["episodes"]}
        for item in result["candidates"]:
            admitted = {fact["fact_id"]: fact for fact in item["episode_evidence"]}
            for fact in admitted.values():
                original = originals[fact["episode_id"]]
                self.assertEqual(fact["episode_id"], item["episode_id"])
                self.assertEqual(fact["episode_version"], original["version"])
                self.assertEqual(original["raw_text"][fact["start"]:fact["end"]], fact["exact_quote"])
            for reason in item["reasons"]:
                self.assertTrue(reason["episode_fact_ids"])
                for ref in reason["episode_fact_ids"]:
                    self.assertEqual(admitted[ref]["kind"], "ACTION")
        for criterion in result["question_analysis"]["criteria"]:
            evidence = criterion["question_evidence"]
            self.assertEqual(
                self.payload["question"]["text"][evidence["start"]:evidence["end"]], evidence["quote"],
            )

    def test_foreign_and_excluded_ids_do_not_leak_in_metadata(self):
        self.payload["excluded_episode_ids"] = ["episode-team-api"]
        encoded = json.dumps(self.run_pipeline(), ensure_ascii=False)
        for private in ("episode-foreign-private", "비공개 성과", "user-other", "episode-team-api"):
            self.assertNotIn(private, encoded)

    def test_project_owner_comes_from_authentication(self):
        with self.assertRaises(ContractError) as caught:
            recommend(self.payload, user_id="user-other")
        self.assertEqual(caught.exception.code, "PROJECT_ACCESS_DENIED")

    def test_validator_rechecks_ownership_if_retriever_is_replaced(self):
        request = Request.parse(self.payload)
        foreign = next(item for item in request.episodes if item.owner_id == "user-other")
        result = CandidateValidator().validate(
            Candidate(foreign, ("collaboration",)), request,
            QuestionAnalyzer().analyze(request.question_text), "user-demo",
        )
        self.assertTrue(result.rejected)
        self.assertEqual(result.admitted_facts, ())

    def test_snapshot_version_mismatch_cannot_be_selected(self):
        self.payload["snapshot"]["episode_versions"]["episode-team-api"] = 2
        self.payload["top_k"] = 1
        result = self.run_pipeline()
        self.assertEqual(result["candidates"][0]["episode_id"], "episode-solo-debug")
        self.assertNotIn("episode-team-api", json.dumps(result))

    def test_top_k_is_applied_after_candidate_validation(self):
        # The initially strongest raw story has a false result assertion.
        self.payload["episodes"][0]["facts"][-1]["text"] = "재시도 오류가 999건에서 0건으로 줄었습니다."
        self.payload["top_k"] = 1
        result = self.run_pipeline()
        self.assertEqual(result["candidates"][0]["episode_id"], "episode-solo-debug")

    def test_stale_only_input_requests_snapshot_reconciliation(self):
        self.payload["episodes"] = [self.payload["episodes"][0]]
        self.payload["snapshot"]["episode_versions"]["episode-team-api"] = 2
        result = self.run_pipeline()
        self.assertEqual(result["status"], "NEEDS_INPUT")
        self.assertEqual(result["candidates"], [])
        self.assertTrue(any("스냅샷과 버전" in item for item in result["limitations"]))

    def test_gcp_cannot_be_changed_to_aws_in_candidate_evidence(self):
        fact = self.payload["episodes"][1]["facts"][1]
        fact["text"] = fact["text"].replace("GCP", "AWS")
        result = self.run_pipeline()
        item = candidate(result, "episode-solo-debug")
        self.assertEqual(item["status"], "NEEDS_CONFIRMATION")
        self.assertIn("TECHNOLOGY_DISTORTION", [issue["code"] for issue in item["confirmation_items"]])
        self.assertNotIn("AWS", json.dumps(item))
        self.assertEqual(result["eligibility_assessment"]["status"], "NOT_ASSESSED")

    def test_number_distortion_is_not_quoted_or_recommended_as_fact(self):
        self.payload["episodes"][0]["facts"][-1]["text"] = "재시도 오류가 999건에서 0건으로 줄었습니다."
        item = candidate(self.run_pipeline(), "episode-team-api")
        self.assertEqual(item["status"], "NEEDS_CONFIRMATION")
        self.assertIn("NUMBER_DISTORTION", [issue["code"] for issue in item["confirmation_items"]])
        self.assertNotIn("999", json.dumps(item))

    def test_cannot_combine_a_different_episodes_evidence(self):
        self.payload["episodes"][0]["facts"][1]["evidence"]["episode_id"] = "episode-learning"
        item = candidate(self.run_pipeline(), "episode-team-api")
        self.assertNotIn("collaboration", item["question_fit"]["matched_criterion_ids"])
        self.assertIn("EPISODE_BOUNDARY_MISMATCH", [issue["code"] for issue in item["confirmation_items"]])

    def test_unmatched_quote_and_out_of_range_span_need_confirmation(self):
        for value, expected in (("changed text", "QUOTE_MISMATCH"), (None, "INVALID_EVIDENCE_SPAN")):
            with self.subTest(expected=expected):
                self.payload = load_sample()
                fact = self.payload["episodes"][0]["facts"][1]
                if value:
                    fact["text"] = value
                else:
                    fact["evidence"]["end"] = 100000
                item = candidate(self.run_pipeline(), "episode-team-api")
                self.assertIn(expected, [issue["code"] for issue in item["confirmation_items"]])

    def test_negated_personal_action_is_not_positive_evidence(self):
        episode = self.payload["episodes"][0]
        episode["facts"][1]["text"] = "저는 요구사항을 조율하지 않았습니다."
        rewrite_episode(episode)
        item = candidate(self.run_pipeline(), "episode-team-api")
        self.assertNotIn("collaboration", item["question_fit"]["matched_criterion_ids"])
        self.assertEqual(item["status"], "NEEDS_CONFIRMATION")

    def test_unicode_offsets_use_python_codepoints(self):
        episode = self.payload["episodes"][0]
        episode["facts"][0]["text"] += " 🧪"
        rewrite_episode(episode)
        item = candidate(self.run_pipeline(), "episode-team-api")
        self.assertEqual(item["status"], "DIRECT_MATCH")
        self.assertTrue(item["episode_evidence"][0]["exact_quote"].endswith("🧪"))

    def test_unknown_and_negated_questions_request_input(self):
        for question in ("지원 동기를 설명해 주세요.", "협업이 아닌 개인 경험을 작성해 주세요."):
            with self.subTest(question=question):
                self.payload["question"]["text"] = question
                result = self.run_pipeline()
                self.assertEqual(result["status"], "NEEDS_INPUT")
                self.assertEqual(result["candidates"], [])

    def test_no_relevant_experience_is_not_ineligibility(self):
        self.payload["episodes"] = [self.payload["episodes"][2]]
        result = self.run_pipeline()
        self.assertEqual(result["status"], "NO_CANDIDATES")
        self.assertEqual(result["eligibility_assessment"]["status"], "NOT_ASSESSED")

    def test_empty_experience_list_is_a_valid_no_candidates_result(self):
        self.payload["episodes"] = []
        self.assertEqual(self.run_pipeline()["status"], "NO_CANDIDATES")

    def test_company_claims_cannot_replace_missing_episode_evidence(self):
        self.payload["episodes"] = [self.payload["episodes"][3]]
        item = self.run_pipeline()["candidates"][0]
        self.assertEqual(item["status"], "NEEDS_CONFIRMATION")
        self.assertEqual(item["reasons"], [])
        self.assertEqual(item["company_context"], [])

    def test_upstream_diagnostic_sample_remains_unusable_company_context(self):
        diagnostic = load_sample("upstream/skhynix_claim_handoff_sample.json")
        self.payload["company_knowledge"] = diagnostic
        result = self.run_pipeline()
        self.assertEqual(result["company_context"]["status"], "UNAVAILABLE")
        self.assertIn("DIAGNOSTIC_COMPANY_SAMPLE_REJECTED", result["company_context"]["diagnostics"])
        self.assertEqual(result["candidates"][0]["episode_id"], "episode-team-api")
        self.assertTrue(all(not item["company_context"] for item in result["candidates"]))
        self.assertEqual(len(diagnostic["manual_claim_examples"]), 6)
        self.assertTrue(all(not item["usable_for_matching"] for item in diagnostic["manual_claim_examples"]))
        with self.assertRaises(ContractError) as caught:
            recommend(diagnostic, user_id="user-demo")
        self.assertEqual(caught.exception.code, "DIAGNOSTIC_IS_NOT_RECOMMENDATION_INPUT")

    def test_required_company_context_cannot_silently_become_optional(self):
        self.payload["company_context_policy"] = "REQUIRED"
        self.payload["company_knowledge"] = {}
        result = self.run_pipeline()
        self.assertEqual(result["status"], "NEEDS_INPUT")
        self.assertEqual(result["candidates"], [])

    def test_claim_status_refs_scope_and_dates_are_checked(self):
        mutations = [
            (lambda b: b["claims"][0].update(verification_status="PENDING"), "CLAIM_NOT_VERIFIED_OR_ALLOWED"),
            (lambda b: b["claims"][0].update(usage_status="RESTRICTED"), "CLAIM_NOT_VERIFIED_OR_ALLOWED"),
            (lambda b: b["claims"][0].update(source_version_id="{{W2_SV}}"), "SOURCE_VERSION_NOT_RESOLVED_IN_SNAPSHOT"),
            (lambda b: b["evidence"][0].update(source_version_id="wrong-version"), "EVIDENCE_NOT_RESOLVED_IN_SOURCE_VERSION"),
            (lambda b: b["claims"][0]["scope"].update(job_posting_version_id="old-posting"), "CLAIM_POSTING_VERSION_MISMATCH"),
            (lambda b: b["claims"][0]["scope"].update(job_role="다른 직무"), "CLAIM_JOB_ROLE_MISMATCH"),
            (lambda b: b["claims"][0].update(valid_to="2026-09-02"), "CLAIM_OUTSIDE_VALIDITY"),
            (lambda b: b["claims"][0].update(valid_from="2026-09-09"), "CLAIM_OUTSIDE_VALIDITY"),
            (lambda b: b["claims"][0].update(valid_to="yesterday"), "INVALID_CLAIM_DATE"),
            (lambda b: b["evidence"][0].update(exact_quote=""), "EVIDENCE_NOT_RESOLVED_IN_SOURCE_VERSION"),
        ]
        for mutate, expected in mutations:
            with self.subTest(expected=expected):
                self.payload = load_sample()
                mutate(self.payload["company_knowledge"])
                result = self.run_pipeline()
                self.assertIn(expected, result["company_context"]["diagnostics"])
                self.assertEqual(result["company_context"]["accepted_claim_ids"], [])
                self.assertTrue(all(not item["company_context"] for item in result["candidates"]))

    def test_date_unknown_is_excluded_unless_explicitly_selected(self):
        self.payload["company_knowledge"]["claims"][0]["published_at"] = None
        self.assertIn("UNDATED_CLAIM_EXCLUDED", self.run_pipeline()["company_context"]["diagnostics"])
        self.payload["unknown_date_policy"] = "INCLUDE_WITH_LABEL"
        result = self.run_pipeline()
        self.assertEqual(result["company_context"]["status"], "AVAILABLE")
        self.assertIn("CLAIM_PUBLICATION_DATE_UNKNOWN", result["company_context"]["diagnostics"])
        self.assertIsNone(result["candidates"][0]["company_context"][0]["published_at"])

    def test_duplicate_claims_do_not_change_question_first_order(self):
        before = [item["episode_id"] for item in self.run_pipeline()["candidates"]]
        for index in range(8):
            duplicate = copy.deepcopy(self.payload["company_knowledge"]["claims"][0])
            duplicate["claim_id"] = f"duplicate-claim-{index}"
            self.payload["company_knowledge"]["claims"].append(duplicate)
        after = [item["episode_id"] for item in self.run_pipeline()["candidates"]]
        self.assertEqual(before, after)

    def test_synthetic_context_cannot_be_used_as_real_input(self):
        self.payload["data_kind"] = "REAL"
        result = self.run_pipeline()
        self.assertIn("SYNTHETIC_CONTEXT_IN_REAL_REQUEST", result["company_context"]["diagnostics"])
        self.assertEqual(result["company_context"]["status"], "UNAVAILABLE")

    def test_malformed_contracts_report_codes_not_private_content(self):
        mutations = [
            (lambda p: p.update(top_k=True), "EXPECTED_INTEGER"),
            (lambda p: p.update(data_kind=[]), "INVALID_DATA_KIND"),
            (lambda p: p.update(company_context_policy={}), "INVALID_CONTEXT_POLICY"),
            (lambda p: p["snapshot"].update(question_version=2), "QUESTION_VERSION_MISMATCH"),
            (lambda p: p["snapshot"].update(question_id="another-question"), "QUESTION_ID_MISMATCH"),
            (lambda p: p["snapshot"].update(question_version=True), "EXPECTED_INTEGER"),
            (lambda p: p["episodes"].append(copy.deepcopy(p["episodes"][0])), "DUPLICATE_EPISODE"),
        ]
        for mutate, expected in mutations:
            with self.subTest(expected=expected):
                self.payload = load_sample()
                mutate(self.payload)
                with self.assertRaises(ContractError) as caught:
                    self.run_pipeline()
                self.assertEqual(caught.exception.code, expected)
                self.assertNotIn(self.payload["question"]["text"], str(caught.exception))

    def test_execution_does_not_mutate_input_or_historical_snapshot(self):
        original = copy.deepcopy(self.payload)
        first = self.run_pipeline()
        second = self.run_pipeline()
        self.assertEqual(self.payload, original)
        self.assertEqual(first, second)


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "epick_w4", *args],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=15,
        )

    def test_real_cli_runs_without_api_keys_or_network(self):
        result = self.run_cli("--input", str(SAMPLES / "w4_learning.json"), "--user-id", "user-demo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["candidates"][0]["episode_id"], "episode-learning")

    def test_cli_diagnostic_override_only_removes_company_context(self):
        result = self.run_cli(
            "--input", str(SAMPLES / "w4_collaboration.json"), "--user-id", "user-demo",
            "--knowledge-file", str(SAMPLES / "upstream/skhynix_claim_handoff_sample.json"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["company_context"]["status"], "UNAVAILABLE")
        self.assertEqual(payload["candidates"][0]["status"], "DIRECT_MATCH")

    def test_cli_refuses_to_overwrite_input(self):
        input_file = SAMPLES / "w4_collaboration.json"
        before = input_file.read_bytes()
        result = self.run_cli(
            "--input", str(input_file), "--user-id", "user-demo", "--output", str(input_file),
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(input_file.read_bytes(), before)
        self.assertEqual(json.loads(result.stderr), {"error": "OUTPUT_ALREADY_EXISTS"})


if __name__ == "__main__":
    unittest.main()
