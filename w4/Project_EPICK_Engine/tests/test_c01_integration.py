"""W4-owned C01 acceptance cases, using explicit fiction and model stubs."""

from copy import deepcopy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock
from uuid import uuid4

import httpx
from fastapi import FastAPI

from epick_w4.api import create_service_router
from epick_w4.c01_adapter import consume_c01, status_of, wire
from epick_w4.c01_consumer import C01Consumer, C01Error
from epick_w4.c01_contract import C01Knowledge, C01ServerContext, C01ServiceOutput
from epick_w4.contracts import ContractError
from examples.w4_c01_demo import build_context, C01DemoBackend, C01DemoClient, FixtureTransport
from examples.w4_service_demo import read

ROOT = Path(__file__).resolve().parents[1]
NOW = 1789603200.0


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.context = build_context(NOW)
        self.value = self.context["company_knowledge"]
        self.binding = self.value["sources"][0]

    def consume(self):
        return consume_c01(self.value, self.context["question_scope_id"])

    def test_upstream_files_are_unmodified_and_local_schemas_match_models(self):
        baseline = json.loads(
            (ROOT / "epick_w4/c01_schemas/baseline.json").read_text(encoding="utf-8")
        )
        for entry in baseline["files"]:
            self.assertEqual(
                hashlib.sha256((ROOT / entry["local_path"]).read_bytes()).hexdigest(),
                entry["sha256"],
            )
        for name, model in (
            ("c01-knowledge", C01Knowledge),
            ("server-context-v2", C01ServerContext),
            ("detailed-output-v3", C01ServiceOutput),
        ):
            schema = json.loads(
                (ROOT / f"schemas/w4-{name}.schema.json").read_text(encoding="utf-8")
            )
            schema.pop("$schema")
            self.assertEqual(schema, model.model_json_schema())

    def test_source_local_ids_and_tree_and_native_evidence_are_preserved(self):
        company = self.consume()
        self.assertEqual(company.summary["status"], "AVAILABLE")
        self.assertEqual(len(company.criteria), 4)
        self.assertEqual(len({c["id"] for c in company.criteria}), 4)
        requirements = [c for c in company.criteria if c["kind"] == "REQUIREMENT"]
        self.assertEqual(requirements[0]["upstream_id"], requirements[1]["upstream_id"])
        self.assertNotEqual(requirements[0]["id"], requirements[1]["id"])
        self.assertEqual(
            requirements[0]["upstream_record"]["condition_nodes"][0]["operator"], "AND"
        )
        self.assertEqual(requirements[0]["upstream_record"]["condition_nodes"][2]["operator"], "OR")
        self.assertEqual(
            company.criteria[0]["company_evidence"][0]["provenance"],
            self.binding["knowledge"]["bundle"]["evidences"][0],
        )
        self.assertEqual(len(company.summary["dependencies"]), 2)

    def test_original_w3_fixture_remains_limited_without_invented_claims(self):
        fixture = json.loads(
            (ROOT / "samples/c01/upstream/w4-consumer.json").read_text(encoding="utf-8")
        )
        self.value["sources"] = [self.binding]
        self.binding["signal"] = fixture["ready"]
        self.binding["knowledge"] = fixture["knowledge"]
        before = deepcopy(fixture)
        company = self.consume()
        self.assertEqual(company.criteria, ())
        self.assertIn("C01_REQUIREMENTS_NOT_ASSESSED", company.summary["diagnostics"])
        self.assertEqual(
            company.summary["upstream_details"][0]["requirement_presence"], "NOT_ASSESSED"
        )
        self.assertEqual(fixture, before)

    def test_missing_date_hash_or_scope_is_not_inferred_from_evidence(self):
        original = deepcopy(self.binding["metadata"])
        for field, code in (
            ("published_at", "COMPANY_PUBLICATION_DATE_UNKNOWN"),
            ("content_sha256", "COMPANY_SOURCE_HASH_UNKNOWN"),
            ("scope_id", "COMPANY_REFERENCE_SCOPE_MISMATCH"),
        ):
            with self.subTest(field=field):
                self.binding["metadata"] = {**original, field: None}
                company = self.consume()
                self.assertEqual(company.criteria, ())
                self.assertIn(code, company.summary["diagnostics"])

    def test_source_version_generation_and_exact_extraction_key_are_required(self):
        for change in ("version", "generation", "extraction", "representation", "duplicate_source"):
            with self.subTest(change=change):
                self.setUp()
                if change == "version":
                    self.binding["knowledge"]["bundle"]["evidences"][0]["source_ref"][
                        "source_version_id"
                    ] = str(uuid4())
                elif change == "generation":
                    self.binding["knowledge_generation"] -= 1
                elif change == "extraction":
                    self.binding["metadata"]["index_key"]["extraction_revision_id"] = str(uuid4())
                elif change == "representation":
                    self.binding["metadata"]["index_key"]["representation"] = "html"
                else:
                    self.value["sources"].append(deepcopy(self.binding))
                with self.assertRaises(ContractError):
                    self.consume()

    def test_invalid_tree_or_evidence_membership_is_rejected(self):
        for change in (
            "cycle",
            "or_single_child",
            "missing_root",
            "foreign_evidence",
            "shared_child",
            "duplicate_node",
        ):
            with self.subTest(change=change):
                self.setUp()
                requirement = self.binding["knowledge"]["bundle"]["requirements"][0]
                nodes = requirement["condition_nodes"]
                if change == "cycle":
                    nodes[2]["children"] = ["root", "documentation"]
                elif change == "or_single_child":
                    nodes[2]["children"] = ["documentation"]
                elif change == "missing_root":
                    requirement["root_node_id"] = "missing"
                elif change == "foreign_evidence":
                    nodes[1]["evidence_id"] = "foreign"
                elif change == "shared_child":
                    nodes[2]["children"] = ["apply", "documentation"]
                else:
                    nodes.append(deepcopy(nodes[1]))
                with self.assertRaises(ContractError):
                    self.consume()

    def test_nonusable_statuses_are_preserved_and_excluded(self):
        record = self.binding["knowledge"]["bundle"]["claims"][0]
        record.update(verification_status="FAILED", usage_status="BLOCKED")
        company = self.consume()
        self.assertEqual(len(company.criteria), 3)
        self.assertEqual(
            company.summary["upstream_details"][0]["attestations"][0]["usage_status"], "BLOCKED"
        )
        self.assertIn("COMPANY_ATTESTATION_NOT_USABLE", company.summary["diagnostics"])

    def test_general_requirement_is_not_promoted_to_required(self):
        self.binding["knowledge"]["bundle"]["requirements"][0]["necessity"] = "GENERAL"
        criterion = next(c for c in self.consume().criteria if c["kind"] == "REQUIREMENT")
        self.assertEqual(criterion["requirement_type"], "GENERAL")

    def test_excerpt_integrity_and_offset_pair_are_checked(self):
        for change in ("hash", "offset"):
            with self.subTest(change=change):
                self.setUp()
                evidence = self.binding["knowledge"]["bundle"]["evidences"][0]
                if change == "hash":
                    evidence["excerpt"] += " changed"
                else:
                    evidence["start_offset"] = 0
                with self.assertRaises(ContractError):
                    self.consume()

    def test_ready_requires_both_cursors_and_r2_scope(self):
        for patch in (
            {"required_event_cursor": 10},
            {"required_restriction_revision": 10},
            {"generation": 4.0},
            {"restriction_scope": "source"},
            {"reason": "RESTRICTED"},
        ):
            with self.subTest(patch=patch), self.assertRaises(ContractError):
                wire("signal", {**self.binding["signal"], **patch})


class ConsumerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "consumer.sqlite"
        self.context = build_context(NOW, source_count=1)
        self.transport = FixtureTransport(self.context)
        self.consumer = C01Consumer(
            self.path, transport=self.transport, max_cache_ttl_seconds=300, clock=lambda: NOW
        )
        self.signal = self.context["company_knowledge"]["sources"][0]["signal"]
        self.owner = self.context["project"]["owner_id"]
        self.request = read("request.json")

    def tearDown(self):
        self.consumer.close()
        self.tmp.cleanup()

    def cache(self):
        self.consumer.apply(self.signal)
        self.consumer.put_result(self.owner, self.context, self.request, {"synthetic": True})

    def test_restart_after_commit_before_ack_keeps_valid_cache(self):
        self.cache()
        self.transport.ack_failure = True
        with self.assertRaises(C01Error):
            self.consumer.drain()
        self.consumer.close()
        self.consumer = C01Consumer(
            self.path, transport=self.transport, max_cache_ttl_seconds=300, clock=lambda: NOW
        )
        self.transport.ack_failure = False
        self.assertEqual(self.consumer.drain(), 1)
        self.assertEqual(
            self.consumer.get_result(self.owner, self.context, self.request), {"synthetic": True}
        )

    def test_ack_response_loss_and_identical_redelivery_are_idempotent(self):
        self.cache()
        self.transport.lose_ack_response = True
        with self.assertRaises(C01Error):
            self.consumer.drain()
        self.assertEqual(self.transport.ack(self.signal["signal_id"]), {"delivered": True})
        self.transport.pending = [deepcopy(self.signal)]
        self.assertEqual(self.consumer.drain(), 1)
        self.assertIsNotNone(self.consumer.get_result(self.owner, self.context, self.request))

    def test_invalidation_is_durable_before_ack_is_sent(self):
        self.cache()
        self.transport.pending.clear()
        blocked = self.transport.change(self.signal["source_id"])
        original_ack = self.transport.ack

        def verify_ack(signal_id):
            with self.consumer.transaction():
                self.assertEqual(
                    self.consumer.db.execute("SELECT count(*) FROM results").fetchone()[0], 0
                )
            return original_ack(signal_id)

        self.transport.ack = verify_ack
        self.assertEqual(self.consumer.drain(), 1)
        self.assertIn(blocked["signal_id"], self.transport.acked)

    def test_same_generation_conflict_is_persistent_and_is_not_acked(self):
        self.cache()
        changed = {**self.signal, "signal_id": str(uuid4())}
        self.transport.pending = [changed]
        with self.assertRaises(C01Error):
            self.consumer.drain()
        self.assertNotIn(changed["signal_id"], self.transport.acked)
        self.assertEqual(self.consumer.apply(self.signal), "CONFLICT")
        self.assertEqual(self.consumer.db.execute("SELECT count(*) FROM results").fetchone()[0], 0)

    def test_late_ready_cannot_undo_new_restriction(self):
        self.cache()
        blocked = self.transport.change(self.signal["source_id"])
        self.assertEqual(self.consumer.apply(blocked), "APPLIED")
        self.assertEqual(self.consumer.apply(self.signal), "STALE")
        with self.assertRaises(C01Error):
            self.consumer.check_current(self.context["company_knowledge"])

    def test_false_ack_success_is_rejected(self):
        self.transport.ack = lambda _: {"delivered": False}
        with self.assertRaises(C01Error):
            self.consumer.drain()

    def test_storage_failure_rolls_back_and_never_acks_invalidation(self):
        self.cache()
        self.transport.pending.clear()
        blocked = self.transport.change(self.signal["source_id"])
        self.consumer.db.execute(
            "CREATE TRIGGER fail_delete BEFORE DELETE ON results BEGIN SELECT RAISE(ABORT,'synthetic failure'); END"
        )
        with self.assertRaises(C01Error):
            self.consumer.drain()
        self.assertNotIn(blocked["signal_id"], self.transport.acked)
        self.assertEqual(
            json.loads(self.consumer.db.execute("SELECT payload FROM sources").fetchone()[0]),
            self.signal,
        )
        self.consumer.db.execute("DROP TRIGGER fail_delete")
        self.assertEqual(self.consumer.drain(), 1)
        self.assertEqual(self.consumer.db.execute("SELECT count(*) FROM results").fetchone()[0], 0)

    def test_foreign_database_and_changed_profile_configuration_are_rejected(self):
        path = Path(self.tmp.name) / "legacy.sqlite"
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE state (payload TEXT)")
        with self.assertRaisesRegex(ValueError, "C01_CACHE_PROFILE_MISMATCH"):
            C01Consumer(path, transport=self.transport, max_cache_ttl_seconds=300)
        with self.assertRaisesRegex(ValueError, "C01_CACHE_PROFILE_MISMATCH"):
            C01Consumer(self.path, transport=self.transport, max_cache_ttl_seconds=999)
        with self.consumer.transaction():
            self.consumer.db.execute("DELETE FROM config")
        with self.assertRaisesRegex(ValueError, "C01_CACHE_PROFILE_MISMATCH"):
            C01Consumer(self.path, transport=self.transport, max_cache_ttl_seconds=300)

    def test_new_generation_with_backward_cursors_is_conflict(self):
        self.cache()
        signal = {
            **self.signal,
            "signal_id": str(uuid4()),
            "generation": self.signal["generation"] + 1,
            "event_cursor": 1,
            "required_event_cursor": 1,
            "restriction_revision": 0,
            "required_restriction_revision": 0,
        }
        self.assertEqual(self.consumer.apply(signal), "CONFLICT")
        self.assertEqual(self.consumer.db.execute("SELECT count(*) FROM results").fetchone()[0], 0)


