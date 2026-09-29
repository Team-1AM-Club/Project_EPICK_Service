"""Tests below mock HTTP; actual model artifacts are produced only by the runner."""

from copy import deepcopy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from epick_w4.extraction_eval import collect_extraction_run, evaluate_extraction_runs
from epick_w4.llm_contract import LLMError
from epick_w4.local_client import LocalClient
from epick_w4.model_eval import (
    EvaluationError, build_requests, collect_run, digest, evaluate_runs,
)
from epick_w4.synthetic_policy import check_sample_payload

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


class TestClient:
    provider = "test-fixture"
    model = "fixture-only"
    simulated = True
    generation_config = {"fixture": True}

    def __init__(self, benchmark):
        self.answers = {}
        cases = {c["case_id"]: c for c in benchmark["cases"]}
        for r in build_requests(benchmark):
            c = cases[r["case_id"]]
            a = c["question_answers"][0] if r["axis"] == "question" else c["matching"]["answers"][0]
            self.answers[digest(r["payload"])] = a

    def complete_json(self, **kwargs):
        return deepcopy(self.answers[digest(kwargs["payload"])])


class LocalEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.b = read("samples/evaluation/benchmark.draft.json")
        self.p = read("samples/evaluation/policy.draft.json")
        self.client = LocalClient(provider="test-local", model="test-model", model_sha256="a" * 64)
        self.request = build_requests(self.b)[0]

    def call(self):
        return self.client.complete_json(**{k: self.request[k] for k in ("stage", "system_prompt", "payload")})

    def response(self, content='{"criteria":[]}', model="test-model", finish="stop"):
        return io.BytesIO(json.dumps({"model": model, "choices": [
            {"finish_reason": finish, "message": {"content": content}}],
            "usage": {"total_tokens": 10}}).encode())

    def test_local_transport_keeps_actual_wire_request_response(self):
        with patch.object(self.client._opener, "open", return_value=self.response()) as opened:
            self.assertEqual(self.call(), {"criteria": []})
        req = opened.call_args.args[0]
        self.assertEqual(req.full_url, "http://127.0.0.1:18080/v1/chat/completions")
        self.assertIsNone(req.get_header("Authorization"))
        self.assertEqual(len(self.client.calls), 1)
        self.assertFalse(self.client.simulated)
        wire = json.loads(req.data)
        self.assertNotIn("question_answers", wire["messages"][1]["content"])
        self.assertEqual(self.client.calls[0]["response"]["usage"]["total_tokens"], 10)

    def test_service_transport_does_not_retain_source_or_response_content(self):
        self.client = LocalClient(provider="test-local", model="test-model", model_sha256="a" * 64,
                                  capture_traces=False)
        with patch.object(self.client._opener, "open", return_value=self.response()) as opened:
            self.assertEqual(self.call(), {"criteria": []})
        self.assertIn("messages", json.loads(opened.call_args.args[0].data))
        self.assertFalse(self.client.content_logging_enabled)
        self.assertIsNone(self.client.calls[-1]["request"])
        self.assertIsNone(self.client.calls[-1]["response"])

    def test_failed_service_transport_does_not_retain_private_provider_text(self):
        self.client = LocalClient(provider="test-local", model="test-model", model_sha256="a" * 64,
                                  capture_traces=False)
        with patch.object(self.client._opener, "open", return_value=self.response(content="PRIVATE_FULL_RESPONSE")):
            with self.assertRaises(LLMError): self.call()
        trace = json.dumps(self.client.calls, ensure_ascii=False)
        self.assertNotIn("PRIVATE_FULL_RESPONSE", trace)
        self.assertNotIn(self.request["payload"]["question_text"], trace)

    def test_wrong_model_and_truncation_are_failures(self):
        for result, code in ((self.response(model="other"), "LLM_LOCAL_MODEL_MISMATCH"),
                             (self.response(finish="length"), "LLM_OUTPUT_TRUNCATED")):
            with patch.object(self.client._opener, "open", return_value=result):
                with self.assertRaisesRegex(LLMError, code):
                    self.call()
            self.assertEqual(self.client.calls[-1]["error"], code)

    def test_invalid_json_keeps_raw_response_for_audit(self):
        with patch.object(self.client._opener, "open", return_value=self.response(content="bad JSON")):
            with self.assertRaisesRegex(LLMError, "LLM_INVALID_JSON"):
                self.call()
        self.assertEqual(self.client.calls[0]["response"]["choices"][0]["message"]["content"], "bad JSON")

    def test_unknown_input_never_reaches_transport(self):
        self.request["payload"]["question_text"] = "new private text"
        with patch.object(self.client._opener, "open") as opened:
            with self.assertRaisesRegex(LLMError, "LLM_SYNTHETIC_SAMPLE_REQUIRED"):
                self.call()
        opened.assert_not_called()

    def test_structured_mode_records_and_sends_stage_schema_without_answers(self):
        self.client.structured = True
        with patch.object(self.client._opener, "open", return_value=self.response()) as opened:
            self.call()
        body = json.loads(opened.call_args.args[0].data)
        formats = self.client.generation_config["stage_response_formats"]
        self.assertEqual(body["response_format"], formats["question"])
        self.assertFalse(body["response_format"]["schema"]["additionalProperties"])
        self.assertNotIn("stage_response_formats", body)
        self.assertNotIn(self.request["payload"]["question_text"], json.dumps(formats, ensure_ascii=False))

    def test_all_fixed_evaluation_requests_allowed(self):
        for request in build_requests(self.b):
            check_sample_payload(**{k: request[k] for k in ("stage", "system_prompt", "payload")})

    def test_actual_draft_capture_requires_explicit_exploratory_mode(self):
        client = TestClient(self.b)
        client.simulated = False  # test-only transport fixture, never saved as actual evidence
        with self.assertRaisesRegex(EvaluationError, "HUMAN_REVIEW_REQUIRED"):
            collect_run(self.b, self.p, client, candidate_id="a", generation_config={"test": True})
        run = collect_run(self.b, self.p, client, candidate_id="a",
                          generation_config={"test": True}, exploratory=True)
        with self.assertRaisesRegex(EvaluationError, "HUMAN_REVIEW_REQUIRED"):
            evaluate_runs(self.b, self.p, [run])
        report = evaluate_runs(self.b, self.p, [run], exploratory=True)
        self.assertEqual(report["recommendation"]["status"], "WITHHELD")
        self.assertIn("EXPLORATORY_EVALUATION", report["recommendation"]["blockers"])
        self.assertEqual(report["results"][0]["scores"], {"question": 100, "matching": 100})
        self.assertEqual(self.b["review"]["status"], "DRAFT")


class ExtractionEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.source = read("samples/extraction/raw-experiences.synthetic.json")
        self.reference = read("samples/extraction/benchmark.draft.json")
        outer = self

        class Fixture:
            provider = "test-fixture"
            model = "extraction-fixture"
            simulated = True
            generation_config = {"test": True}

            def complete_json(self, **kwargs):
                return deepcopy(outer.reference["answers"][kwargs["payload"]["episode_id"]])

        self.run = collect_extraction_run(self.source, self.reference, Fixture(), candidate_id="test")

    def score(self):
        return evaluate_extraction_runs(self.source, self.reference, [self.run])["results"][0]

    def test_exact_and_partial_attribute_scores_are_separate(self):
        self.assertEqual(self.score()["scores"]["unit_exact"], 100)
        self.run["records"][0]["response"]["units"][0]["subject"] = "TEAM"
        row = self.score()
        self.assertEqual(row["scheduled_units"], 57)
        self.assertAlmostEqual(row["scores"]["unit_exact"], 100 * 56 / 57, places=5)
        self.assertEqual(row["scores"]["kinds_micro_f1"], 100)
        self.assertEqual(row["scores"]["assertion"], 100)

    def test_missing_and_invalid_responses_remain_in_denominator(self):
        self.run["records"].pop(0)  # three source units
        self.run["records"][0]["response"] = {"units": []}  # four source units
        row = self.score()
        self.assertEqual(row["valid_requests"], 10)
        self.assertEqual(row["scheduled_requests"], 12)
        self.assertEqual(row["correct"]["unit_exact"], 50)

    def test_mismatched_inputs_or_duplicate_records_rejected(self):
        self.run["records"].append(deepcopy(self.run["records"][0]))
        with self.assertRaisesRegex(EvaluationError, "UNKNOWN_OR_DUPLICATE_RECORD"):
            self.score()
        self.run["records"].pop()
        self.source["episodes"][0]["raw_text"] += " changed"
        with self.assertRaises((EvaluationError, LLMError)):
            self.score()


if __name__ == "__main__":
    unittest.main()
