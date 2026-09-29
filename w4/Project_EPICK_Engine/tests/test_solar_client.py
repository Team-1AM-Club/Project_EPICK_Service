"""Real transport code exercised with mocked HTTP; no credentials or paid calls."""

import copy
import io
from http.client import IncompleteRead
import json
import socket
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from epick_w4 import recommend
from epick_w4.__main__ import main
from epick_w4.llm_contract import LLMError
from epick_w4.llm_prompts import QUESTION_PROMPT
from epick_w4.solar_client import API_URL, MAX_RESPONSE_BYTES, SolarClient, _NoRedirect
from epick_w4.synthetic_policy import content_hash
from test_semantic_matching import ROOT, StubClient, candidate_response, question_response, sample


def http_response(value, **choice_fields):
    choice = {"finish_reason": "stop", "message": {
        "content": json.dumps(value, ensure_ascii=False), "reasoning_content": "DO_NOT_RETURN"}}
    choice.update(choice_fields)
    return io.BytesIO(json.dumps({"choices": [choice]}, ensure_ascii=False).encode("utf-8"))


class SolarTests(unittest.TestCase):
    def setUp(self):
        self.client = SolarClient("synthetic-test-key-not-a-credential")
        self.client._opener = Mock()
        self.payload = {"question_text": sample()["question"]["text"]}

    def call(self):
        return self.client.complete_json(stage="question", system_prompt=QUESTION_PROMPT,
                                         payload=self.payload)

    def test_http_request_uses_fixed_endpoint_and_bounded_model_settings(self):
        self.client._opener.open.return_value = http_response(question_response())
        result = self.call()
        self.assertEqual(result, question_response())
        request = self.client._opener.open.call_args.args[0]
        self.assertEqual(request.full_url, API_URL)
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.get_header("Authorization"),
                         "Bearer synthetic-test-key-not-a-credential")
        body = json.loads(request.data)
        self.assertEqual((body["model"], body["reasoning_effort"], body["stream"]),
                         ("solar-pro4", "none", False))
        self.assertEqual(body["max_tokens"], 6000)
        self.assertEqual(json.loads(body["messages"][1]["content"]), self.payload)
        self.assertEqual(self.client._opener.open.call_args.kwargs["timeout"], 60)
        self.assertNotIn("DO_NOT_RETURN", json.dumps(result))
        self.assertNotIn("synthetic-test-key", repr(self.client))

    def test_two_stage_pipeline_through_mocked_provider_envelopes(self):
        def respond(request, **_kwargs):
            body = json.loads(request.data)
            value = json.loads(body["messages"][1]["content"])
            return http_response(candidate_response(value) if "candidates" in value else question_response())
        self.client._opener.open.side_effect = respond
        result = recommend(sample(), user_id="user-demo", llm=self.client)
        self.assertEqual(result["candidates"][0]["episode_id"], "episode-common-plan")
        self.assertEqual(result["candidates"][0]["status"], "DIRECT_MATCH")
        self.assertEqual(result["inference"]["provider"], "upstage")
        self.assertEqual(self.client._opener.open.call_count, 3)

    def test_configuration_requires_key_and_valid_values(self):
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(LLMError) as caught:
            SolarClient.from_env()
        self.assertEqual(caught.exception.code, "LLM_API_KEY_MISSING")
        for kwargs in ({"api_key": "bad\nkey"}, {"api_key": "한글"},
                       {"api_key": "key", "model": "https://another-host"},
                       {"api_key": "key", "timeout": True}, {"api_key": "key", "timeout": 121}):
            with self.subTest(kwargs=kwargs), self.assertRaises(LLMError):
                SolarClient(**kwargs)

    def test_http_failures_are_redacted_and_never_retried(self):
        for status, code in ((401, "LLM_AUTH_FAILED"), (403, "LLM_ACCESS_DENIED"),
                             (429, "LLM_RATE_LIMITED"), (503, "LLM_PROVIDER_UNAVAILABLE"),
                             (302, "LLM_REDIRECT_REJECTED"), (400, "LLM_HTTP_ERROR")):
            body = io.BytesIO(b"PRIVATE_PROVIDER_BODY")
            self.client._opener.open.reset_mock()
            self.client._opener.open.side_effect = HTTPError(API_URL, status, "PRIVATE", {}, body)
            with self.subTest(status=status), self.assertRaises(LLMError) as caught:
                self.call()
            self.assertEqual(str(caught.exception), f"{code}: question")
            self.assertEqual(self.client._opener.open.call_count, 1)
            self.assertTrue(body.closed)

    def test_network_and_timeout_failures_are_redacted(self):
        for error, code in ((TimeoutError("PRIVATE"), "LLM_TIMEOUT"),
                            (URLError(socket.timeout("PRIVATE")), "LLM_TIMEOUT"),
                            (URLError("PRIVATE"), "LLM_NETWORK_ERROR"),
                            (OSError("PRIVATE"), "LLM_NETWORK_ERROR")):
            self.client._opener.open.side_effect = error
            with self.subTest(code=code), self.assertRaises(LLMError) as caught:
                self.call()
            self.assertEqual(str(caught.exception), f"{code}: question")

    def test_incomplete_http_body_is_a_safe_transport_error(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.side_effect = IncompleteRead(b"PRIVATE_PARTIAL_BODY", 500)
        self.client._opener.open.return_value = response
        with self.assertRaises(LLMError) as caught:
            self.call()
        self.assertEqual(str(caught.exception), "LLM_NETWORK_ERROR: question")
        response.__exit__.assert_called_once()

    def test_redirect_handler_does_not_create_a_redirected_request(self):
        request = Request(API_URL, headers={"Authorization": "Bearer test"})
        for code in (301, 302, 303, 307, 308):
            self.assertIsNone(_NoRedirect().redirect_request(request, None, code, "", {},
                                                            "https://another-host.example"))
        self.assertTrue(any(isinstance(handler, _NoRedirect)
                            for handler in SolarClient("test")._opener.handlers))

    def test_response_read_is_bounded(self):
        response = io.BytesIO(b"x" * (MAX_RESPONSE_BYTES + 20))
        self.client._opener.open.return_value = response
        with self.assertRaises(LLMError) as caught:
            self.call()
        self.assertEqual(caught.exception.code, "LLM_RESPONSE_LIMIT_EXCEEDED")
        self.assertTrue(response.closed)

    def test_invalid_envelopes_do_not_leak_provider_text(self):
        for raw in (b"PRIVATE", b"\xff", b"[]", b'{"choices":[]}',
                    b'{"choices":[null]}', b'{"choices":[{},{}]}', b'{"choices":{}}'):
            self.client._opener.open.return_value = io.BytesIO(raw)
            with self.subTest(raw=raw), self.assertRaises(LLMError) as caught:
                self.call()
            self.assertEqual(caught.exception.code, "LLM_INVALID_RESPONSE")

    def test_truncation_refusal_tool_call_and_non_json_are_rejected(self):
        cases = [
            ({"finish_reason": "length"}, "LLM_OUTPUT_TRUNCATED"),
            ({"finish_reason": "tool_calls"}, "LLM_INVALID_RESPONSE"),
            ({"message": {"content": "{}", "refusal": "PRIVATE"}}, "LLM_RESPONSE_REJECTED"),
            ({"message": {"content": "{}", "tool_calls": [{}]}}, "LLM_RESPONSE_REJECTED"),
            ({"message": {"content": "PRIVATE prose"}}, "LLM_INVALID_JSON"),
            ({"message": {"content": '{"x":1,"x":2}'}}, "LLM_INVALID_JSON"),
            ({"message": {"content": None}}, "LLM_INVALID_JSON"),
        ]
        for fields, code in cases:
            self.client._opener.open.return_value = http_response({}, **fields)
            with self.subTest(code=code), self.assertRaises(LLMError) as caught:
                self.call()
            self.assertEqual(caught.exception.code, code)

    def test_real_input_is_blocked_before_transport(self):
        value = sample()
        value["data_kind"] = "REAL"
        with self.assertRaises(LLMError) as caught:
            recommend(value, user_id="user-demo", llm=self.client)
        self.assertEqual(caught.exception.code, "LLM_REAL_DATA_NOT_ENABLED")
        self.client._opener.open.assert_not_called()

    def test_arbitrary_question_cannot_be_sent_by_marking_it_synthetic(self):
        value = sample()
        value["question"]["text"] = "PRIVATE non-fixture question"
        with self.assertRaises(LLMError) as caught:
            recommend(value, user_id="user-demo", llm=self.client)
        self.assertEqual(caught.exception.code, "LLM_SYNTHETIC_SAMPLE_REQUIRED")
        self.client._opener.open.assert_not_called()

    def test_unapproved_episode_is_rejected_before_any_paid_call(self):
        value = sample()
        value["episodes"][0]["facts"][0]["text"] = "PRIVATE unapproved experience"
        with self.assertRaises(LLMError) as caught:
            recommend(value, user_id="user-demo", llm=self.client)
        self.assertEqual(caught.exception.code, "LLM_SYNTHETIC_SAMPLE_REQUIRED")
        self.client._opener.open.assert_not_called()

    def test_modified_fact_or_extra_text_is_blocked_by_content_allowlist(self):
        stub = StubClient()
        recommend(sample(), user_id="user-demo", llm=stub)
        original = stub.requests[-1]
        changes = (
            lambda p: p["candidates"][0]["facts"][0].update(text="PRIVATE"),
            lambda p: p["candidates"][0].update(title="PRIVATE"),
            lambda p: p["candidates"][0].update(episode_id="PRIVATE"),
            lambda p: p.update(owner_id="PRIVATE"),
            lambda p: p["candidates"][0]["facts"][0].update(extra="PRIVATE"),
        )
        for change in changes:
            call = copy.deepcopy(original)
            change(call["payload"])
            with self.assertRaises(LLMError) as caught:
                self.client.complete_json(**call)
            self.assertEqual(caught.exception.code, "LLM_SYNTHETIC_SAMPLE_REQUIRED")
        self.client._opener.open.assert_not_called()

    def test_allowlist_covers_exactly_the_reviewed_fixture_content(self):
        from epick_w4.evidence_extraction import source_units

        expected = {"questions": [], "episodes": {}}
        for name in ("w4_collaboration.json", "w4_learning.json", "w4_semantic.json"):
            value = sample(name)
            expected["questions"].append(content_hash(value["question"]["text"]))
            for episode in value["episodes"]:
                header = {"episode_id": episode["episode_id"], "episode_version": episode["version"],
                          "title": episode["title"]}
                expected["episodes"][content_hash(header)] = [
                    content_hash({key: fact[key] for key in ("fact_id", "kind", "text")})
                    for fact in episode["facts"]]
        raw = json.loads((ROOT / "samples/extraction/raw-experiences.synthetic.json").read_text(encoding="utf-8"))
        for episode in raw["episodes"]:
            header = {"episode_id": episode["episode_id"], "episode_version": episode["version"],
                      "title": episode["title"]}
            key = content_hash(header)
            allowed = set(expected["episodes"].get(key, []))
            for unit in source_units(episode["raw_text"]):
                for kind in ("ROLE", "ACTION", "RESULT"):
                    allowed.add(content_hash({
                        "fact_id": f"extract:{episode['episode_id']}:v{episode['version']}:{unit['unit_id']}:{kind.lower()}",
                        "kind": kind, "text": unit["text"],
                    }))
            expected["episodes"][key] = sorted(allowed)
        actual = json.loads((ROOT / "epick_w4/synthetic_allowlist.json").read_text(encoding="utf-8"))
        self.assertEqual(actual, expected)

    def test_cli_refuses_existing_output_before_creating_paid_client(self):
        with patch("epick_w4.solar_client.SolarClient.from_env") as factory, \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            status = main(["--input", str(ROOT / "samples/w4_semantic.json"),
                           "--user-id", "user-demo", "--engine", "solar",
                           "--output", str(ROOT / "README.md")])
            self.assertEqual(status, 2)
            self.assertEqual(json.loads(stderr.getvalue())["error"], "OUTPUT_ALREADY_EXISTS")
            factory.assert_not_called()

    def test_cli_missing_key_is_safe_and_creates_no_output(self):
        with patch.dict("os.environ", {}, clear=True), \
                patch("sys.stderr", new_callable=io.StringIO) as stderr, \
                patch("epick_w4.solar_client.build_opener") as opener:
            status = main(["--input", str(ROOT / "samples/w4_semantic.json"),
                           "--user-id", "user-demo", "--engine", "solar"])
            self.assertEqual(status, 2)
            self.assertEqual(json.loads(stderr.getvalue())["error"], "LLM_API_KEY_MISSING")
            opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
