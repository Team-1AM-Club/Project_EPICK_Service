"""Contract/security tests with scripted responses; not evidence of LLM quality."""

import copy
import json
from pathlib import Path
import unittest

from epick_w4 import recommend
from epick_w4.contracts import ContractError
from epick_w4.llm_contract import LLMError, parse_json_object
from epick_w4.semantic_matching import MAX_EPISODES

ROOT = Path(__file__).resolve().parents[1]


def sample(name="w4_semantic.json"):
    return json.loads((ROOT / "samples" / name).read_text(encoding="utf-8"))


def question_response():
    return {
        "criteria": [{"criterion_id": "collaboration", "label": "협업·조율",
                      "question_quote": "상반된 입장을 가진 구성원들이 같은 결론에 도달하도록 기여한 사례"}],
        "required_facts": ["ROLE", "ACTION", "RESULT"], "needs_confirmation": False,
    }


def candidate_response(payload):
    # Hand-authored judgments. They test wiring and reference validation only.
    decisions = []
    for episode in payload["candidates"]:
        related = episode["episode_id"] == "episode-common-plan"
        decisions.append({
            "episode_id": episode["episode_id"], "episode_version": episode["episode_version"],
            "relevance": "RELATED" if related else "UNRELATED",
            "fact_checks": [{"fact_id": fact["fact_id"], "usable": True, "issue": None}
                            for fact in episode["facts"]],
            "matches": [{"criterion_id": "collaboration", "fact_ids": [fact["fact_id"]]}
                        for fact in episode["facts"] if related and fact["kind"] == "ACTION"],
        })
    return {"candidates": decisions}


