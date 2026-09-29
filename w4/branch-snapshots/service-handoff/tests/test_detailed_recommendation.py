"""Stage wiring and source-safety regression tests, not LLM quality measurements."""

from copy import deepcopy
import json
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

from epick_w4.contracts import ContractError
from epick_w4.detail_contract import PROMPT, catalog, check_sample_detail, question_spec
from epick_w4.detailed_recommendation import (
    INPUT_SCHEMA, model_payload, prepare_request, recommend_extracted, recommend_from_raw,
)
from epick_w4.evidence_extraction import extract_evidence
from epick_w4.llm_contract import LLMError

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def sample(question="expertise"):
    source = read("samples/extraction/raw-experiences.v4.synthetic.json")
    source["episodes"] = source["episodes"][:3]
    source.update(schema_version=INPUT_SCHEMA, top_k=3,
                  question={"scope_id": catalog()["scope_id"], "question_id": question})
    return source


class ExtractionFixture:
    simulated = True
    provider, model = "fixture", "extraction-fixture"
    def __init__(self):
        self.calls = []
        self.answers = read("samples/extraction/benchmark.v4.draft.json")["answers"]
    def complete_json(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        return deepcopy(self.answers[kwargs["payload"]["episode_id"]])


class JudgmentFixture:
    simulated = True
    provider, model = "fixture", "judgment-fixture"
    def __init__(self):
        self.calls = []
        self.mutate = None
    def complete_json(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        payload = kwargs["payload"]
        qid, eid = payload["question"]["question_id"], payload["episode_id"]
        refs = {}
        if qid == "expertise" and eid == "v4-raw-study":
            refs = {"specific_area": ["u2"], "sustained_learning": ["u2"], "practical_use": ["u2", "u3"]}
        elif qid == "teamwork" and eid == "v4-raw-cooperation":
            refs = {"shared_goal": ["u2"], "cooperation_action": ["u3"], "contribution": ["u2", "u3", "u5"]}
        elif qid == "challenge" and eid == "v4-raw-challenge":
            refs = {"self_set_goal": ["u2"], "persistence": ["u3", "u4"]}
        response = {"checks": [{"check_id": c["id"], "status": "SUPPORTED" if c["id"].split(".")[1] in refs else "NOT_SHOWN",
                               "evidence_ids": refs.get(c["id"].split(".")[1], [])} for c in payload["question"]["content_checks"]]}
        if self.mutate:
            self.mutate(payload, response)
        return response


class DetailedTests(unittest.TestCase):
    def setUp(self):
        self.extractor, self.judge = ExtractionFixture(), JudgmentFixture()
    def call(self, payload=None):
        return recommend_from_raw(payload or sample(), user_id="user-demo", extraction_llm=self.extractor, judgment_llm=self.judge)

    def test_separate_models_and_exact_source_references_reach_ranking(self):
        value = sample()
        result = self.call(value)
        top = result["candidates"][0]
        self.assertEqual((top["episode_id"], top["status"]), ("v4-raw-study", "DIRECT_MATCH"))
        self.assertEqual(result["inference"]["extraction"]["model"], "extraction-fixture")
        self.assertEqual(result["inference"]["judgment"]["model"], "judgment-fixture")
        self.assertFalse(result["subcheck_counts_used_as_ranking_points"])
        self.assertEqual(result["question_scope"]["enforced_form_constraints"], [])
        texts = {e["episode_id"]: e["raw_text"] for e in value["episodes"]}
        for candidate in result["candidates"]:
            for check in candidate["content_checks"]:
                for evidence in check["evidence"]:
                    self.assertEqual(texts[evidence["episode_id"]][evidence["start"]:evidence["end"]], evidence["exact_quote"])

    def test_sustained_learning_requires_period_not_just_action(self):
        self.extractor.answers["v4-raw-study"]["units"][1]["kinds"] = ["ACTION"]
        top = self.call()["candidates"][0]
        check = next(c for c in top["content_checks"] if c["check_id"] == "expertise.sustained_learning")
        self.assertEqual(check["status"], "AMBIGUOUS")
        self.assertEqual(check["validation_issue"], "MISSING_REQUIRED_SOURCE_KINDS")
        self.assertEqual(top["status"], "NEEDS_CONFIRMATION")
        self.assertTrue(check["follow_up_question"])

    def test_goal_shortfall_does_not_exclude_challenge(self):
        top = self.call(sample("challenge"))["candidates"][0]
        self.assertEqual((top["episode_id"], top["status"]), ("v4-raw-challenge", "DIRECT_MATCH"))

    def test_team_action_cannot_be_borrowed_alongside_self_action(self):
        def mutate(payload, result):
            if payload["episode_id"] == "v4-raw-cooperation":
                result["checks"][1]["evidence_ids"] = ["u3", "u4"]
        self.judge.mutate = mutate
        candidate = next(c for c in self.call(sample("teamwork"))["candidates"] if c["episode_id"] == "v4-raw-cooperation")
        self.assertEqual(candidate["content_checks"][1]["status"], "AMBIGUOUS")
        self.assertEqual(candidate["content_checks"][1]["validation_issue"], "SOURCE_STATE_NOT_SUPPORTED")

    def test_future_period_cannot_support_completed_learning(self):
        self.extractor.answers["v4-raw-study"]["units"][1]["assertion"] = "PLANNED"
        candidate = next(c for c in self.call()["candidates"] if c["episode_id"] == "v4-raw-study")
        self.assertTrue(all(c["status"] == "AMBIGUOUS" for c in candidate["content_checks"]))

    def test_invalid_or_duplicate_reference_aborts_not_partial_success(self):
        for duplicate in (False, True):
            def mutate(payload, result):
                if duplicate:
                    result["checks"][1] = deepcopy(result["checks"][0])
                else:
                    result["checks"][0].update(status="SUPPORTED", evidence_ids=["other-episode-u1"])
            self.judge.mutate = mutate
            with self.assertRaises(LLMError):
                self.call()

    def test_missing_user_theme_never_infers_personality_or_calls_model(self):
        result = self.call(sample("self_description"))
        self.assertEqual(result["status"], "NEEDS_INPUT")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(self.extractor.calls, [])
        self.assertEqual(self.judge.calls, [])

    def test_continuous_learning_theme_needs_period_as_well_as_application(self):
        value = sample("self_description")
        value["question"]["user_theme"] = "꾸준히 배우고 적용하는 태도"
        for refs, expected in ((["u3"], "AMBIGUOUS"), (["u2", "u3"], "SUPPORTED")):
            def mutate(payload, result):
                if payload["episode_id"] == "v4-raw-study":
                    result["checks"][0].update(status="SUPPORTED", evidence_ids=refs)
            self.judge.mutate = mutate
            candidate = next(c for c in self.call(value)["candidates"] if c["episode_id"] == "v4-raw-study")
            self.assertEqual(candidate["content_checks"][0]["status"], expected)

    def test_scoping_excludes_foreign_old_and_excluded_before_calls(self):
        value = sample()
        value["episodes"][0]["owner_id"] = "someone-else"
        value["snapshot"]["episode_versions"]["v4-raw-study"] = 2
        value["excluded_episode_ids"] = ["v4-raw-challenge"]
        result = self.call(value)
        self.assertEqual(result["status"], "NEEDS_INPUT")
        self.assertEqual(self.extractor.calls + self.judge.calls, [])
        self.assertEqual(result["snapshot"]["extracted_episode_versions"], {})

    def test_foreign_project_and_real_data_fail_before_calls(self):
        for field, value, error in (("project", {"project_id": "p", "owner_id": "other"}, ContractError),
                                    ("data_kind", "REAL", LLMError)):
            payload = sample()
            payload[field] = value
            with self.assertRaises(error):
                self.call(payload)
        self.assertEqual(self.extractor.calls + self.judge.calls, [])

    def test_persisted_extraction_is_checked_against_raw_before_judgment(self):
        raw, question, top_k = prepare_request(sample(), "user-demo")
        result = extract_evidence(raw, user_id="user-demo", llm=self.extractor)
        result["episodes"][0]["evidence_units"][0]["text"] = "invented"
        with self.assertRaises(ContractError):
            recommend_extracted(raw, result, question=question, top_k=top_k, user_id="user-demo", llm=self.judge)
        self.assertEqual(self.judge.calls, [])

    def test_real_transport_policy_rejects_changed_data_catalog_and_theme(self):
        raw, question, _ = prepare_request(sample(), "user-demo")
        extraction = extract_evidence(raw, user_id="user-demo", llm=self.extractor)
        payload = model_payload(question, extraction["episodes"][0])
        check_sample_detail(PROMPT, payload)
        for field in ("text", "catalog", "theme"):
            bad = deepcopy(payload)
            if field == "text":
                bad["source_units"][0]["text"] += " private"
            elif field == "catalog":
                bad["question"]["content_checks"][0]["description"] = "private"
            else:
                bad["question"] = question_spec({"scope_id": catalog()["scope_id"], "question_id": "self_description", "user_theme": "private"})
            with self.assertRaises(LLMError):
                check_sample_detail(PROMPT, bad)


@unittest.skipUnless(importlib.util.find_spec("fastapi") and importlib.util.find_spec("httpx"), "Install .[api,test] for HTTP tests")
class DetailedApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_host_injects_separate_clients_and_auth_cannot_be_spoofed(self):
        import httpx
        from fastapi import FastAPI
        from epick_w4.api import create_router
        extraction, judgment = ExtractionFixture(), JudgmentFixture()
        ef, jf = Mock(return_value=extraction), Mock(return_value=judgment)
        app = FastAPI()
        app.include_router(create_router(authenticate=lambda: "user-demo", client_factory=jf, extraction_client_factory=ef))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/w4/recommend-from-raw", json=sample())
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["candidates"][0]["episode_id"], "v4-raw-study")
            ef.reset_mock(); jf.reset_mock()
            bad = sample(); bad["project"]["owner_id"] = "foreign"
            self.assertEqual((await client.post("/w4/recommend-from-raw", json=bad)).status_code, 403)
            ef.assert_not_called(); jf.assert_not_called()


if __name__ == "__main__":
    unittest.main()
