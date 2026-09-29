"""Selection and inference wiring only; all model evidence/adapters here are fakes."""

from copy import deepcopy
import io
import json
import unittest
from unittest.mock import Mock, patch

from epick_w4 import extract_evidence, recommend, select_evaluated_client
from epick_w4.__main__ import main
from epick_w4.llm_contract import LLMError
from epick_w4.llm_prompts import QUESTION_PROMPT
from epick_w4.model_eval import approve_document
from epick_w4.model_selection import available_client_factories
from test_evidence_extraction import ExtractionStub, extraction_sample
from test_model_eval import fixture_run, fixtures, recorded_fixture
from test_semantic_matching import ROOT, StubClient, sample


def selection_fixture():
    benchmark, policy = fixtures()
    a = fixture_run(benchmark, policy, "fixture-a")
    b = fixture_run(benchmark, policy, "fixture-b")
    a["records"][0]["response"]["required_facts"] = []
    # Emulate reviewed imports ONLY inside tests. Never save these as real scores.
    benchmark = approve_document(benchmark, "unit-test-review")
    policy = approve_document(policy, "unit-test-review")
    runs = [recorded_fixture(a), recorded_fixture(b)]
    client = StubClient()
    client.provider, client.model = b["provider"], b["model"]
    client.simulated = False
    client.generation_config = deepcopy(b["generation_config"])
    factories = {"fixture-a": Mock(), "fixture-b": Mock(return_value=client)}
    return benchmark, policy, runs, client, factories


