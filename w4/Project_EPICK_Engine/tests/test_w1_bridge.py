from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from jsonschema import Draft202012Validator

from epick_w4.c01_consumer import C01Consumer
from epick_w4.service_adapter import execute_service
from epick_w4.w1_bridge import BridgeError, W1ExecutionAdapter, publication_for, validate_binding
from examples.w4_c01_demo import C01DemoBackend, C01DemoClient, FixtureTransport
from examples.w4_w1_demo import SyntheticRunStore, candidate_responses, fixture
from tests.test_c01_staged import StagedStub

ROOT = Path(__file__).resolve().parents[1]


class W1BridgeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.binding, self.context = fixture()
        self.consumer = C01Consumer(self.directory / "c01.sqlite", transport=FixtureTransport(self.context),
                                    max_cache_ttl_seconds=300)
        self.addCleanup(self.consumer.close)
        self.consumer.drain()
        self.store = SyntheticRunStore(self.directory / "run.sqlite", self.binding, self.context,
                                       check_sources=self.consumer.check_current)
        self.addCleanup(self.store.close)
        self.result = execute_service(self.binding.request, user_id=str(self.binding.owner_user_id),
            backend=C01DemoBackend(self.context), extraction_factory=lambda: C01DemoClient("extraction"),
            judgment_factory=StagedStub, c01_consumer=self.consumer, c01_split=True)
        # Canned unit-test result, never counted as actual model evaluation.
        for stage in ("extraction", "judgment"):
            self.result["inference"][stage]["mode"] = "LLM"
        self.adapter = W1ExecutionAdapter(store=self.store, extraction_factory=Mock(), judgment_factory=Mock(),
                                          c01_consumer=self.consumer)

    def execute(self):
        with patch("epick_w4.w1_bridge.execute_service", return_value=deepcopy(self.result)) as engine:
            self.adapter.execute(owner_user_id=self.binding.owner_user_id, run_id=self.binding.run_id)
        return engine

    def test_actual_w1_candidate_schema_and_full_details_are_preserved(self):
        value = publication_for(self.binding, self.result)
        schema = json.loads((ROOT / "schemas/w1-candidate.pinned.schema.json").read_text(encoding="utf-8"))
        for row in candidate_responses(value):
            Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(row)
            self.assertEqual(row["validation_status"], "LIMITED")
            self.assertEqual(row["match_status"], "NEEDS_VERIFICATION")
        self.assertEqual(value["full_result"], self.result)
        self.assertEqual(value["source_dependencies"], self.result["company_context"]["dependencies"])
        self.assertEqual(value["result_version"], publication_for(self.binding, deepcopy(self.result))["result_version"])

    def test_duplicate_worker_does_not_call_model_or_append_candidates(self):
        self.assertEqual(self.execute().call_count, 1)
        first = self.store.read_result(self.binding.owner_user_id, self.binding.run_id)
        self.assertEqual(self.execute().call_count, 0)
        self.assertEqual(self.store.read_result(self.binding.owner_user_id, self.binding.run_id), first)

    def test_other_owner_cannot_acquire_or_read(self):
        with self.assertRaises(BridgeError):
            self.adapter.execute(owner_user_id=uuid4(), run_id=self.binding.run_id)
        self.assertIsNone(self.store.read_result(uuid4(), self.binding.run_id))

    def test_uuid_and_snapshot_and_version_mapping_are_fenced(self):
        bad = [replace(self.binding, snapshot_id=uuid4()),
               replace(self.binding, question_version_id="untrusted"),
               replace(self.binding, episode_versions=self.binding.episode_versions[:-1]),
               replace(self.binding, episode_versions=self.binding.episode_versions + self.binding.episode_versions[:1])]
        for binding in bad:
            with self.assertRaises(BridgeError):
                validate_binding(binding, self.context, binding.owner_user_id, binding.run_id)

    def test_real_input_is_blocked(self):
        from epick_w4.synthetic_policy import content_hash
        context = deepcopy(self.context)
        context["data_kind"] = "REAL"
        binding = replace(self.binding, context_sha256=content_hash(context))
        with self.assertRaisesRegex(BridgeError, "REAL_DATA"):
            validate_binding(binding, context, binding.owner_user_id, binding.run_id)

    def test_simulated_result_is_not_published_as_actual_inference(self):
        self.result["inference"]["judgment"]["mode"] = "SIMULATED_LLM"
        with self.assertRaisesRegex(BridgeError, "ACTUAL_INFERENCE_REQUIRED"):
            self.execute()
        self.assertIsNone(self.store.read_result(self.binding.owner_user_id, self.binding.run_id))

    def test_both_simulated_stages_are_rejected_without_acceptance_opt_in(self):
        for stage in ("extraction", "judgment"):
            self.result["inference"][stage]["mode"] = "SIMULATED_LLM"
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(BridgeError, "ACTUAL_INFERENCE_REQUIRED"):
                self.execute()
        self.assertIsNone(self.store.read_result(self.binding.owner_user_id, self.binding.run_id))

    def test_mixed_actual_and_simulated_stages_are_rejected_even_with_opt_in(self):
        self.result["inference"]["judgment"]["mode"] = "SIMULATED_LLM"
        with patch.dict(os.environ, {"W4_RECOMMENDATION_SYNTHETIC_ACCEPTANCE": "YES"}, clear=True):
            with self.assertRaisesRegex(BridgeError, "ACTUAL_INFERENCE_REQUIRED"):
                self.execute()
        self.assertIsNone(self.store.read_result(self.binding.owner_user_id, self.binding.run_id))

    def test_unknown_episode_or_duplicate_rank_cannot_be_saved(self):
        for change in ("episode", "rank", "question"):
            value = deepcopy(self.result)
            if change == "episode":
                value["candidates"][0]["episode_id"] = "foreign"
            elif change == "rank":
                value["candidates"][0]["rank"] = 2
            else:
                value["question_scope"]["question_id"] = "challenge"
            with self.assertRaises(BridgeError):
                publication_for(self.binding, value)

    def test_revoke_or_cancel_during_inference_prevents_atomic_publication(self):
        for name in ("revoke", "cancel"):
            with self.subTest(change=name):
                # Reset only this isolated fixture's row between fault scenarios.
                with self.store.db:
                    self.store.db.execute("UPDATE run SET status='PENDING',allowed=1,lease=NULL,publication=NULL")
                def engine(*args, **kwargs):
                    getattr(self.store, name)()
                    return deepcopy(self.result)
                with patch("epick_w4.w1_bridge.execute_service", side_effect=engine), self.assertRaises(BridgeError):
                    self.adapter.execute(owner_user_id=self.binding.owner_user_id, run_id=self.binding.run_id)
                self.assertIsNone(self.store.read_result(self.binding.owner_user_id, self.binding.run_id))
                if name == "cancel":
                    self.assertEqual(self.store.db.execute("SELECT status FROM run").fetchone()[0], "CANCELLED")

    def test_context_change_during_inference_prevents_publication(self):
        def engine(*args, **kwargs):
            self.store.change_context()
            return deepcopy(self.result)
        with patch("epick_w4.w1_bridge.execute_service", side_effect=engine), self.assertRaises(BridgeError):
            self.adapter.execute(owner_user_id=self.binding.owner_user_id, run_id=self.binding.run_id)
        self.assertIsNone(self.store.read_result(self.binding.owner_user_id, self.binding.run_id))

    def test_result_revocation_after_completion_blocks_later_reads(self):
        self.execute()
        self.assertIsNotNone(self.store.read_result(self.binding.owner_user_id, self.binding.run_id))
        self.store.revoke()
        self.assertIsNone(self.store.read_result(self.binding.owner_user_id, self.binding.run_id))

    def test_previous_lease_cannot_publish(self):
        lease = self.store.acquire(owner_user_id=self.binding.owner_user_id, run_id=self.binding.run_id)
        stale = replace(lease, lease_token=uuid4())
        self.assertFalse(self.store.publish(stale, publication_for(self.binding, self.result)))

    def test_source_expiry_after_publication_blocks_reads(self):
        self.execute()
        self.consumer.clock = lambda: 9999999999
        self.assertIsNone(self.store.read_result(self.binding.owner_user_id, self.binding.run_id))

    def test_source_generation_change_after_publication_blocks_reads(self):
        self.execute()
        source_id = self.context["company_knowledge"]["sources"][0]["signal"]["source_id"]
        self.consumer.transport.current[source_id]["generation"] += 1
        self.assertIsNone(self.store.read_result(self.binding.owner_user_id, self.binding.run_id))


if __name__ == "__main__":
    unittest.main()