class C01ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = NOW
        self.backend = C01DemoBackend(build_context(NOW))
        self.transport = FixtureTransport(self.backend.context)
        self.consumer = C01Consumer(
            Path(self.tmp.name) / "w4.sqlite",
            transport=self.transport,
            max_cache_ttl_seconds=300,
            clock=lambda: self.now,
        )
        self.consumer.drain()
        self.user = "user-demo"
        self.extractor, self.judge = C01DemoClient("extraction"), C01DemoClient("judgment")
        self.ef, self.jf = Mock(return_value=self.extractor), Mock(return_value=self.judge)
        app = FastAPI()
        app.include_router(
            create_service_router(
                authenticate=lambda: self.user,
                backend=self.backend,
                extraction_client_factory=self.ef,
                client_factory=self.jf,
                c01_consumer=self.consumer,
            )
        )
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://fixture"
        )

    async def asyncTearDown(self):
        await self.http.aclose()
        self.consumer.close()
        self.tmp.cleanup()

    async def post(self, request=None):
        return await self.http.post("/w4/recommend-from-raw", json=request or read("request.json"))

    def result_count(self):
        return self.consumer.db.execute("SELECT count(*) FROM results").fetchone()[0]

    def source(self, index=0):
        return self.backend.context["company_knowledge"]["sources"][index]["signal"]["source_id"]

    async def test_actual_w4_http_flow_preserves_tree_sources_and_exact_episode_evidence(self):
        response = await self.post()
        self.assertEqual(response.status_code, 200, response.text)
        result = C01ServiceOutput.model_validate(response.json()).model_dump()
        self.assertEqual(result["schema_version"], "w4-detailed-output/0.3")
        self.assertEqual(result["eligibility_assessment"], {"status": "NOT_ASSESSED"})
        self.assertEqual(len(result["company_context"]["dependencies"]), 2)
        top = result["candidates"][0]
        self.assertEqual(top["episode_id"], "v4-raw-study")
        self.assertEqual(len(top["company_support"]), 4)
        self.assertTrue(all(s["status"] == "SUPPORTED" for s in top["company_support"]))
        original = next(
            e["raw_text"]
            for e in self.backend.context["episodes"]
            if e["episode_id"] == top["episode_id"]
        )
        for support in top["company_support"]:
            for evidence in support["episode_evidence"]:
                self.assertEqual(
                    original[evidence["start"] : evidence["end"]], evidence["exact_quote"]
                )
            if support["kind"] == "REQUIREMENT":
                leaves = {c["node_id"]: c["status"] for c in support["condition_assessment"]}
                self.assertEqual(
                    leaves,
                    {"apply": "SUPPORTED", "validation": "SUPPORTED", "documentation": "NOT_SHOWN"},
                )
        self.assertEqual(len(self.extractor.calls) + len(self.judge.calls), 6)
        self.assertEqual(self.result_count(), 1)

    async def test_cached_result_requires_current_w1_access_and_no_extra_calls(self):
        first = await self.post()
        second = await self.post()
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.json(), first.json())
        self.assertEqual(len(self.extractor.calls) + len(self.judge.calls), 6)
        self.backend.allowed["RETURN_TO_CALLER"] = False
        denied = await self.post()
        self.assertEqual((denied.status_code, denied.json()), (403, {"error": "POLICY_DENIED"}))
        self.assertEqual(self.result_count(), 0)

    async def test_second_source_change_before_signal_delivery_removes_combined_result(self):
        self.assertEqual((await self.post()).status_code, 200)
        self.transport.change(self.source(1))
        response = await self.post()
        self.assertEqual(
            (response.status_code, response.json()), (409, {"error": "C01_CONTEXT_CHANGED"})
        )
        self.assertEqual(self.result_count(), 0)
        self.assertEqual(len(self.judge.calls), 3)

    async def test_source_restriction_during_extraction_stops_next_model_call(self):
        self.extractor.after_call = lambda: self.transport.change(self.source())
        response = await self.post()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(len(self.extractor.calls), 1)
        self.assertEqual(self.judge.calls, [])
        self.assertEqual(self.result_count(), 0)

    async def test_change_after_last_model_call_blocks_return(self):
        def change():
            if len(self.judge.calls) == 3:
                self.transport.change(self.source())

        self.judge.after_call = change
        response = await self.post()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertNotIn("candidates", response.json())
        self.assertEqual(self.result_count(), 0)

    async def test_status_outage_never_serves_cache(self):
        self.assertEqual((await self.post()).status_code, 200)
        self.transport.unavailable = True
        response = await self.post()
        self.assertEqual(
            (response.status_code, response.json()), (503, {"error": "C01_STATUS_UNAVAILABLE"})
        )
        self.assertEqual(self.result_count(), 0)

    async def test_full_index_key_is_checked_even_if_generation_is_unchanged(self):
        self.transport.current[self.source()]["index_key"]["extraction_revision_id"] = str(uuid4())
        response = await self.post()
        self.assertEqual(response.status_code, 409, response.text)
        self.ef.assert_not_called()

    async def test_and_requires_all_children_and_or_accepts_one_branch(self):
        def remove_validation(payload, response):
            for requirement in response["requirement_checks"]:
                for leaf in requirement["nodes"]:
                    if leaf["node_id"] == "validation":
                        leaf.update(status="NOT_SHOWN", evidence_ids=[])

        self.judge.mutate_response = remove_validation
        response = await self.post()
        self.assertEqual(response.status_code, 200, response.text)
        top = response.json()["candidates"][0]
        self.assertTrue(
            all(
                s["status"] == "NOT_SHOWN"
                for s in top["company_support"]
                if s["kind"] == "REQUIREMENT"
            )
        )
        self.assertEqual(top["used_company_refs"]["requirement_ids"], [])

    async def test_result_only_cannot_support_condition_leaf(self):
        def result_only(payload, response):
            for requirement in response["requirement_checks"]:
                for leaf in requirement["nodes"]:
                    if leaf["status"] == "SUPPORTED":
                        leaf["evidence_ids"] = ["u4"]

        self.judge.mutate_response = result_only
        response = await self.post()
        self.assertEqual(response.status_code, 200, response.text)
        top = response.json()["candidates"][0]
        self.assertTrue(
            all(
                s["status"] == "AMBIGUOUS"
                for s in top["company_support"]
                if s["kind"] == "REQUIREMENT"
            )
        )

    async def test_missing_or_foreign_leaf_is_model_failure(self):
        def remove_leaf(payload, response):
            response["requirement_checks"][0]["nodes"].pop()

        self.judge.mutate_response = remove_leaf
        response = await self.post()
        self.assertEqual((response.status_code, response.json()), (502, {"error": "MODEL_FAILURE"}))
        self.assertEqual(self.result_count(), 0)

    async def test_cache_ttl_recomputes_then_source_expiry_blocks(self):
        self.assertEqual((await self.post()).status_code, 200)
        self.now += 301
        self.assertEqual(self.consumer.purge_expired(), 1)
        self.assertEqual((await self.post()).status_code, 200)
        self.assertEqual(len(self.judge.calls), 6)
        self.now += 1500
        response = await self.post()
        self.assertEqual(
            (response.status_code, response.json()), (409, {"error": "C01_SOURCE_EXPIRED"})
        )
        self.assertEqual(self.result_count(), 0)

    async def test_recovery_requires_new_ready_and_rebound_knowledge(self):
        self.transport.change(self.source(), reason="INDEX_PENDING")
        self.consumer.drain()
        self.assertEqual((await self.post()).status_code, 409)
        ready = self.transport.change(self.source(), reason="READY")
        ready["history_complete"] = False
        self.transport.current[self.source()] = status_of(ready)
        self.consumer.drain()
        self.assertEqual((await self.post()).status_code, 409)
        binding = self.backend.context["company_knowledge"]["sources"][0]
        binding.update(signal=ready, knowledge_generation=ready["generation"])
        self.backend.context["context_version"] = "recomputed-c01-context"
        self.assertEqual((await self.post()).status_code, 200)

    async def test_public_request_and_other_user_cannot_supply_or_reuse_company_data(self):
        request = read("request.json")
        request["company_knowledge"] = self.backend.context["company_knowledge"]
        self.assertEqual((await self.post(request)).status_code, 422)
        self.user = "other-user"
        self.assertEqual((await self.post()).status_code, 404)
        self.ef.assert_not_called()

    async def test_real_or_unknown_profile_is_rejected_before_model(self):
        self.backend.context["company_knowledge"]["data_kind"] = "REAL"
        self.assertEqual((await self.post()).status_code, 403)
        self.backend.context["company_knowledge"]["data_kind"] = "SYNTHETIC"
        self.backend.context["company_knowledge"]["profile"] = "r1"
        self.assertEqual((await self.post()).status_code, 503)
        self.ef.assert_not_called()

    async def test_w1_revocation_after_cache_commit_prevents_return_and_removes_result(self):
        original = self.consumer.put_result

        def revoke(*args):
            original(*args)
            self.backend.allowed["RETURN_TO_CALLER"] = False

        self.consumer.put_result = revoke
        response = await self.post()
        self.assertEqual((response.status_code, response.json()), (403, {"error": "POLICY_DENIED"}))
        self.assertEqual(self.result_count(), 0)

    async def test_unregistered_company_is_blocked_before_any_real_transport_call(self):
        self.judge.simulated = False
        self.backend.context["company_knowledge"]["sources"][0]["knowledge"]["bundle"]["claims"][0][
            "statement"
        ] += " unregistered"
        response = await self.post()
        self.assertEqual(
            (response.status_code, response.json()),
            (403, {"error": "LLM_SYNTHETIC_SAMPLE_REQUIRED"}),
        )
        self.assertEqual(self.extractor.calls + self.judge.calls, [])

    async def test_registered_payload_and_model_response_match_transport_schema(self):
        from jsonschema import Draft202012Validator
        from epick_w4.c01_detail import PROMPT
        from epick_w4.output_schemas import schema_for
        from epick_w4.synthetic_policy import check_sample_payload

        seen = []

        def inspect_payload(payload, response):
            check_sample_payload("c01_company_details", PROMPT, payload)
            Draft202012Validator(schema_for("c01_company_details", payload)).validate(response)
            seen.append(True)

        self.judge.mutate_response = inspect_payload
        response = await self.post()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(seen), 3)


if __name__ == "__main__":
    unittest.main()
