"""Audit the scorer and fixture boundaries; these tests do not establish LLM accuracy."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from epick_w4.c01_detail import PROMPT, check_sample_c01_detail
from epick_w4.company_detail import outbound_payload
from epick_w4.detail_contract import question_spec
from epick_w4.detailed_recommendation import model_payload
from epick_w4.evidence_extraction import extract_evidence
from epick_w4.extraction_eval import build_extraction_requests
from epick_w4.llm_contract import LLMError
from epick_w4.model_eval import digest
from examples.w4_c01_domain_demo import cases, protocol

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("c01_domain_evaluation", ROOT / "scripts/run-c01-domain-evaluation.py")
EVAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVAL)


class C01DomainEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context, cls.raw, cls.reference, cls.company = protocol()
        cls.gold = json.loads((ROOT / "samples/c01/domain-reference.v1.draft.json").read_text(encoding="utf-8"))
        cls.question = question_spec({"scope_id": cls.context["question_scope_id"], "question_id": "expertise"})

        class ReferenceClient:
            simulated = True
            provider = model = "test-reference-not-real-inference"

            def complete_json(self, *, payload, **_):
                return deepcopy(cls.reference["answers"][payload["episode_id"]])

        cls.extraction = extract_evidence(cls.raw, user_id="user-demo", llm=ReferenceClient())

    def records(self):
        records = []
        for gold in self.gold["cases"]:
            def expected(row, key, value):
                return {key: value, "status": row["statuses"][0], "evidence_ids": row["required_units"][:]}

            response = {"checks": [expected(value, "check_id", key) for key, value in gold["checks"].items()],
                        "company_links": [], "requirement_checks": []}
            for criterion in self.company.criteria:
                if criterion["kind"] == "CLAIM":
                    if "SUPPORTED" in gold["claim"]["statuses"]:
                        response["company_links"].append({"kind": "CLAIM", "id": criterion["id"],
                            "evidence_ids": gold["claim"]["required_units"][:]})
                else:
                    response["requirement_checks"].append({"id": criterion["id"],
                        "nodes": [expected(value, "node_id", key) for key, value in gold["leaves"].items()]})
            records.append({"case_id": gold["case_id"], "response": response, "error": None})
        return records

    def score(self, records):
        return EVAL.score_records(records, self.extraction, self.company, self.question, self.gold)

    def add_claim(self, record, unit="u2"):
        criterion = next(c for c in self.company.criteria if c["kind"] == "CLAIM")
        record["response"]["company_links"].append({"kind": "CLAIM", "id": criterion["id"], "evidence_ids": [unit]})

    def test_reference_scorer_and_source_deduplication(self):
        scored = self.score(self.records())
        self.assertTrue(scored["frozen_case_gate_passed"])
        self.assertTrue(all(value == 100 for value in scored["scores"].values()))
        self.assertEqual(scored["counts"]["claim_link"], [7, 7])
        self.assertEqual(scored["counts"]["leaf_status"], [21, 21])
        self.assertEqual(scored["counts"]["evidence_sufficiency"], [49, 49])

    def test_missing_call_keeps_denominators_and_fails_gate(self):
        scored = self.score(self.records()[:-1])
        self.assertEqual(scored["valid_requests"], 6)
        self.assertEqual(scored["counts"]["claim_link"], [6, 7])
        self.assertFalse(scored["frozen_case_gate_passed"])

    def test_duplicate_and_unknown_records_rejected(self):
        rows = self.records()
        with self.assertRaises(ValueError):
            self.score(rows + [rows[0]])
        rows[0]["case_id"] = "not-in-fixture"
        with self.assertRaises(ValueError):
            self.score(rows)

    def test_wrong_domain_is_a_false_positive_despite_true_learning(self):
        rows = self.records()
        self.add_claim(rows[2])
        scored = self.score(rows)
        self.assertEqual(scored["false_positive_company_support"], 1)
        self.assertEqual(scored["delivered_false_positive_company_support"], 1)
        self.assertEqual(scored["counts"]["question_status"], [21, 21])
        self.assertFalse(scored["frozen_case_gate_passed"])

    def test_generic_documentation_is_valid_without_code_application(self):
        result = self.score(self.records())["details"][2]
        nodes = {d["id"]: d for d in result["details"]}
        self.assertEqual(nodes["documentation"]["expected"], ["SUPPORTED"])
        self.assertEqual(nodes["apply"]["expected"], ["NOT_SHOWN"])
        self.assertEqual(nodes["apply_AND_validation_OR_documentation"]["actual_by_source"], ["NOT_SHOWN", "NOT_SHOWN"])

    def test_rejecting_everything_does_not_pass(self):
        rows = self.records()
        for row in rows:
            row["response"]["company_links"] = []
            for requirement in row["response"]["requirement_checks"]:
                for node in requirement["nodes"]:
                    node.update(status="NOT_SHOWN", evidence_ids=[])
        result = self.score(rows)
        self.assertEqual(result["false_positive_company_support"], 0)
        self.assertFalse(result["frozen_case_gate_passed"])

    def test_colleague_support_is_wrong_even_when_code_guard_catches_it(self):
        rows = self.records()
        self.add_claim(rows[3])
        result = self.score(rows)
        self.assertEqual(result["false_positive_company_support"], 1)
        self.assertEqual(result["delivered_false_positive_company_support"], 0)
        self.assertFalse(result["frozen_case_gate_passed"])

    def test_planned_action_cannot_be_a_supported_claim(self):
        rows = self.records()
        self.add_claim(rows[4])
        result = self.score(rows)
        self.assertEqual(result["false_positive_company_support"], 1)
        self.assertEqual(result["delivered_false_positive_company_support"], 0)

    def test_predeclared_uncertain_plan_status_is_allowed(self):
        rows = self.records()
        for row in rows[4]["response"]["checks"]:
            row.update(status="AMBIGUOUS", evidence_ids=["u2", "u3"])
        for row in rows[4]["response"]["requirement_checks"]:
            for node in row["nodes"]:
                if node["node_id"] != "documentation":
                    node.update(status="AMBIGUOUS", evidence_ids=["u2", "u3"])
        self.assertTrue(self.score(rows)["frozen_case_gate_passed"])

    def test_correct_status_with_incomplete_citation_fails(self):
        rows = self.records()
        rows[0]["response"]["company_links"][0]["evidence_ids"] = ["u3"]
        result = self.score(rows)
        self.assertEqual(result["counts"]["claim_link"], [7, 7])
        self.assertEqual(result["counts"]["evidence_sufficiency"], [48, 49])
        self.assertFalse(result["frozen_case_gate_passed"])

    def test_new_fixture_exact_payloads_allowed_but_arbitrary_edits_blocked(self):
        self.assertEqual(len(build_extraction_requests(self.raw)), 7)
        for item in self.extraction["episodes"]:
            value = outbound_payload(model_payload(self.question, item), self.company)
            check_sample_c01_detail(PROMPT, value)
            value["source_units"][0]["text"] += " 다른 원문"
            with self.assertRaises(LLMError):
                check_sample_c01_detail(PROMPT, value)
        value = outbound_payload(model_payload(self.question, self.extraction["episodes"][0]), self.company)
        value["company_criteria"][0]["statement"] += " 다른 기업 주장"
        with self.assertRaises(LLMError):
            check_sample_c01_detail(PROMPT, value)

    def test_reference_files_match_builder_and_new_source_namespace(self):
        self.assertEqual(self.gold["cases"], cases())
        for name, expected in (("domain-input.synthetic.json", self.raw),
                               ("domain-extraction-reference.draft.json", self.reference),
                               ("domain-server-context.synthetic.json", self.context)):
            self.assertEqual(json.loads((ROOT / "samples/c01" / name).read_text(encoding="utf-8")), expected)
        self.assertTrue(all(s["signal"]["source_id"].startswith("20000000-") for s in self.context["company_knowledge"]["sources"]))

    def test_scorer_does_not_modify_the_shared_extraction(self):
        before = digest(self.extraction)
        self.score(self.records())
        self.assertEqual(digest(self.extraction), before)

    def capture_client(self):
        payload = outbound_payload(model_payload(self.question, self.extraction["episodes"][0]), self.company)
        response = self.records()[0]["response"]
        trace = {"error": None, "stage": "c01_company_details",
                 "request": {"model": "test-captured-model", "messages": [
                     {"role": "system", "content": PROMPT}, {"role": "user", "content": json.dumps(payload)}]},
                 "response": {"model": "test-captured-model", "choices": [
                     {"finish_reason": "stop", "message": {"content": json.dumps(response)}}]}}
        return EVAL.CapturedClient({"provider": "test", "model": "test-captured-model"}, [trace]), payload, response

    def test_capture_recomposition_matches_exact_input_and_does_not_modify_capture(self):
        client, payload, response = self.capture_client()
        result = client.complete_json(stage="c01_company_details", system_prompt=PROMPT, payload=payload)
        self.assertEqual(result, response)
        self.assertTrue(client.replayed)
        result["company_links"] = []
        self.assertEqual(client.records[0]["response"], response)

    def test_capture_recomposition_rejects_different_prompt_payload_and_model(self):
        for change in ("prompt", "payload", "model"):
            client, payload, _ = self.capture_client()
            prompt = PROMPT
            if change == "prompt":
                prompt += "changed"
            elif change == "payload":
                payload["source_units"][0]["text"] += "changed"
            else:
                client.model = "another-model"
            with self.assertRaises(ValueError):
                client.complete_json(stage="c01_company_details", system_prompt=prompt, payload=payload)

    def test_capture_recomposition_rejects_truncated_or_failed_responses(self):
        for failure in ("transport", "truncation"):
            client, payload, _ = self.capture_client()
            if failure == "transport":
                client.calls[0]["error"] = "LLM_NETWORK_ERROR"
            else:
                client.calls[0]["response"]["choices"][0]["finish_reason"] = "length"
            with self.assertRaises(ValueError):
                client.complete_json(stage="c01_company_details", system_prompt=PROMPT, payload=payload)

    def test_resume_can_reuse_a_previously_reused_complete_capture(self):
        client, _, _ = self.capture_client()
        spec = {"candidate_id": "fixture-model", "provider": "test", "model": client.model}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            saved = root / "previous" / spec["candidate_id"]
            saved.mkdir(parents=True)
            EVAL.write(saved / "reused-http-traces.json", client.calls * 7)
            with patch.object(EVAL.RUNNER, "start_server") as start:
                with EVAL.live_model(None, spec, root / "new", None, previous=root / "previous") as reused:
                    self.assertTrue(reused.replayed)
                start.assert_not_called()

    def test_failed_or_interrupted_capture_starts_a_fresh_model_and_preserves_reason(self):
        client, _, _ = self.capture_client()
        spec = {"candidate_id": "fixture-model", "provider": "test", "model": client.model, "sha256": "1" * 64}
        args = SimpleNamespace(runtime="unused", port=18080, fit_target_mib=2048)
        for interrupted in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                saved = root / "previous" / spec["candidate_id"]
                saved.mkdir(parents=True)
                if interrupted:
                    EVAL.write(saved / "response-001.json", {"response": {}})
                else:
                    failed = deepcopy(client.calls[0])
                    failed.update(error="LLM_NETWORK_ERROR", response=None)
                    EVAL.write(saved / "http-traces.json", [failed])
                process, log = Mock(), Mock()
                with patch.object(EVAL.RUNNER, "start_server", return_value=(process, log)) as start, \
                        patch.object(EVAL.RUNNER, "stop_server") as stop:
                    with EVAL.live_model(args, spec, root / "new", {"model_directory": directory},
                                         previous=root / "previous") as fresh:
                        self.assertFalse(getattr(fresh, "replayed", False))
                    start.assert_called_once()
                    stop.assert_called_once_with(process)
                    log.close.assert_called_once()
                self.assertTrue((root / "new/previous-incomplete-attempt.json").is_file())


if __name__ == "__main__":
    unittest.main()