class ModelSelectionTests(unittest.TestCase):
    def setUp(self):
        self.benchmark, self.policy, self.runs, self.client, self.factories = selection_fixture()

    def select(self):
        return select_evaluated_client(self.benchmark, self.policy, self.runs,
                                       client_factories=self.factories)

    def assert_no_construction(self):
        for factory in self.factories.values():
            factory.assert_not_called()

    def test_unique_winner_drives_non_solar_inference_and_records_selection(self):
        selected = self.select()
        result = recommend(sample(), user_id="user-demo", llm=selected)
        selection = result["inference"]["model_selection"]
        self.assertEqual(result["inference"]["provider"], "unit-test-fixture")
        self.assertEqual(result["inference"]["model"], "fixture-b")
        self.assertEqual(selection["candidate_id"], "fixture-b")
        self.assertEqual(selection["total_score"], 100)
        self.assertEqual(selection["evaluated_axes"], ["question", "matching"])
        self.assertEqual(selection["unevaluated_stages"], ["extraction"])
        self.assertEqual(len(selection["run_sha256"]), 64)
        self.assertEqual(len(self.client.requests), 3)
        self.factories["fixture-a"].assert_not_called()
        self.factories["fixture-b"].assert_called_once_with()
        self.assertEqual(result["candidates"][0]["episode_id"], "episode-common-plan")

    def test_same_selected_model_can_extract_with_evaluation_scope_visible(self):
        client = ExtractionStub()
        client.provider, client.model = self.client.provider, self.client.model
        client.simulated = False
        client.generation_config = self.client.generation_config
        self.factories["fixture-b"].return_value = client
        result = extract_evidence(extraction_sample(), user_id="user-demo", llm=self.select())
        self.assertEqual(result["inference"]["model"], "fixture-b")
        self.assertIn("extraction", result["inference"]["model_selection"]["unevaluated_stages"])
        self.assertEqual(len(client.requests), 4)

    def test_unapproved_or_changed_policy_does_not_construct_any_client(self):
        for policy in (fixtures()[1], {**self.policy, "weights": {"question": 9, "matching": 1}}):
            with self.assertRaises(LLMError) as caught:
                select_evaluated_client(self.benchmark, policy, self.runs, client_factories=self.factories)
            self.assertEqual(caught.exception.code, "LLM_EVALUATION_NOT_APPROVED")
        self.assert_no_construction()

    def test_simulated_and_incomplete_comparisons_cannot_select_a_runtime_model(self):
        original = deepcopy(self.runs)
        for kind in ("simulation", "incomplete", "unreviewed-import", "one-model"):
            self.runs = deepcopy(original)
            if kind == "simulation":
                self.runs[1]["provenance"] = "SIMULATED"
            elif kind == "incomplete":
                self.runs[1]["records"].pop()
                self.runs[1] = recorded_fixture(self.runs[1])
            elif kind == "unreviewed-import":
                self.runs[1].pop("review")
            else:
                self.runs = self.runs[1:]
            with self.subTest(kind=kind), self.assertRaises(LLMError) as caught:
                self.select()
            self.assertEqual(caught.exception.code, "LLM_SELECTION_UNAVAILABLE")
        self.assert_no_construction()

    def test_tie_does_not_use_name_input_order_or_available_adapter_to_choose(self):
        self.runs[0] = recorded_fixture(fixture_run(self.benchmark, self.policy, "fixture-a"))
        self.runs.reverse()
        with self.assertRaises(LLMError) as caught:
            self.select()
        self.assertEqual(caught.exception.code, "LLM_SELECTION_TIED")
        self.assert_no_construction()

    def test_zero_scores_cannot_select_a_model(self):
        for i, run in enumerate(self.runs):
            for record in run["records"]:
                record.update(response=None, error="TIMEOUT")
            self.runs[i] = recorded_fixture(run)
        with self.assertRaises(LLMError) as caught:
            self.select()
        self.assertEqual(caught.exception.code, "LLM_SELECTION_UNAVAILABLE")
        self.assert_no_construction()

    def test_missing_winner_adapter_never_falls_back_to_loser_or_solar(self):
        with patch("epick_w4.solar_client.SolarClient") as solar:
            with self.assertRaises(LLMError) as caught:
                select_evaluated_client(self.benchmark, self.policy, self.runs,
                                         client_factories={"fixture-a": self.factories["fixture-a"]})
        self.assertEqual(caught.exception.code, "LLM_SELECTED_ADAPTER_UNAVAILABLE")
        self.assert_no_construction()
        solar.assert_not_called()

    def test_different_model_provider_settings_or_simulation_is_not_the_evaluated_winner(self):
        changes = ({"provider": "upstage"}, {"model": "different"},
                   {"generation_config": {"fixture": False}}, {"generation_config": None},
                   {"simulated": True})
        for change in changes:
            _, _, _, client, _ = selection_fixture()
            for key, value in change.items():
                setattr(client, key, value)
            self.factories["fixture-b"].return_value = client
            with self.subTest(change=change), self.assertRaises(LLMError) as caught:
                self.select()
            self.assertEqual(caught.exception.code, "LLM_SELECTED_CONFIGURATION_MISMATCH")
            self.assertEqual(client.requests, [])

    def test_configuration_drift_after_selection_is_caught_before_next_call(self):
        selected = self.select()
        self.client.generation_config["fixture"] = False
        with self.assertRaises(LLMError) as caught:
            selected.complete_json(stage="question", system_prompt=QUESTION_PROMPT,
                                    payload={"question_text": sample()["question"]["text"]})
        self.assertEqual(caught.exception.code, "LLM_SELECTED_CONFIGURATION_MISMATCH")
        self.assertEqual(self.client.requests, [])

    def test_inference_cannot_override_prompt_or_send_unapproved_content(self):
        selected = self.select()
        value = {"question_text": sample()["question"]["text"]}
        for prompt, payload, code in ((QUESTION_PROMPT + "PRIVATE", value, "LLM_SELECTED_PROMPT_MISMATCH"),
                                      (QUESTION_PROMPT, {"question_text": "PRIVATE"}, "LLM_SYNTHETIC_SAMPLE_REQUIRED")):
            with self.assertRaises(LLMError) as caught:
                selected.complete_json(stage="question", system_prompt=prompt, payload=payload)
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertEqual(self.client.requests, [])

    def test_inconsistent_comparison_input_and_factory_error_are_redacted(self):
        self.runs[0]["records"][0]["request_sha256"] = "PRIVATE"
        with self.assertRaises(LLMError) as caught:
            self.select()
        self.assertEqual(str(caught.exception), "LLM_EVALUATION_INVALID: configuration")
        self.assert_no_construction()
        self.benchmark, self.policy, self.runs, self.client, self.factories = selection_fixture()
        self.factories["fixture-b"].side_effect = RuntimeError("PRIVATE credential")
        with self.assertRaises(LLMError) as caught:
            self.select()
        self.assertEqual(str(caught.exception), "LLM_CLIENT_INITIALIZATION_FAILED: configuration")
        self.factories["fixture-b"].side_effect = None
        selected = self.select()
        self.client.complete_json = Mock(side_effect=RuntimeError("PRIVATE response"))
        with self.assertRaises(LLMError) as caught:
            selected.complete_json(stage="question", system_prompt=QUESTION_PROMPT,
                                    payload={"question_text": sample()["question"]["text"]})
        self.assertEqual(str(caught.exception), "LLM_CLIENT_CALL_FAILED: question")

    def test_selection_does_not_mutate_evidence_and_metadata_is_an_independent_copy(self):
        original = deepcopy((self.benchmark, self.policy, self.runs))
        selected = self.select()
        selection = selected.selection
        selection["total_score"] = -100
        self.assertEqual(selected.selection["total_score"], 100)
        self.assertEqual((self.benchmark, self.policy, self.runs), original)

    def test_cli_uses_evaluation_selection_with_injected_non_solar_adapter(self):
        documents = {"benchmark.json": self.benchmark, "policy.json": self.policy,
                     "a.json": self.runs[0], "b.json": self.runs[1]}
        for operation, file in (("recommend", "w4_semantic.json"),
                                ("extract", "extraction/raw-experiences.synthetic.json")):
            if operation == "extract":
                client = ExtractionStub()
                client.provider, client.model = self.client.provider, self.client.model
                client.simulated, client.generation_config = False, self.client.generation_config
                self.factories["fixture-b"].return_value = client
            with patch("epick_w4.evaluate.read_document", side_effect=lambda p: deepcopy(documents[p.name])), \
                    patch("sys.stdout", new_callable=io.StringIO) as stdout:
                code = main(["--input", str(ROOT / "samples" / file), "--user-id", "user-demo",
                             "--operation", operation, "--engine", "evaluated",
                             "--benchmark", "benchmark.json", "--policy", "policy.json",
                             "--run", "a.json", "--run", "b.json"], client_factories=self.factories)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["inference"]["model"], "fixture-b")
        self.factories["fixture-a"].assert_not_called()

    def test_cli_requires_evaluation_files_and_refuses_ignored_evaluation_flags(self):
        for args, expected in ((["--engine", "evaluated"], "LLM_EVALUATION_FILES_REQUIRED"),
                                (["--engine", "solar", "--policy", "p.json"], "EVALUATION_ARGUMENTS_REQUIRE_EVALUATED_ENGINE")):
            with patch("epick_w4.solar_client.SolarClient.from_env") as factory, \
                    patch("sys.stderr", new_callable=io.StringIO) as stderr:
                code = main(["--input", "unused", "--user-id", "user-demo", *args])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(stderr.getvalue())["error"], expected)
            factory.assert_not_called()

    def test_cli_builtin_registry_does_not_invent_an_adapter_for_other_provider(self):
        with patch("epick_w4.solar_client.SolarClient") as solar:
            factories = available_client_factories(self.runs)
            with self.assertRaises(LLMError) as caught:
                select_evaluated_client(self.benchmark, self.policy, self.runs, client_factories=factories)
        self.assertEqual(caught.exception.code, "LLM_SELECTED_ADAPTER_UNAVAILABLE")
        solar.assert_not_called()


