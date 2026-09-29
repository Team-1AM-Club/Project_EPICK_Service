from copy import deepcopy
import tempfile
from pathlib import Path
import unittest

from epick_w4.c01_adapter import consume_c01
from epick_w4.c01_consumer import C01Consumer
from epick_w4.c01_staged import assess, check_sample, requests
from epick_w4.company_detail import outbound_payload
from epick_w4.detail_contract import question_spec
from epick_w4.detailed_recommendation import model_payload
from epick_w4.evidence_extraction import extract_evidence
from epick_w4.llm_contract import LLMError
from epick_w4.service_adapter import ServiceError, execute_service
from examples.w4_c01_demo import C01DemoBackend, C01DemoClient, FixtureTransport, build_context
from examples.w4_c01_fresh_demo import protocol
from examples.w4_service_demo import read


class StagedStub(C01DemoClient):
    generation_config = {"test_fixture_version": "staged-v1"}
    def __init__(self):
        super().__init__("judgment")
        self.stages, self.after_stage = [], None

    def complete_json(self, *, stage, system_prompt, payload):
        if not stage.startswith("c01_") or stage == "c01_company_details":
            return super().complete_json(stage=stage, system_prompt=system_prompt, payload=payload)
        self.stages.append(stage)
        def row(key, value):
            return {key: value, "status": "NOT_SHOWN", "evidence_ids": []}
        if stage == "c01_question":
            result = {"checks": [row("check_id", c["id"]) for c in payload["question"]["content_checks"]]}
        elif stage == "c01_claims":
            result = {"claims": [row("id", c["id"]) for c in payload["company_criteria"]]}
        else:
            result = {"requirement_checks": [{"id": c["id"], "nodes": [row("node_id", n["node_id"])
                for n in c["condition_nodes"] if n["operator"] == "LEAF"]} for c in payload["company_criteria"]]}
        if self.after_stage:
            self.after_stage(stage, result)
        return result


