"""HTTP integration/security contracts with fixtures, not model quality scoring."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
import logging
from pathlib import Path
import unittest
from unittest.mock import Mock

import httpx
from fastapi import FastAPI

from epick_w4.api import create_service_router
from epick_w4.company_context import consume_company, project_diagnostic_handoff
from epick_w4.company_detail import COMPANY_PROMPT, check_sample_company_detail
from epick_w4.contracts import ContractError
from epick_w4.detailed_recommendation import model_payload, prepare_request
from epick_w4.evidence_extraction import extract_evidence
from epick_w4.handoff_contract import KnowledgeBundle, ServerContext, ServiceOutput, ServiceRequest
from epick_w4.llm_contract import LLMError
from examples.w4_service_demo import DemoBackend, DemoClient, read

ROOT = Path(__file__).resolve().parents[1]


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.backend, self.extractor, self.judge = DemoBackend(), DemoClient("extraction"), DemoClient("judgment")
        self.user = "user-demo"
        self.ef, self.jf = Mock(return_value=self.extractor), Mock(return_value=self.judge)
        app = FastAPI()
        app.include_router(create_service_router(authenticate=lambda: self.user, backend=self.backend,
                           client_factory=self.jf, extraction_client_factory=self.ef))
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture")

    async def asyncTearDown(self):
        await self.http.aclose()

    async def post(self, payload=None):
        return await self.http.post("/w4/recommend-from-raw", json=read("request.json") if payload is None else payload)

    def no_calls(self):
        self.assertEqual(self.extractor.calls + self.judge.calls, [])

    async def test_server_data_company_ids_and_both_kinds_of_exact_evidence(self):
        response = await self.post()
        self.assertEqual(response.status_code, 200, response.text)
        result = ServiceOutput.model_validate(response.json()).model_dump()
        self.assertNotIn("status", result)
        self.assertEqual(result["processing_status"], "COMPLETED_WITH_LIMITATIONS")
        self.assertEqual(result["company_context"]["status"], "AVAILABLE")
        self.assertEqual(result["eligibility_assessment"], {"status": "NOT_ASSESSED"})
        top = result["candidates"][0]
        self.assertEqual(top["episode_id"], "v4-raw-study")
        self.assertEqual(top["used_company_refs"]["claim_ids"], ["synthetic-learning-claim"])
        self.assertEqual(top["used_company_refs"]["requirement_ids"], ["synthetic-application-preference"])
        original = {e["episode_id"]: e["raw_text"] for e in self.backend.context["episodes"]}
        company = {e["evidence_id"]: e for e in self.backend.context["company_knowledge"]["evidence"]}
        for c in result["candidates"]:
            for support in c["company_support"]:
                for e in support["episode_evidence"]:
                    self.assertEqual(original[c["episode_id"]][e["start"]:e["end"]], e["exact_quote"])
                for e in support["company_evidence"]:
                    self.assertEqual(e, company[e["evidence_id"]])
        self.assertEqual(len(self.extractor.calls), 3)
        self.assertEqual(len(self.judge.calls), 3)
        sends = [c for c in self.backend.checks if c["action"] == "SEND_TO_PROVIDER"]
        self.assertEqual(len(sends), 6)
        self.assertTrue(all(c["provider"] == "simulated-fixture" for c in sends))
        self.assertEqual(self.backend.checks[-1]["action"], "RETURN_TO_CALLER")

    async def test_owner_episode_snapshot_provider_and_data_kind_cannot_be_supplied(self):
        for field in ("owner_id", "project", "episodes", "snapshot", "company_knowledge", "data_kind", "provider", "model", "api_key", "base_url"):
            with self.subTest(field=field):
                value = read("request.json"); value[field] = "PRIVATE_CLIENT_VALUE"
                response = await self.post(value)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("PRIVATE_CLIENT_VALUE", response.text)
        self.no_calls(); self.ef.assert_not_called(); self.jf.assert_not_called()

    async def test_unauthenticated_foreign_and_missing_project(self):
        self.user = None
        self.assertEqual((await self.post()).status_code, 401)
        self.user = "other-user"
        foreign = await self.post()
        self.user = "user-demo"
        value = read("request.json"); value["project_id"] = "absent"
        missing = await self.post(value)
        self.assertEqual((foreign.status_code, foreign.json()), (404, missing.json()))
        self.no_calls(); self.ef.assert_not_called()

    async def test_foreign_episode_and_stale_server_snapshot_fail_before_model(self):
        original = deepcopy(self.backend.context)
        for change in ("foreign", "stale", "duplicate", "excluded"):
            self.backend.context = deepcopy(original)
            ep = self.backend.context["episodes"][0]
            if change == "foreign": ep["owner_id"] = "other-user"
            elif change == "stale": ep["version"] += 1
            elif change == "duplicate": self.backend.context["episodes"].append(deepcopy(ep))
            else: self.backend.context["excluded_episode_ids"].append(ep["episode_id"])
            response = await self.post()
            self.assertEqual(response.status_code, 503, response.text)
        self.no_calls()

    async def test_question_scope_is_bound_to_server_project(self):
        request = read("request.json"); request["question"]["scope_id"] = "different-job"
        self.assertEqual((await self.post(request)).status_code, 422)
        self.no_calls()

    async def test_process_denial_precedes_factory_creation(self):
        self.backend.allowed["PROCESS"] = False
        self.assertEqual((await self.post()).status_code, 403)
        self.ef.assert_not_called(); self.jf.assert_not_called(); self.no_calls()

    async def test_send_denial_precedes_first_model_call(self):
        self.backend.allowed["SEND_TO_PROVIDER"] = False
        self.assertEqual((await self.post()).status_code, 403)
        self.no_calls()

    async def test_send_withdrawal_stops_next_episode(self):
        self.extractor.after_call = lambda: self.backend.allowed.update(SEND_TO_PROVIDER=False)
        self.assertEqual((await self.post()).status_code, 403)
        self.assertEqual(len(self.extractor.calls), 1)
        self.assertEqual(self.judge.calls, [])

    async def test_return_denial_does_not_disclose_computed_result(self):
        self.judge.after_call = lambda: self.backend.allowed.update(RETURN_TO_CALLER=False)
        response = await self.post()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "POLICY_DENIED"})
        self.assertEqual(len(self.judge.calls), 3)

    async def test_process_withdrawal_after_final_call_blocks_return(self):
        def withdraw():
            if len(self.judge.calls) == 3: self.backend.allowed["PROCESS"] = False
        self.judge.after_call = withdraw
        self.assertEqual((await self.post()).status_code, 403)
        self.assertEqual(len(self.judge.calls), 3)

    async def test_deletion_exclusion_edit_and_w3_withdrawal_invalidate_snapshot(self):
        original = deepcopy(self.backend.context)
        for change in ("deletion", "exclusion", "edit", "w3"):
            with self.subTest(change=change):
                self.backend.context = deepcopy(original)
                self.extractor.calls.clear(); self.judge.calls.clear()
                def mutate():
                    c = self.backend.context
                    if change in ("deletion", "exclusion"):
                        removed = c["episodes"].pop()
                        c["snapshot"]["episode_versions"].pop(removed["episode_id"])
                        if change == "exclusion": c["excluded_episode_ids"].append(removed["episode_id"])
                    elif change == "edit": c["episodes"][0]["raw_text"] += " changed without revision bump"
                    else: c["company_knowledge"]["claims"][0]["usage_status"] = "WITHDRAWN"
                self.extractor.after_call = mutate
                response = await self.post()
                self.assertEqual((response.status_code, response.json()), (409, {"error": "CONTEXT_CHANGED"}))
                self.assertEqual(len(self.extractor.calls), 1)
                self.assertEqual(self.judge.calls, [])

    async def test_access_revoked_after_model_call(self):
        self.extractor.after_call = lambda: setattr(self.backend, "accessible", False)
        self.assertEqual((await self.post()).status_code, 404)
        self.assertEqual(len(self.extractor.calls), 1)

    async def test_context_change_after_last_model_call_blocks_return(self):
        def mutate():
            if len(self.judge.calls) == 3: self.backend.context["context_version"] = "changed"
        self.judge.after_call = mutate
        self.assertEqual((await self.post()).status_code, 409)
        self.assertEqual(len(self.judge.calls), 3)

    async def test_real_source_or_real_company_is_never_enabled_by_policy(self):
        for location in (self.backend.context, self.backend.context["company_knowledge"]):
            location["data_kind"] = "REAL"
            response = await self.post()
            self.assertEqual((response.status_code, response.json()), (403, {"error": "LLM_REAL_DATA_NOT_ENABLED"}))
            location["data_kind"] = "SYNTHETIC"
        self.no_calls(); self.ef.assert_not_called()

    async def test_diagnostic_limits_preserved_and_manual_examples_never_used(self):
        self.backend.context = DemoBackend(diagnostic=True).context
        response = await self.post()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["company_context"]["status"], "UNAVAILABLE")
        self.assertIn("DIAGNOSTIC_SAMPLE_REJECTED", result["company_context"]["diagnostics"])
        codes = {c for r in result["company_context"]["source_reviews"] for c in r["codes"]}
        self.assertTrue({"COMPANY_PUBLICATION_DATE_UNKNOWN", "ATTACHMENT_UNPARSED"} <= codes)
        self.assertTrue(all(not c["company_support"] for c in result["candidates"]))
        self.assertNotIn("{{W2_", response.text)
        self.assertNotIn("draft-claim-", response.text)

    async def test_job_connection_cannot_borrow_old_catalog_when_company_missing(self):
        self.backend.context = DemoBackend(diagnostic=True).context
        value = read("request.json"); value["question"]["question_id"] = "job_experience"
        response = await self.post(value)
        self.assertEqual(response.status_code, 200, response.text)
        for c in response.json()["candidates"]:
            self.assertEqual(c["content_checks"][0]["status"], "NOT_SHOWN")
            self.assertEqual(c["status"], "NEEDS_CONFIRMATION")

    async def test_unknown_company_ids_or_episode_units_fail_closed(self):
        for kind in ("id", "unit", "duplicate"):
            def mutate(payload, response):
                if response["company_links"]:
                    if kind == "id": response["company_links"][0]["id"] = "another-company"
                    elif kind == "unit": response["company_links"][0]["evidence_ids"] = ["foreign-u100"]
                    else: response["company_links"][1] = deepcopy(response["company_links"][0])
            self.judge.mutate_response = mutate
            response = await self.post()
            self.assertEqual((response.status_code, response.json()), (502, {"error": "MODEL_FAILURE"}))

    async def test_result_only_cannot_be_promoted_to_company_support(self):
        def mutate(payload, response):
            for link in response["company_links"]: link["evidence_ids"] = ["u4"]
        self.judge.mutate_response = mutate
        response = await self.post()
        self.assertEqual(response.status_code, 200, response.text)
        top = response.json()["candidates"][0]
        self.assertTrue(all(c["status"] == "AMBIGUOUS" for c in top["company_support"]))
        self.assertEqual(top["used_company_refs"]["claim_ids"], [])
        self.assertEqual(top["used_company_refs"]["requirement_ids"], [])

    async def test_job_supported_without_company_link_is_downgraded(self):
        self.judge.mutate_response = lambda p, r: r.update(company_links=[])
        request = read("request.json"); request["question"]["question_id"] = "job_experience"
        response = await self.post(request)
        study = next(c for c in response.json()["candidates"] if c["episode_id"] == "v4-raw-study")
        self.assertEqual(study["content_checks"][0]["validation_issue"], "COMPANY_CONNECTION_NOT_SUPPORTED")

    async def test_missing_theme_still_checks_return_permission_without_calling_model(self):
        request = read("request.json"); request["question"]["question_id"] = "self_description"
        response = await self.post(request)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["processing_status"], "NEEDS_INPUT")
        self.assertEqual(self.backend.checks[-1]["action"], "RETURN_TO_CALLER")
        self.no_calls()

    async def test_content_logging_client_is_rejected(self):
        self.extractor.content_logging_enabled = True
        response = await self.post()
        self.assertEqual((response.status_code, response.json()), (503, {"error": "MODEL_CONTENT_LOGGING_NOT_DISABLED"}))
        self.no_calls()

    async def test_provider_and_backend_exception_text_is_not_logged_or_returned(self):
        secret = "PRIVATE_RAW_TEXT_AND_FULL_MODEL_RESPONSE"
        stream = io.StringIO(); handler = logging.StreamHandler(stream)
        logger = logging.getLogger(); logger.addHandler(handler)
        try:
            for source in ("model", "policy", "load"):
                with self.subTest(source=source), redirect_stderr(stream), redirect_stdout(stream):
                    if source == "model":
                        self.judge.complete_json = Mock(side_effect=LLMError(secret, secret))
                    elif source == "policy":
                        self.backend.authorize = Mock(side_effect=RuntimeError(secret))
                    else:
                        self.backend.load_context = Mock(side_effect=RuntimeError(secret))
                    response = await self.post()
                    self.assertGreaterEqual(response.status_code, 500)
                    self.assertNotIn(secret, response.text)
            self.assertNotIn(secret, stream.getvalue())
        finally:
            logger.removeHandler(handler)

    async def test_transport_cannot_send_unregistered_company_before_extraction(self):
        self.judge.simulated = False
        self.backend.context["company_knowledge"]["claims"][0]["statement"] = "unregistered personal information"
        response = await self.post()
        self.assertEqual((response.status_code, response.json()), (403, {"error": "LLM_SYNTHETIC_SAMPLE_REQUIRED"}))
        self.no_calls()

    async def test_body_shape_and_limits_are_enforced_before_lookup(self):
        for body in ('{"x":1,"x":2}', '{"x":NaN}', '["PRIVATE"]', '{"x":"\\ud800"}'):
            response = await self.http.post("/w4/recommend-from-raw", content=body, headers={"Content-Type": "application/json"})
            self.assertEqual(response.status_code, 400)
        self.assertEqual((await self.http.post("/w4/recommend-from-raw", content="PRIVATE")).status_code, 415)
        response = await self.http.post("/w4/recommend-from-raw", content="x" * 1_000_001, headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 413)
        self.no_calls(); self.ef.assert_not_called()


class CompanyGateTests(unittest.TestCase):
    def setUp(self):
        self.context = read("server-context.synthetic.json")
        self.bundle = self.context["company_knowledge"]
        self.scope = self.context["question_scope_id"]

    def test_source_and_attestation_date_or_scope_limits_are_not_repaired(self):
        for mutation, expected in (
            (lambda b: b["claims"][0].update(verification_status="PENDING"), "COMPANY_ATTESTATION_NOT_USABLE"),
            (lambda b: b["claims"][0].update(usage_status="WITHDRAWN"), "COMPANY_ATTESTATION_NOT_USABLE"),
            (lambda b: b["claims"][0].update(published_at=None), "COMPANY_PUBLICATION_DATE_UNKNOWN"),
            (lambda b: b["claims"][0].update(valid_to="2026-09-08"), "COMPANY_ATTESTATION_OUTSIDE_VALIDITY"),
            (lambda b: b["claims"][0].update(published_at="2026-09-02"), "COMPANY_PUBLICATION_DATE_MISMATCH"),
            (lambda b: b["claims"][0].update(scope_id="other-scope"), "COMPANY_REFERENCE_SCOPE_MISMATCH"),
            (lambda b: b["claims"][0].update(evidence_ids=["absent"]), "COMPANY_EVIDENCE_NOT_RESOLVED"),
            (lambda b: b["source_versions"][0].update(parse_status="UNPARSED"), "COMPANY_SOURCE_NOT_READY"),
            (lambda b: b.update(source_reviews=[]), "COMPANY_SOURCE_REVIEW_MISSING"),
        ):
            with self.subTest(code=expected):
                bundle = deepcopy(self.bundle); mutation(bundle)
                company = consume_company(bundle, self.scope)
                self.assertIn(expected, company.summary["diagnostics"])
                self.assertNotIn("synthetic-learning-claim", company.summary["accepted_refs"]["claim_ids"])

    def test_required_unparsed_source_is_limited_without_inventing_no_requirements(self):
        self.bundle["source_reviews"][0].update(status="UNAVAILABLE", codes=["ATTACHMENT_UNPARSED"], limitations=["JD unparsed"])
        company = consume_company(self.bundle, self.scope)
        self.assertEqual(company.criteria, ())
        self.assertEqual(company.summary["status"], "UNAVAILABLE")
        self.assertIn("REQUIRED_COMPANY_SOURCE_UNAVAILABLE", company.summary["diagnostics"])

    def test_placeholder_duplicate_version_and_wrong_evidence_membership_rejected(self):
        for field in ("placeholder", "duplicate", "snapshot", "evidence"):
            b = deepcopy(self.bundle)
            if field == "placeholder": b["claims"][0]["source_version_id"] = "{{W2_VERSION}}"
            elif field == "duplicate": b["claims"].append(deepcopy(b["claims"][0]))
            elif field == "snapshot": b["input_version_refs"]["source_version_ids"] = []
            else: b["evidence"][0]["source_version_id"] = "another-version"
            with self.assertRaises(ContractError): consume_company(b, self.scope)

    def test_legacy_allowed_enum_is_not_silently_treated_as_usable(self):
        self.bundle["claims"][0]["usage_status"] = "ALLOWED"
        with self.assertRaises(ContractError): consume_company(self.bundle, self.scope)

    def test_original_diagnostic_sample_is_never_consumed_as_bundle(self):
        upstream = json.loads((ROOT / "samples/upstream/skhynix_claim_handoff_sample.json").read_text(encoding="utf-8"))
        with self.assertRaises(ContractError): consume_company(upstream, self.scope)
        before = deepcopy(upstream)
        limited = project_diagnostic_handoff(upstream, scope_id=self.scope)
        self.assertEqual(consume_company(limited, self.scope).criteria, ())
        self.assertEqual(upstream, before)
        self.assertNotIn("manual_claim_examples", limited)

    def test_schemas_are_generated_from_validating_models(self):
        for name, model in (("service-input", ServiceRequest), ("server-context", ServerContext),
                            ("knowledge-bundle", KnowledgeBundle), ("detailed-output", ServiceOutput)):
            schema = json.loads((ROOT / f"schemas/w4-{name}.schema.json").read_text(encoding="utf-8"))
            schema.pop("$schema")
            self.assertEqual(schema, model.model_json_schema())

    def test_company_transport_allows_only_registered_fiction_and_whole_units(self):
        from epick_w4.company_detail import outbound_payload
        request = read("request.json")
        payload = {"schema_version": "w4-detailed-input/0.1", "request_id": request["request_id"],
                   **{k: self.context[k] for k in ("project", "snapshot", "episodes", "excluded_episode_ids", "data_kind")},
                   "question": request["question"], "top_k": request["top_k"]}
        raw, question, _ = prepare_request(payload, "user-demo")
        extracted = extract_evidence(raw, user_id="user-demo", llm=DemoClient("extraction"))
        p = outbound_payload(model_payload(question, extracted["episodes"][0]), consume_company(self.bundle, self.scope))
        check_sample_company_detail(COMPANY_PROMPT, p)
        for key in ("company", "text", "question"):
            changed = deepcopy(p)
            if key == "company": changed["company_criteria"][0]["statement"] += " private"
            elif key == "text": changed["source_units"][0]["text"] += " private"
            else: changed["question"]["job_duties"] = ["invented"]
            with self.assertRaises(LLMError): check_sample_company_detail(COMPANY_PROMPT, changed)


if __name__ == "__main__":
    unittest.main()
