"""In-process HTTP adapter checks. No server, model API, or real data is used."""

import unittest
from unittest.mock import Mock, patch

from epick_w4.llm_contract import LLMError
from test_semantic_matching import StubClient, sample
from test_evidence_extraction import ExtractionStub, extraction_sample

try:
    import httpx
    from fastapi import FastAPI, HTTPException
    from epick_w4.api import MAX_BODY_BYTES, create_router
    API_AVAILABLE = True
except ModuleNotFoundError as error:
    if error.name not in ("fastapi", "httpx"):
        raise
    API_AVAILABLE = False


@unittest.skipUnless(API_AVAILABLE, "Install .[api,test] to run HTTP adapter tests")
class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.llm = StubClient()
        self.factory = Mock(return_value=self.llm)
        self.user_id = "user-demo"

        def authenticate():
            return self.user_id

        app = FastAPI()
        app.include_router(create_router(authenticate=authenticate, client_factory=self.factory))
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                      base_url="http://w4-test")

    async def asyncTearDown(self):
        await self.http.aclose()

    async def test_json_request_returns_same_grounded_recommendation(self):
        response = await self.http.post("/w4/recommend", json=sample())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["candidates"][0]["episode_id"], "episode-common-plan")
        self.assertEqual(response.json()["inference"]["mode"], "SIMULATED_LLM")
        self.assertEqual(len(self.llm.requests), 3)

    async def test_missing_authentication_does_not_construct_client(self):
        self.user_id = None
        response = await self.http.post("/w4/recommend", json=sample())
        self.assertEqual(response.status_code, 401)
        self.factory.assert_not_called()

    async def test_request_body_cannot_override_authenticated_identity(self):
        value = sample()
        self.user_id = "other-user"
        value["user_id"] = "user-demo"
        response = await self.http.post("/w4/recommend", json=value)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "PROJECT_ACCESS_DENIED")
        self.factory.assert_not_called()

    async def test_host_authentication_dependency_can_reject_request(self):
        def authenticate():
            raise HTTPException(status_code=401, detail="Unauthorized")
        app = FastAPI()
        app.include_router(create_router(authenticate=authenticate, client_factory=self.factory))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                      base_url="http://w4-test") as http:
            self.assertEqual((await http.post("/w4/recommend", json=sample())).status_code, 401)
        self.factory.assert_not_called()

    async def test_real_data_returns_forbidden_and_never_reaches_model(self):
        value = sample()
        value["data_kind"] = "REAL"
        response = await self.http.post("/w4/recommend", json=value)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "LLM_REAL_DATA_NOT_ENABLED")
        self.assertEqual(self.llm.requests, [])

    async def test_invalid_json_and_top_level_value_are_redacted(self):
        for body in ('{"PRIVATE":', '["PRIVATE"]', '{"x":1,"x":2}', '{"x":NaN}',
                     '```json\n{}\n```', '{"x":"\\ud800"}'):
            response = await self.http.post("/w4/recommend", content=body,
                                      headers={"Content-Type": "application/json"})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json(), {"error": "INVALID_JSON_OBJECT"})
        self.factory.assert_not_called()

    async def test_invalid_contract_does_not_echo_input(self):
        value = sample()
        value["question"]["version"] = "PRIVATE"
        response = await self.http.post("/w4/recommend", json=value)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("PRIVATE", response.text)
        self.assertEqual(self.llm.requests, [])

    async def test_content_type_and_actual_body_size_are_checked(self):
        response = await self.http.post("/w4/recommend", content="PRIVATE")
        self.assertEqual(response.status_code, 415)
        response = await self.http.post("/w4/recommend", content="x" * (MAX_BODY_BYTES + 1),
                                  headers={"Content-Type": "application/json", "Content-Length": "1"})
        self.assertEqual(response.status_code, 413)
        self.factory.assert_not_called()

    async def test_provider_errors_map_to_safe_http_responses(self):
        for code, stage, status in (("LLM_API_KEY_MISSING", "configuration", 503),
                                    ("LLM_RATE_LIMITED", "question", 503),
                                    ("LLM_TIMEOUT", "question", 504),
                                    ("LLM_INVALID_JSON", "question", 502),
                                    ("LLM_SYNTHETIC_SAMPLE_REQUIRED", "question", 403)):
            self.llm.failure = LLMError(code, stage)
            response = await self.http.post("/w4/recommend", json=sample())
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.json(), {"error": code, "stage": stage})

    async def test_large_pool_does_not_reach_model(self):
        value = sample()
        value["question"]["text"] = "가상 긴 문항" * 2000
        response = await self.http.post("/w4/recommend", json=value)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.llm.requests, [])

    async def test_slow_model_work_is_dispatched_off_event_loop(self):
        with patch("epick_w4.api.run_in_threadpool", wraps=__import__(
                "starlette.concurrency", fromlist=["run_in_threadpool"]).run_in_threadpool) as dispatch:
            response = await self.http.post("/w4/recommend", json=sample())
        self.assertEqual(response.status_code, 200)
        dispatch.assert_called_once()


@unittest.skipUnless(API_AVAILABLE, "Install .[api,test] to run HTTP adapter tests")
class ExtractionApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.llm = ExtractionStub()
        self.factory = Mock(return_value=self.llm)
        self.user_id = "user-demo"

        def authenticate():
            return self.user_id

        app = FastAPI()
        app.include_router(create_router(authenticate=authenticate, client_factory=self.factory))
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://w4-test")

    async def asyncTearDown(self):
        await self.http.aclose()

    async def test_raw_request_returns_grounded_extraction(self):
        response = await self.http.post("/w4/extract-evidence", json=extraction_sample())
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["schema_version"], "w4-evidence-output/0.1")
        self.assertEqual(result["inference"]["mode"], "SIMULATED_LLM")
        self.assertEqual(len(result["episodes"]), 4)
        self.assertEqual(len(self.llm.requests), 4)

    async def test_missing_or_foreign_authentication_precedes_client_construction(self):
        for user, status in ((None, 401), ("other-user", 403)):
            self.user_id = user
            response = await self.http.post("/w4/extract-evidence", json=extraction_sample())
            self.assertEqual(response.status_code, status)
        self.factory.assert_not_called()

    async def test_real_raw_data_cannot_reach_model(self):
        value = extraction_sample()
        value["data_kind"] = "REAL"
        response = await self.http.post("/w4/extract-evidence", json=value)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "LLM_REAL_DATA_NOT_ENABLED")
        self.assertEqual(self.llm.requests, [])

    async def test_unexpected_facts_field_returns_redacted_contract_error(self):
        value = extraction_sample()
        value["episodes"][1]["facts"] = "PRIVATE"
        response = await self.http.post("/w4/extract-evidence", json=value)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("PRIVATE", response.text)
        self.assertEqual(self.llm.requests, [])

    async def test_oversized_later_episode_is_rejected_before_any_model_call(self):
        value = extraction_sample()
        value["episodes"][-1]["raw_text"] = "가" * 3001
        response = await self.http.post("/w4/extract-evidence", json=value)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.llm.requests, [])

    async def test_bad_model_reference_returns_safe_gateway_error(self):
        self.llm.responses["episode-learning"]["units"][0]["unit_id"] = "PRIVATE"
        response = await self.http.post("/w4/extract-evidence", json=extraction_sample())
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {"error": "LLM_INVALID_UNIT_REFERENCE", "stage": "extraction"})
        self.assertEqual(len(self.llm.requests), 1)


if __name__ == "__main__":
    unittest.main()