class StubClient:
    provider = "test-stub"
    model = "scripted-response-not-a-model"
    simulated = True

    def __init__(self, question=None, candidates=None, failure=None):
        self.question = question_response() if question is None else question
        self.candidates = candidate_response if candidates is None else candidates
        self.failure = failure
        self.requests = []

    def complete_json(self, *, stage, system_prompt, payload):
        self.requests.append({"stage": stage, "system_prompt": system_prompt, "payload": copy.deepcopy(payload)})
        if self.failure:
            raise self.failure
        response = self.question if stage == "question" else self.candidates(payload)
        return copy.deepcopy(response)


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.payload = sample()
        self.client = StubClient()

    def run_pipeline(self):
        return recommend(self.payload, user_id="user-demo", llm=self.client)

    def test_semantic_contract_can_match_without_baseline_keywords(self):
        self.assertEqual(recommend(self.payload, user_id="user-demo")["status"], "NEEDS_INPUT")
        result = self.run_pipeline()
        self.assertEqual([(c["episode_id"], c["status"]) for c in result["candidates"]],
                         [("episode-common-plan", "DIRECT_MATCH")])
        self.assertEqual([r["stage"] for r in self.client.requests], ["question", "candidates", "candidates"])
        self.assertTrue(all(len(r["payload"]["candidates"]) == 1 for r in self.client.requests[1:]))
        self.assertEqual(result["inference"]["mode"], "SIMULATED_LLM")
        self.assertTrue(any("실제 LLM 호출" in text for text in result["limitations"]))

    def test_no_keyword_prefilter_when_question_uses_known_words(self):
        self.payload["question"]["text"] = "협업 경험에서 역할과 행동 및 결과를 설명해 주세요."
        self.client.question["criteria"][0]["question_quote"] = "협업"
        self.run_pipeline()
        sent_ids = [e["episode_id"] for r in self.client.requests[1:] for e in r["payload"]["candidates"]]
        self.assertIn("episode-common-plan", sent_ids)

    def test_source_quotes_and_question_spans_are_recovered_by_code(self):
        result = self.run_pipeline()
        question = self.payload["question"]["text"]
        evidence = result["question_analysis"]["criteria"][0]["question_evidence"]
        self.assertEqual(question[evidence["start"]:evidence["end"]], evidence["quote"])
        item = result["candidates"][0]
        original = self.payload["episodes"][0]["raw_text"]
        for fact in item["episode_evidence"]:
            self.assertEqual(original[fact["start"]:fact["end"]], fact["exact_quote"])
        self.assertIn(self.payload["episodes"][0]["facts"][1]["text"], item["reasons"][0]["text"])
        self.assertEqual(result["eligibility_assessment"]["status"], "NOT_ASSESSED")

    def test_authentication_precedes_any_adapter_call(self):
        with self.assertRaises(ContractError):
            recommend(self.payload, user_id="someone-else", llm=self.client)
        self.assertEqual(self.client.requests, [])

    def test_foreign_excluded_and_stale_records_do_not_reach_adapter_or_metadata(self):
        foreign = copy.deepcopy(self.payload["episodes"][1])
        foreign.update(episode_id="foreign-secret", owner_id="another-owner", title="PRIVATE MARKER")
        stale = copy.deepcopy(foreign)
        stale.update(episode_id="stale-secret", owner_id="user-demo")
        self.payload["episodes"].extend([foreign, stale])
        self.payload["snapshot"]["episode_versions"].update({"foreign-secret": 1, "stale-secret": 2})
        self.payload["excluded_episode_ids"] = ["episode-solo-fix"]
        result = self.run_pipeline()
        transmitted = json.dumps(self.client.requests, ensure_ascii=False)
        returned = json.dumps(result, ensure_ascii=False)
        for marker in ("foreign-secret", "another-owner", "PRIVATE MARKER", "stale-secret", "episode-solo-fix"):
            self.assertNotIn(marker, transmitted)
            self.assertNotIn(marker, returned)

    def test_corrupted_assertion_is_removed_before_semantic_matching(self):
        fact = self.payload["episodes"][0]["facts"][1]
        fact["text"] = "저는 AWS 운영으로 999건을 해결했습니다."
        result = self.run_pipeline()
        transmitted = json.dumps(self.client.requests[-1]["payload"], ensure_ascii=False)
        self.assertNotIn("999", transmitted)
        self.assertNotIn(fact["fact_id"], transmitted)
        self.assertEqual(result["candidates"][0]["status"], "NEEDS_CONFIRMATION")
        self.assertFalse(result["candidates"][0]["reasons"])

    def test_model_cannot_cite_corrupted_or_cross_episode_fact(self):
        def malformed(payload):
            value = candidate_response(payload)
            value["candidates"][0]["matches"][0]["fact_ids"] = ["episode-solo-fix-fact-2"]
            return value
        self.client.candidates = malformed
        with self.assertRaises(LLMError) as caught:
            self.run_pipeline()
        self.assertEqual(caught.exception.code, "LLM_INVALID_FACT_REFERENCE")

    def test_model_cannot_invent_a_question_quote(self):
        self.client.question["criteria"][0]["question_quote"] = "없는 문항 내용"
        with self.assertRaises(LLMError) as caught:
            self.run_pipeline()
        self.assertEqual(caught.exception.code, "LLM_UNGROUNDED_QUESTION")
        self.assertEqual(len(self.client.requests), 1)

    def test_question_response_shape_is_strict(self):
        for change in (
            lambda q: q.update(needs_confirmation="false"),
            lambda q: q.update(required_facts=["ROLE", "ROLE"]),
            lambda q: q.update(required_facts=["SALARY"]),
            lambda q: q["criteria"].append(copy.deepcopy(q["criteria"][0])),
            lambda q: q["criteria"][0].update(criterion_id="made-up-id"),
            lambda q: q.update(score=100),
        ):
            self.client.question = question_response()
            change(self.client.question)
            with self.subTest(response=self.client.question), self.assertRaises(LLMError):
                self.run_pipeline()

    def test_ambiguous_question_requests_input_without_matching(self):
        self.client.question = {"criteria": [], "required_facts": [], "needs_confirmation": True}
        result = self.run_pipeline()
        self.assertEqual(result["status"], "NEEDS_INPUT")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(len(self.client.requests), 1)

    def test_required_company_missing_stops_before_any_model_call(self):
        self.payload["company_context_policy"] = "REQUIRED"
        result = self.run_pipeline()
        self.assertEqual(result["status"], "NEEDS_INPUT")
        self.assertEqual(result["inference"]["calls"], [])
        self.assertEqual(self.client.requests, [])

    def test_company_diagnostic_is_not_transmitted_to_model(self):
        self.payload["company_knowledge"] = sample("upstream/skhynix_claim_handoff_sample.json")
        result = self.run_pipeline()
        self.assertIn("DIAGNOSTIC_COMPANY_SAMPLE_REJECTED", result["company_context"]["diagnostics"])
        self.assertNotIn("manual_claim_examples", json.dumps(self.client.requests))

    def test_explicit_fact_requirements_cannot_be_removed_by_model(self):
        self.client.question["required_facts"] = []
        result = self.run_pipeline()
        self.assertEqual(result["question_analysis"]["explicit_fact_requirements"], ["ROLE", "ACTION", "RESULT"])
        self.assertEqual(result["question_analysis"]["character_limit"], 700)

    def test_unusable_personal_action_cannot_be_a_match(self):
        def malformed(payload):
            value = candidate_response(payload)
            value["candidates"][0]["fact_checks"][1].update(
                usable=False, issue="PERSONAL_CONTRIBUTION_UNCLEAR")
            return value
        self.client.candidates = malformed
        with self.assertRaises(LLMError) as caught:
            self.run_pipeline()
        self.assertEqual(caught.exception.code, "LLM_INVALID_FACT_REFERENCE")

    def test_uncertain_contribution_is_retained_as_confirmation(self):
        def uncertain(payload):
            value = candidate_response(payload)
            item = value["candidates"][0]
            item["relevance"] = "UNCERTAIN"
            item["matches"] = []
            item["fact_checks"][1].update(usable=False, issue="PERSONAL_CONTRIBUTION_UNCLEAR")
            return value
        self.client.candidates = uncertain
        item = self.run_pipeline()["candidates"][0]
        self.assertEqual(item["status"], "NEEDS_CONFIRMATION")
        self.assertFalse(item["reasons"])
        self.assertNotIn("ACTION", [fact["kind"] for fact in item["episode_evidence"]])

    def test_model_cannot_override_status_or_rank(self):
        for key in ("status", "rank", "score"):
            def add_field(payload):
                value = candidate_response(payload)
                value["candidates"][0][key] = "DIRECT_MATCH"
                return value
            self.client.candidates = add_field
            with self.subTest(key=key), self.assertRaises(LLMError):
                self.run_pipeline()

    def test_candidate_reference_contracts_fail_closed(self):
        changes = [
            (lambda v: v["candidates"].pop(), "LLM_INCOMPLETE_CANDIDATE_SET"),
            (lambda v: v["candidates"].append(copy.deepcopy(v["candidates"][0])), "LLM_INVALID_EPISODE_REFERENCE"),
            (lambda v: v["candidates"][0].update(episode_id="not-in-input"), "LLM_INVALID_EPISODE_REFERENCE"),
            (lambda v: v["candidates"][0].update(episode_version=2), "LLM_INVALID_EPISODE_REFERENCE"),
            (lambda v: v["candidates"][0].update(episode_version=True), "LLM_INVALID_EPISODE_REFERENCE"),
            (lambda v: v["candidates"][0]["fact_checks"].pop(), "LLM_INCOMPLETE_FACT_CHECKS"),
            (lambda v: v["candidates"][0]["matches"][0].update(criterion_id="leadership"), "LLM_INVALID_CRITERION_REFERENCE"),
            (lambda v: v["candidates"][0]["matches"][0].update(fact_ids=["episode-common-plan-fact-1"]), "LLM_INVALID_FACT_REFERENCE"),
        ]
        for change, expected in changes:
            def malformed(payload):
                value = candidate_response(payload)
                change(value)
                return value
            self.client.candidates = malformed
            with self.subTest(expected=expected), self.assertRaises(LLMError) as caught:
                self.run_pipeline()
            self.assertEqual(caught.exception.code, expected)

    def test_large_pool_is_not_silently_truncated_or_sent(self):
        for index in range(MAX_EPISODES):
            episode = copy.deepcopy(self.payload["episodes"][0])
            episode["episode_id"] = f"extra-{index}"
            self.payload["episodes"].append(episode)
            self.payload["snapshot"]["episode_versions"][episode["episode_id"]] = 1
        with self.assertRaises(LLMError) as caught:
            self.run_pipeline()
        self.assertEqual(caught.exception.code, "LLM_CANDIDATE_LIMIT_EXCEEDED")
        self.assertEqual(self.client.requests, [])

    def test_large_question_is_rejected_before_any_model_call(self):
        self.payload["question"]["text"] = "큰 문항" * 2000
        with self.assertRaises(LLMError) as caught:
            self.run_pipeline()
        self.assertEqual(caught.exception.code, "LLM_INPUT_LIMIT_EXCEEDED")
        self.assertEqual(self.client.requests, [])

    def test_real_data_is_not_enabled_in_initial_adapter_integration(self):
        self.payload["data_kind"] = "REAL"
        with self.assertRaises(LLMError) as caught:
            self.run_pipeline()
        self.assertEqual(caught.exception.code, "LLM_REAL_DATA_NOT_ENABLED")
        self.assertEqual(self.client.requests, [])

    def test_provider_failure_is_not_disguised_as_rule_success(self):
        self.client.failure = LLMError("LLM_TIMEOUT", "question")
        with self.assertRaises(LLMError) as caught:
            self.run_pipeline()
        self.assertEqual(str(caught.exception), "LLM_TIMEOUT: question")
        self.assertNotIn(self.payload["question"]["text"], str(caught.exception))

    def test_input_immutable_and_metadata_does_not_store_original_text(self):
        before = copy.deepcopy(self.payload)
        result = self.run_pipeline()
        self.assertEqual(self.payload, before)
        meta = json.dumps(result["inference"], ensure_ascii=False)
        for item in (self.payload["question"]["text"], self.payload["episodes"][0]["raw_text"]):
            self.assertNotIn(item, meta)
        self.assertEqual(len(result["inference"]["calls"]), 3)

    def test_other_known_candidate_id_is_not_valid_for_current_single_call(self):
        def wrong_episode(payload):
            answer = candidate_response(payload)
            answer["candidates"][0]["episode_id"] = "episode-solo-fix"
            return answer
        self.client.candidates = wrong_episode
        with self.assertRaisesRegex(LLMError, "LLM_INVALID_EPISODE_REFERENCE"):
            self.run_pipeline()
        self.assertEqual(len(self.client.requests), 2)

    def test_missing_personal_action_is_confirmation_even_if_model_says_unrelated(self):
        self.payload["episodes"] = self.payload["episodes"][:1]
        self.payload["episodes"][0]["facts"] = [f for f in self.payload["episodes"][0]["facts"] if f["kind"] != "ACTION"]
        def no_action(payload):
            answer = candidate_response(payload)
            answer["candidates"][0].update(relevance="UNRELATED", matches=[])
            return answer
        self.client.candidates = no_action
        result = self.run_pipeline()
        self.assertEqual(result["candidates"][0]["status"], "NEEDS_CONFIRMATION")
        self.assertIn({"code": "MISSING_PERSONAL_FACT", "kind": "ACTION"},
                      result["candidates"][0]["confirmation_items"])


class ResponseParsingTests(unittest.TestCase):
    def test_json_and_single_fence_are_accepted(self):
        for text in ('{"ok":true}', '```json\n{"ok":true}\n```'):
            self.assertEqual(parse_json_object(text, "question"), {"ok": True})

    def test_malformed_ambiguous_or_non_json_response_is_rejected(self):
        for text in ('[]', 'null', '{"a":1,"a":2}', '{"a":NaN}', 'explanation {"a":1}',
                     '{"a":', '{"a":"\\ud800"}', None):
            with self.subTest(text=text), self.assertRaises(LLMError):
                parse_json_object(text, "question")


if __name__ == "__main__":
    unittest.main()