try:
    import httpx
    from fastapi import FastAPI
    from epick_w4.api import create_router
    API_AVAILABLE = True
except ModuleNotFoundError as error:
    if error.name not in ("fastapi", "httpx"):
        raise
    API_AVAILABLE = False


@unittest.skipUnless(API_AVAILABLE, "Install .[api,test] for HTTP selection checks")
class SelectedModelApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_api_calls_selected_model_and_returns_selection_evidence(self):
        benchmark, policy, runs, client, factories = selection_fixture()
        app = FastAPI()
        app.include_router(create_router(authenticate=lambda: "user-demo", client_factory=lambda:
            select_evaluated_client(benchmark, policy, runs, client_factories=factories)))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://w4-test") as http:
            response = await http.post("/w4/recommend", json=sample())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["inference"]["model_selection"]["candidate_id"], "fixture-b")
        self.assertEqual(len(client.requests), 3)
        factories["fixture-a"].assert_not_called()

    async def test_api_without_valid_comparison_returns_configuration_error_without_call(self):
        benchmark, policy = fixtures()
        factory = Mock()
        app = FastAPI()
        app.include_router(create_router(authenticate=lambda: "user-demo", client_factory=lambda:
            select_evaluated_client(benchmark, policy, [], client_factories={"unused": factory})))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://w4-test") as http:
            response = await http.post("/w4/recommend", json=sample())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": "LLM_EVALUATION_NOT_APPROVED", "stage": "configuration"})
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
