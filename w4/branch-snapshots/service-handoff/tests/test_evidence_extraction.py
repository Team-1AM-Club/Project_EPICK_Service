"""Raw extraction boundaries and mocked transport. No external calls or real data."""

from copy import deepcopy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from epick_w4 import extract_evidence, recommend
from epick_w4.__main__ import main
from epick_w4.contracts import ContractError, Request
from epick_w4.evidence_extraction import model_payload, source_units
from epick_w4.llm_contract import LLMError
from epick_w4.llm_prompts import EXTRACTION_PROMPT
from epick_w4.solar_client import API_URL, SolarClient
from epick_w4.synthetic_policy import check_sample_payload, content_hash

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = ROOT / "samples/extraction/raw-experiences.synthetic.json"


def extraction_sample():
    return json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))


def labels():
    return json.loads((ROOT / "samples/extraction/model-output.mock.json").read_text(
        encoding="utf-8"))["responses"]


class ExtractionStub:
    simulated = True
    provider = "SIMULATED-FIXTURE"
    model = "source-unit-label-fixture"

    def __init__(self):
        self.responses = labels()
        self.requests = []
        self.failure = None

    def complete_json(self, *, stage, system_prompt, payload):
        self.requests.append({"stage": stage, "system_prompt": system_prompt,
                              "payload": deepcopy(payload)})
        if self.failure:
            raise self.failure
        return deepcopy(self.responses[payload["episode_id"]])


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.value = extraction_sample()
        self.client = ExtractionStub()

    def call(self):
        return extract_evidence(self.value, user_id="user-demo", llm=self.client)

    def only_first(self):
        self.value["episodes"] = self.value["episodes"][:1]

    def test_raw_input_produces_exact_versioned_source_references(self):
        original = deepcopy(self.value)
        result = self.call()
        self.assertEqual(self.value, original)
        self.assertEqual(result["status"], "EXTRACTED_WITH_LIMITATIONS")
        self.assertEqual(result["inference"]["mode"], "SIMULATED_LLM")
        self.assertEqual(len(self.client.requests), 4)
        self.assertEqual(sum(len(e["evidence_units"]) for e in result["episodes"]), 19)
        self.assertEqual(sum(len(e["episode"]["facts"]) for e in result["episodes"]), 12)
        ids = []
        for item in result["episodes"]:
            episode = item["episode"]
            self.assertEqual(item["validation_status"], "REQUIRES_CANDIDATE_VALIDATION")
            for fact in [*episode["facts"], *item["evidence_units"]]:
                ref = fact["evidence"]
                self.assertEqual(ref["episode_id"], episode["episode_id"])
                self.assertEqual(ref["episode_version"], episode["version"])
                self.assertEqual(episode["raw_text"][ref["start"]:ref["end"]], fact["text"])
            ids.extend(fact["fact_id"] for fact in episode["facts"])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotIn("candidates", result)

    def test_outbound_excludes_identity_title_company_and_existing_fact_labels(self):
        self.call()
        for call in self.client.requests:
            self.assertEqual(call["stage"], "extraction")
            self.assertEqual(call["system_prompt"], EXTRACTION_PROMPT)
            self.assertEqual(set(call["payload"]), {"episode_id", "episode_version", "source_units"})
            self.assertTrue(all(set(u) == {"unit_id", "text"} for u in call["payload"]["source_units"]))
            self.assertNotIn("user-demo", json.dumps(call["payload"]))

    def test_missing_period_is_not_inferred_from_learning_action(self):
        item = self.call()["episodes"][0]
        self.assertEqual(item["field_coverage"]["PERIOD"]["status"], "NOT_EXTRACTED")
        self.assertTrue(any(q["field"] == "PERIOD" for q in item["follow_up_questions"]))
        self.assertEqual([f["kind"] for f in item["episode"]["facts"]], ["ROLE", "ACTION", "RESULT"])

    def test_context_fields_remain_separate_and_preserve_team_goal(self):
        item = self.call()["episodes"][-1]
        for kind in ("GOAL", "PERIOD", "OBSTACLE", "REFLECTION", "CONTEXT"):
            self.assertEqual(item["field_coverage"][kind]["status"], "EXTRACTED")
        self.assertTrue(all(f["kind"] in ("ROLE", "ACTION", "RESULT") for f in item["episode"]["facts"]))
        self.assertEqual(item["evidence_units"][2]["subject"], "TEAM")
        self.assertEqual(item["evidence_units"][2]["kinds"], ["GOAL"])
        self.assertEqual(len(item["field_coverage"]["PERIOD"]["evidence_ids"]), 3)

    def test_team_contribution_is_not_promoted_to_personal_role_or_action(self):
        item = self.call()["episodes"][2]
        self.assertEqual([f["kind"] for f in item["episode"]["facts"]], ["RESULT"])
        for kind in ("ROLE", "ACTION"):
            self.assertEqual(item["field_coverage"][kind]["status"], "REVIEW_REQUIRED")
            self.assertEqual(item["field_coverage"][kind]["evidence_ids"], [])
            self.assertEqual(len(item["field_coverage"][kind]["review_evidence_ids"]), 1)

    def test_planned_action_is_preserved_for_review_and_not_exported_as_fact(self):
        item = self.call()["episodes"][-1]
        planned = item["evidence_units"][-1]
        self.assertEqual(planned["assertion"], "PLANNED")
        self.assertIn(planned["evidence_id"], item["field_coverage"]["ACTION"]["review_evidence_ids"])
        self.assertFalse(any(f["text"] == planned["text"] for f in item["episode"]["facts"]))

    def test_negated_hypothetical_and_uncertain_labels_do_not_export_completed_fact(self):
        self.only_first()
        action = self.client.responses["episode-learning"]["units"][1]
        for assertion in ("NEGATED", "HYPOTHETICAL", "UNCERTAIN", "MIXED"):
            action.update(assertion=assertion, issue="INSUFFICIENT_CONTEXT" if assertion in (
                "UNCERTAIN", "MIXED") else None)
            with self.subTest(assertion=assertion):
                item = self.call()["episodes"][0]
                self.assertFalse(any(f["kind"] == "ACTION" for f in item["episode"]["facts"]))
                self.assertEqual(item["field_coverage"]["ACTION"]["status"], "REVIEW_REQUIRED")

    def test_mixed_subject_is_reviewed_even_if_one_speaker_is_self(self):
        self.only_first()
        self.client.responses["episode-learning"]["units"][1].update(
            subject="MIXED", issue="INSUFFICIENT_CONTEXT")
        item = self.call()["episodes"][0]
        self.assertEqual(item["field_coverage"]["ACTION"]["status"], "REVIEW_REQUIRED")

    def test_instruction_flagged_by_model_never_becomes_fact(self):
        self.only_first()
        self.client.responses["episode-learning"]["units"][1].update(
            kinds=[], issue="INSTRUCTION_IN_SOURCE")
        item = self.call()["episodes"][0]
        self.assertEqual(item["field_coverage"]["ACTION"]["status"], "NOT_EXTRACTED")
        self.assertEqual(item["evidence_units"][1]["issue"], "INSTRUCTION_IN_SOURCE")

    def test_response_order_cannot_change_source_order_ids_or_facts(self):
        first = self.call()
        for response in self.client.responses.values():
            response["units"].reverse()
            for unit in response["units"]:
                unit["kinds"].reverse()
        self.assertEqual(self.call(), first)

    def test_unicode_crlf_whitespace_and_duplicate_lines_keep_exact_positions(self):
        self.only_first()
        raw = "  저는 🧪 테스트를 했습니다.  \r\n\r\n  저는 🧪 테스트를 했습니다.  \n끝"
        self.value["episodes"][0]["raw_text"] = raw
        units = self.call()["episodes"][0]["evidence_units"]
        self.assertEqual(len(units), 3)
        self.assertEqual(units[0]["text"], units[1]["text"])
        self.assertNotEqual(units[0]["evidence_id"], units[1]["evidence_id"])
        self.assertEqual(units[1]["evidence"]["start"], raw.index("  저는", 1))
        for unit in units:
            ref = unit["evidence"]
            self.assertEqual(raw[ref["start"]:ref["end"]], unit["text"])
        self.assertEqual(len(source_units("저는 행동했습니다. 하지만 결과는 아직 없습니다.")), 1)

    def test_foreign_excluded_and_stale_episodes_are_absent_from_calls_and_output(self):
        first, second, third, _ = self.value["episodes"]
        first["owner_id"] = "other-user"
        first["raw_text"] = "PRIVATE_FOREIGN"
        self.value["excluded_episode_ids"] = [second["episode_id"]]
        second["raw_text"] = "PRIVATE_EXCLUDED"
        self.value["snapshot"]["episode_versions"][third["episode_id"]] = 2
        third["raw_text"] = "PRIVATE_STALE"
        result = self.call()
        self.assertEqual(len(result["episodes"]), 1)
        self.assertEqual(len(self.client.requests), 1)
        self.assertEqual(result["diagnostics"], [{"code": "EPISODE_VERSION_MISMATCH"}])
        self.assertEqual(result["snapshot"]["extracted_episode_versions"], {"episode-library-reservation": 1})
        serialized = json.dumps([result, self.client.requests])
        self.assertNotIn("PRIVATE", serialized)
        for episode in (first, second, third):
            self.assertNotIn(episode["episode_id"], serialized)

    def test_no_eligible_episode_makes_no_model_call(self):
        self.value["excluded_episode_ids"] = [e["episode_id"] for e in self.value["episodes"]]
        result = self.call()
        self.assertEqual(result["status"], "NO_ELIGIBLE_EPISODES")
        self.assertEqual(result["episodes"], [])
        self.assertEqual(self.client.requests, [])

    def test_authenticated_identity_is_required_before_model_work(self):
        for user, code in (("other-user", "PROJECT_ACCESS_DENIED"), ("", "EXPECTED_NONEMPTY_STRING")):
            with self.subTest(user=user), self.assertRaises(ContractError) as caught:
                extract_evidence(self.value, user_id=user, llm=self.client)
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.client.requests, [])

    def test_real_data_is_blocked_even_with_simulated_client(self):
        self.value["data_kind"] = "REAL"
        with self.assertRaises(LLMError) as caught:
            self.call()
        self.assertEqual(caught.exception.code, "LLM_REAL_DATA_NOT_ENABLED")
        self.assertEqual(self.client.requests, [])

    def test_invalid_input_is_rejected_before_any_call_and_redacted(self):
        changes = (
            lambda p: p.update(schema_version="PRIVATE"),
            lambda p: p.update(user_id="PRIVATE"),
            lambda p: p["episodes"][1].update(facts=[]),
            lambda p: p["episodes"][1].update(version=True),
            lambda p: p["episodes"][1].update(raw_text=" \r\n"),
            lambda p: p["episodes"].append(deepcopy(p["episodes"][0])),
            lambda p: p["snapshot"]["episode_versions"].update({"episode-learning": False}),
            lambda p: p.update(excluded_episode_ids=["x", "x"]),
            lambda p: p.update(request_id="PRIVATE\ud800"),
            lambda p: p["episodes"][1].update(raw_text="PRIVATE\ud800"),
        )
        for index, change in enumerate(changes):
            self.value = extraction_sample()
            change(self.value)
            with self.subTest(index=index), self.assertRaises(ContractError) as caught:
                self.call()
            self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertEqual(self.client.requests, [])

    def test_all_input_limits_are_checked_before_first_call_without_truncation(self):
        for raw in ("가" * 3001, "\n".join(["가"] * 65), "\n".join(["가" * 2500] * 5)):
            self.value = extraction_sample()
            self.value["episodes"][-1]["raw_text"] = raw
            with self.subTest(size=len(raw)), self.assertRaises(LLMError) as caught:
                self.call()
            self.assertEqual(caught.exception.code, "LLM_INPUT_LIMIT_EXCEEDED")
        self.assertEqual(self.client.requests, [])

    def test_episode_count_and_combined_text_limits(self):
        for count, raw, code in ((21, "가", "LLM_CANDIDATE_LIMIT_EXCEEDED"),
                                  (6, "\n".join(["가" * 2000] * 5), "LLM_INPUT_LIMIT_EXCEEDED")):
            base = extraction_sample()["episodes"][0]
            self.value["episodes"] = [{**base, "episode_id": f"limit-{i}", "raw_text": raw} for i in range(count)]
            self.value["snapshot"]["episode_versions"] = {e["episode_id"]: 1 for e in self.value["episodes"]}
            with self.subTest(count=count), self.assertRaises(LLMError) as caught:
                self.call()
            self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.client.requests, [])

    def test_unknown_duplicate_or_omitted_unit_references_fail_whole_result(self):
        self.only_first()
        changes = (
            lambda r: r["units"][0].update(unit_id="other-episode:PRIVATE"),
            lambda r: r["units"][0].update(unit_id="u2"),
            lambda r: r["units"].pop(),
            lambda r: r["units"].append(deepcopy(r["units"][0])),
        )
        for change in changes:
            response = labels()["episode-learning"]
            change(response)
            self.client.responses["episode-learning"] = response
            with self.assertRaises(LLMError) as caught:
                self.call()
            self.assertNotIn("PRIVATE", str(caught.exception))

    def test_schema_injection_invalid_types_and_inconsistent_decisions_are_rejected(self):
        self.only_first()
        changes = [
            {"text": "PRIVATE invented text"}, {"start": 0}, {"rank": 1},
            {"kinds": ["SCORE"]}, {"kinds": ["ACTION", "ACTION"]}, {"kinds": {}},
            {"kinds": [True]}, {"kinds": [[]]}, {"kinds": []},
            {"subject": "EVERYONE"}, {"subject": []}, {"assertion": True}, {"issue": {}},
            {"subject": "MIXED"}, {"assertion": "UNCERTAIN"},
            {"issue": "INSTRUCTION_IN_SOURCE"}, {"issue": "NO_EVIDENCE"},
        ]
        for change in changes:
            response = labels()["episode-learning"]
            response["units"][0].update(change)
            self.client.responses["episode-learning"] = response
            with self.subTest(change=change), self.assertRaises(LLMError) as caught:
                self.call()
            self.assertNotIn("PRIVATE", str(caught.exception))
        for response in (None, [], {"units": None}, {"units": [None] * 3}, {"units": [], "extra": True}):
            self.client.responses["episode-learning"] = response
            with self.assertRaises(LLMError):
                self.call()

    def test_provider_failure_aborts_without_retry_or_partial_success(self):
        self.client.failure = LLMError("LLM_TIMEOUT", "extraction")
        with self.assertRaises(LLMError) as caught:
            self.call()
        self.assertEqual(caught.exception.code, "LLM_TIMEOUT")
        self.assertEqual(len(self.client.requests), 1)

    def test_new_facts_fit_existing_contract_and_grounding_validator(self):
        result = self.call()
        value = json.loads((ROOT / "samples/w4_collaboration.json").read_text(encoding="utf-8"))
        value["episodes"] = [e["episode"] for e in result["episodes"]]
        value["snapshot"]["episode_versions"] = result["snapshot"]["extracted_episode_versions"]
        parsed = Request.parse(value)
        for episode in parsed.episodes:
            for fact in episode.facts:
                self.assertEqual(episode.raw_text[fact.start:fact.end], fact.text)
        # This is the existing limited rules validator, not an actual LLM quality check.
        recommendation = recommend(value, user_id="user-demo")
        team = next(c for c in recommendation["candidates"] if c["episode_id"] == "episode-team-api")
        self.assertEqual(team["status"], "DIRECT_MATCH")

    def test_allowlist_is_exactly_the_registered_fictional_outbound_payloads(self):
        allowed = json.loads((ROOT / "epick_w4/extraction_allowlist.json").read_text(encoding="utf-8"))
        expected = set()
        for version in ("v3", "v4"):
            fixed = json.loads((ROOT / f"samples/extraction/raw-experiences.{version}.synthetic.json").read_text(encoding="utf-8"))
            expected.update(content_hash(model_payload(e, source_units(e["raw_text"]))) for e in fixed["episodes"])
        self.assertEqual(allowed, {"payload_sha256": sorted(expected)})
        for episode in self.value["episodes"]:
            check_sample_payload("extraction", EXTRACTION_PROMPT, model_payload(episode, source_units(episode["raw_text"])))

    def test_modified_later_episode_is_blocked_before_any_transport_call(self):
        self.client = SolarClient("synthetic-test-key-not-a-credential")
        self.client._opener = Mock()
        self.value["episodes"][-1]["raw_text"] += "\nPRIVATE unapproved input"
        with self.assertRaises(LLMError) as caught:
            self.call()
        self.assertEqual(caught.exception.code, "LLM_SYNTHETIC_SAMPLE_REQUIRED")
        self.client._opener.open.assert_not_called()

    def test_extraction_uses_existing_solar_transport_with_mocked_http(self):
        self.client = SolarClient("synthetic-test-key-not-a-credential")
        self.client._opener = Mock()
        def response(request, **_kwargs):
            self.assertEqual(request.full_url, API_URL)
            body = json.loads(request.data)
            self.assertEqual(body["messages"][0]["content"], EXTRACTION_PROMPT)
            payload = json.loads(body["messages"][1]["content"])
            answer = labels()[payload["episode_id"]]
            return io.BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps(answer, ensure_ascii=False)}}]}, ensure_ascii=False).encode("utf-8"))
        self.client._opener.open.side_effect = response
        result = self.call()
        self.assertEqual(result["inference"]["mode"], "LLM")
        self.assertEqual(self.client._opener.open.call_count, 4)
        self.assertEqual(result["episodes"][2]["field_coverage"]["ACTION"]["status"], "REVIEW_REQUIRED")

    def test_transport_rejects_extra_outbound_text_or_modified_prompt(self):
        client = SolarClient("synthetic-test-key-not-a-credential")
        client._opener = Mock()
        episode = self.value["episodes"][0]
        payload = model_payload(episode, source_units(episode["raw_text"]))
        for prompt, value in ((EXTRACTION_PROMPT + " PRIVATE", payload),
                               (EXTRACTION_PROMPT, {**payload, "extra": "PRIVATE"})):
            with self.assertRaises(LLMError):
                client.complete_json(stage="extraction", system_prompt=prompt, payload=value)
        client._opener.open.assert_not_called()

    def test_cli_requires_explicit_solar_and_refuses_existing_output_first(self):
        base = ["--input", str(SAMPLE_PATH), "--user-id", "user-demo", "--operation", "extract"]
        cases = (([], "EXTRACTION_REQUIRES_LLM_WITHOUT_KNOWLEDGE_FILE"),
                 (["--engine", "solar", "--knowledge-file", "unused"], "EXTRACTION_REQUIRES_LLM_WITHOUT_KNOWLEDGE_FILE"),
                 (["--engine", "solar", "--output", str(ROOT / "README.md")], "OUTPUT_ALREADY_EXISTS"))
        for args, code in cases:
            with patch("epick_w4.solar_client.SolarClient.from_env") as factory, \
                    patch("sys.stderr", new_callable=io.StringIO) as stderr:
                self.assertEqual(main([*base, *args]), 2)
                self.assertEqual(json.loads(stderr.getvalue())["error"], code)
                factory.assert_not_called()

    def test_cli_missing_key_returns_safe_error(self):
        with patch.dict("os.environ", {}, clear=True), \
                patch("epick_w4.solar_client.build_opener") as opener, \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(main(["--input", str(SAMPLE_PATH), "--user-id", "user-demo",
                                   "--operation", "extract", "--engine", "solar"]), 2)
            self.assertEqual(json.loads(stderr.getvalue()), {
                "error": "LLM_API_KEY_MISSING", "stage": "configuration"})
            opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