class StagedTests(unittest.TestCase):
    def temporary_directory(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return directory.name

    def setUp(self):
        context, raw, reference = protocol()
        class Extractor:
            simulated = True
            provider = model = "reference-for-test"
            def complete_json(self, *, payload, **kwargs):
                return deepcopy(reference["answers"][payload["episode_id"]])
        self.extraction = extract_evidence(raw, user_id="user-demo", llm=Extractor())
        self.company = consume_c01(context["company_knowledge"], context["question_scope_id"])
        question = question_spec({"scope_id": context["question_scope_id"], "question_id": "expertise"})
        self.payload = outbound_payload(model_payload(question, self.extraction["episodes"][0]), self.company)

    def test_all_stages_accept_registered_fiction_and_reject_changed_text(self):
        for stage, prompt, payload in requests(self.payload):
            check_sample(stage, prompt, payload)
            payload["source_units"][0]["text"] += " changed"
            with self.assertRaises(LLMError):
                check_sample(stage, prompt, payload)

    def test_projection_preserves_condition_tree_but_does_not_transmit_verification(self):
        values = list(requests(self.payload))
        self.assertEqual(values[0][2]["company_criteria"], [])
        projected = values[2][2]["company_criteria"][0]
        original = next(c for c in self.company.criteria if c["kind"] == "REQUIREMENT")
        self.assertEqual(projected["condition_nodes"], original["upstream_record"]["condition_nodes"])
        self.assertNotIn("verification_status", projected)
        self.assertNotIn("company_evidence", projected)

    def test_modified_company_criterion_and_prompt_are_blocked(self):
        stage, prompt, payload = list(requests(self.payload))[1]
        with self.assertRaises(LLMError):
            check_sample(stage, prompt + "change", payload)
        payload["company_criteria"][0]["statement"] += "changed"
        with self.assertRaises(LLMError):
            check_sample(stage, prompt, payload)

    def test_explicit_negative_claims_do_not_become_positive_links(self):
        result, calls = assess(StagedStub(), self.payload)
        self.assertEqual(result["company_links"], [])
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(result["requirement_checks"]), 2)

    def test_claim_cardinality_reference_and_citation_rules(self):
        for failure in ("duplicate", "missing", "foreign", "uncited", "negative_citation"):
            client = StagedStub()
            def change(stage, result):
                if stage != "c01_claims":
                    return
                rows = result["claims"]
                if failure == "duplicate":
                    rows[1] = deepcopy(rows[0])
                elif failure == "missing":
                    rows.pop()
                elif failure == "foreign":
                    rows[0]["evidence_ids"] = ["foreign"]
                elif failure == "uncited":
                    rows[0]["status"] = "SUPPORTED"
                else:
                    rows[0]["evidence_ids"] = ["u2"]
            client.after_stage = change
            with self.subTest(failure=failure), self.assertRaises(LLMError):
                assess(client, self.payload)

    def test_empty_criteria_needs_only_question_call(self):
        self.payload["company_criteria"] = []
        client = StagedStub()
        value, calls = assess(client, self.payload)
        self.assertEqual(len(calls), 1)
        self.assertEqual(value["requirement_checks"], [])

    def service(self, directory, backend, client):
        consumer = C01Consumer(Path(directory) / "c01.sqlite", transport=FixtureTransport(backend.context),
                               max_cache_ttl_seconds=300)
        consumer.drain()
        self.addCleanup(consumer.close)
        def extraction_factory():
            extractor = C01DemoClient("extraction")
            extractor.generation_config = {"test_fixture_version": "extraction-v1"}
            return extractor
        def execute(split=True):
            return execute_service(read("request.json"), user_id="user-demo", backend=backend,
                extraction_factory=extraction_factory, judgment_factory=lambda: client,
                c01_consumer=consumer, c01_split=split)
        return execute

    def test_context_change_between_stages_prevents_next_call(self):
        directory = self.temporary_directory()
        backend, client = C01DemoBackend(build_context()), StagedStub()
        execute = self.service(directory, backend, client)
        def change(stage, _):
            if stage == "c01_question":
                backend.context["context_version"] = "changed"
        client.after_stage = change
        with self.assertRaises(ServiceError) as caught:
            execute()
        self.assertEqual(caught.exception.code, "CONTEXT_CHANGED")
        self.assertEqual(client.stages, ["c01_question"])

    def test_protocol_cache_isolation_and_same_protocol_cache_hit(self):
        directory = self.temporary_directory()
        backend, client = C01DemoBackend(build_context()), StagedStub()
        execute = self.service(directory, backend, client)
        old = execute(False)
        fresh = execute(True)
        self.assertNotEqual(old["inference"]["judgment"]["prompt_version"], fresh["inference"]["judgment"]["prompt_version"])
        self.assertEqual(len(client.stages), 9)
        self.assertEqual(execute(True), fresh)
        self.assertEqual(len(client.stages), 9)

    def test_model_or_settings_change_cannot_reuse_old_staged_result(self):
        directory = self.temporary_directory()
        backend, client = C01DemoBackend(build_context()), StagedStub()
        execute = self.service(directory, backend, client)
        execute()
        client.model = "different-test-model"
        value = execute()
        self.assertEqual(value["inference"]["judgment"]["model"], client.model)
        self.assertEqual(len(client.stages), 18)
        client.generation_config = {"test_fixture_version": "staged-v2"}
        execute()
        self.assertEqual(len(client.stages), 27)

    def test_unknown_generation_settings_disable_staged_cache_reuse(self):
        directory = self.temporary_directory()
        backend, client = C01DemoBackend(build_context()), StagedStub()
        client.generation_config = None
        execute = self.service(directory, backend, client)
        execute()
        execute()
        self.assertEqual(len(client.stages), 18)


if __name__ == "__main__":
    unittest.main()
