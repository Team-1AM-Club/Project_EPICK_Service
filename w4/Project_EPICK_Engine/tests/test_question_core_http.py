"""HTTP wire, terminal/retry behavior and restarted outbox checks; no W1/AWS calls."""

import hashlib
import io
import json
import unittest
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from epick_w4.question_core_contract import QuestionCoreError, canonical_json
from epick_w4.question_core_http import (
    BASE_URL,
    RESOLVE_PATH,
    SCHEMA_VERSION,
    W1HttpContexts,
    W1HttpSettings,
)
from epick_w4.question_core_relay import QuestionCoreRelay
from epick_w4.question_core_runtime import ROOT, runtime_contract
from examples.w4_question_core_local import LocalAcceptance, LocalClock, local_producer, read

CONTEXT_KEY = "00000000-0000-4000-8000-000000000001"


def context_value(**changes):
    return {
        **read("context.json"),
        "schema_version": SCHEMA_VERSION,
        "context_key": CONTEXT_KEY,
        "authorization_revision": "sha256:" + "a" * 64,
        **changes,
    }


class Response(io.BytesIO):
    def __init__(self, body, *, status=200, content_type="application/json"):
        super().__init__(body)
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type


class SequenceOpener:
    def __init__(self, *values):
        self.values, self.requests = list(values), []

    def open(self, request, *, timeout):
        self.requests.append((request, timeout))
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, dict):
            return Response(canonical_json(value))
        return value


def http_error(status):
    return HTTPError(BASE_URL, status, "private error detail", Message(), io.BytesIO(b"SECRET"))


class QuestionCoreHttpTests(unittest.TestCase):
    def contexts(self, *values):
        opener = SequenceOpener(*values)
        return W1HttpContexts(W1HttpSettings("SYNTHETIC-NOT-A-SECRET"), opener=opener), opener

    def code(self, expected, callback):
        with self.assertRaisesRegex(QuestionCoreError, "^" + expected + "$"):
            callback()

    def test_route_headers_body_and_uncached_requests(self):
        contexts, opener = self.contexts(context_value(), context_value())
        self.assertEqual(contexts.load(CONTEXT_KEY).context_key, CONTEXT_KEY)
        contexts.load(CONTEXT_KEY)
        self.assertEqual(len(opener.requests), 2)
        request, timeout = opener.requests[0]
        self.assertEqual(request.full_url, BASE_URL + RESOLVE_PATH)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(timeout, 5)
        self.assertEqual(
            json.loads(request.data),
            {
                "schema_version": SCHEMA_VERSION,
                "context_key": CONTEXT_KEY,
            },
        )
        headers = {k.lower(): v for k, v in request.header_items()}
        self.assertEqual(headers["authorization"], "Bearer SYNTHETIC-NOT-A-SECRET")
        self.assertEqual(headers["x-epick-service-principal"], "w4")
        self.assertNotIn("SYNTHETIC-NOT-A-SECRET", repr(contexts.settings))

    def test_only_fixed_private_origin_principal_and_safe_bearer_are_accepted(self):
        for changes in (
            {"base_url": "https://example.com"},
            {"base_url": BASE_URL + "/"},
            {"principal": "w2"},
            {"bearer": ""},
            {"bearer": "x\r\nHeader:y"},
            {"timeout_seconds": 0},
            {"timeout_seconds": float("nan")},
        ):
            with self.subTest(changes=changes):
                self.code(
                    "CORE_CONTEXT_CONFIG_INVALID",
                    lambda: W1HttpContexts(W1HttpSettings(**{"bearer": "SYNTHETIC", **changes})),
                )

    def test_bad_context_key_is_rejected_before_http(self):
        contexts, opener = self.contexts()
        for key in ("job-1", None, CONTEXT_KEY + "\n"):
            self.code("CORE_CONTEXT_REQUEST_INVALID", lambda: contexts.load(key))
        self.assertEqual(opener.requests, [])

    def test_401_and_403_latch_until_adapter_is_reconfigured(self):
        for status in (401, 403):
            contexts, opener = self.contexts(http_error(status), context_value())
            for _ in range(2):
                self.code("CORE_CONTEXT_AUTH_FAILED", lambda: contexts.load(CONTEXT_KEY))
            self.assertEqual(len(opener.requests), 1)
        fresh, _ = self.contexts(context_value())
        self.assertTrue(fresh.load(CONTEXT_KEY).processing_allowed)

    def test_documented_terminal_statuses_do_not_expose_error_body(self):
        for status, code in (
            (404, "CORE_CONTEXT_NOT_FOUND"),
            (422, "CORE_CONTEXT_REQUEST_INVALID"),
        ):
            contexts, _ = self.contexts(http_error(status))
            self.code(code, lambda: contexts.load(CONTEXT_KEY))

    def test_503_and_network_failures_remain_retryable_without_internal_retry(self):
        for error in (http_error(503), URLError("SECRET"), TimeoutError("SECRET")):
            contexts, opener = self.contexts(error, context_value())
            self.code("CORE_CURRENTNESS_UNAVAILABLE", lambda: contexts.load(CONTEXT_KEY))
            self.assertEqual(len(opener.requests), 1)
            self.assertTrue(contexts.load(CONTEXT_KEY).processing_allowed)

    def test_redirect_and_unexpected_status_are_terminal(self):
        for status in (301, 302, 307, 308, 429, 500, 204):
            contexts, _ = self.contexts(http_error(status))
            self.code("CORE_CONTEXT_RESPONSE_INVALID", lambda: contexts.load(CONTEXT_KEY))

    def test_closed_response_rejects_missing_extra_wrong_type_and_bad_hash(self):
        missing = context_value()
        missing.pop("schema_version")
        for value in (
            missing,
            context_value(owner_user_id="private"),
            context_value(schema_version="other"),
            context_value(processing_allowed=1),
            context_value(authorization_revision="epoch-1"),
            context_value(current_decision_version=True),
            context_value(valid_until="2026-09-20T00:00:00"),
        ):
            contexts, _ = self.contexts(value)
            self.code("CORE_CONTEXT_RESPONSE_INVALID", lambda: contexts.load(CONTEXT_KEY))

    def test_bad_content_type_duplicate_keys_size_and_json_are_rejected(self):
        for response in (
            Response(b"{}", content_type="text/html"),
            Response(b"{"),
            Response(b'{"schema_version":"a","schema_version":"b"}'),
            Response(b" " * (16 * 1024 + 1)),
            Response(b'{"n":NaN}'),
        ):
            contexts, _ = self.contexts(response)
            self.code("CORE_CONTEXT_RESPONSE_INVALID", lambda: contexts.load(CONTEXT_KEY))

    def test_response_key_must_match_request(self):
        contexts, _ = self.contexts(
            context_value(context_key="00000000-0000-4000-8000-000000000002")
        )
        self.code("CORE_CONTEXT_KEY_MISMATCH", lambda: contexts.load(CONTEXT_KEY))

    def test_source_bytes_match_reviewed_commit(self):
        folder = ROOT / "samples/question-core-w1-context-20260919"
        pin = json.loads((folder / "source-provenance.json").read_bytes())
        self.assertEqual(pin["w1_full_sha"], runtime_contract().source.w1_full_sha)
        for name, evidence in pin["files"].items():
            self.assertEqual(
                hashlib.sha256((folder / name).read_bytes()).hexdigest(), evidence["sha256"]
            )
        for name, expected in pin["input_documents"].items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected)
        runtime_contract().require_adopted()

    def test_real_loopback_http_roundtrip_and_redirect_does_not_forward_bearer(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                received.append(
                    (
                        self.path,
                        self.headers.get("Authorization"),
                        json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                    )
                )
                if len(received) == 1:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(canonical_json(context_value()))
                else:
                    self.send_response(307)
                    self.send_header("Location", "/must-not-follow")
                    self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            from epick_w4.question_core_http import _NoRedirect

            wire = build_opener(ProxyHandler({}), _NoRedirect())

            class LoopbackRoute:
                def open(self, request, *, timeout):
                    routed = Request(
                        f"http://127.0.0.1:{server.server_port}" + RESOLVE_PATH,
                        data=request.data,
                        headers=dict(request.header_items()),
                        method="POST",
                    )
                    return wire.open(routed, timeout=timeout)

            adapter = W1HttpContexts(W1HttpSettings("SYNTHETIC"), opener=LoopbackRoute())
            self.assertTrue(adapter.load(CONTEXT_KEY).processing_allowed)
            self.code("CORE_CONTEXT_RESPONSE_INVALID", lambda: adapter.load(CONTEXT_KEY))
            self.assertEqual(len(received), 2)
            self.assertEqual(received[0][0], RESOLVE_PATH)
            self.assertEqual(received[0][1], "Bearer SYNTHETIC")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


class QuestionCoreHttpOutboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "outbox.sqlite3"
        self.clock = LocalClock()

    def producer(self, *values):
        opener = SequenceOpener(*values)
        producer = local_producer(
            self.path,
            contexts=W1HttpContexts(W1HttpSettings("SYNTHETIC"), opener=opener),
            clock=self.clock,
            contract=runtime_contract(),
        )
        return producer, opener

    def prepare(self, producer):
        return producer.prepare(submission_key="http-submit", context_key=CONTEXT_KEY)

    def test_false_revoked_expired_real_never_creates_outbox(self):
        for changes in (
            {"processing_allowed": False},
            {"revoked": True},
            {"question_current": False},
            {"source_active": False},
            {"data_kind": "REAL"},
            {"valid_until": "2026-09-18T00:00:00Z"},
        ):
            producer, _ = self.producer(context_value(**changes))
            with self.assertRaises(QuestionCoreError):
                self.prepare(producer)
            self.assertIsNone(producer.store.get_submission("http-submit"))

    def test_restart_reloads_http_then_sends_exact_committed_body(self):
        first, first_http = self.producer(context_value())
        saved = self.prepare(first)
        restarted, second_http = self.producer(context_value())
        sender = LocalAcceptance()
        result = QuestionCoreRelay(producer=restarted, sender=sender, clock=self.clock).run_once()
        self.assertEqual(result["status"], "SENT")
        self.assertEqual(sender.bodies, [saved.body])
        self.assertEqual(len(first_http.requests), 1)
        self.assertEqual(len(second_http.requests), 1)

    def test_503_release_keeps_body_and_next_attempt_resolves_again(self):
        producer, opener = self.producer(context_value(), http_error(503), context_value())
        saved = self.prepare(producer)
        sender = LocalAcceptance()
        relay = QuestionCoreRelay(producer=producer, sender=sender, clock=self.clock)
        self.assertEqual(relay.run_once()["status"], "RETRY")
        self.assertEqual(sender.bodies, [])
        self.clock.advance()
        self.assertEqual(relay.run_once()["status"], "SENT")
        self.assertEqual(sender.bodies, [saved.body])
        self.assertEqual(len(opener.requests), 3)
        self.assertEqual(producer.store.get_submission("http-submit"), saved)

    def test_revoked_changed_expired_or_terminal_context_blocks_restart_send(self):
        for n, value in enumerate(
            (
                context_value(processing_allowed=False),
                context_value(revoked=True),
                context_value(authorization_revision="sha256:" + "b" * 64),
                context_value(analysis_input_version="changed-input"),
                context_value(valid_until="2026-09-18T00:00:00Z"),
                http_error(401),
                http_error(404),
                http_error(422),
            )
        ):
            self.path = Path(self.temp.name) / f"blocked-{n}.sqlite3"
            first, _ = self.producer(context_value())
            saved = self.prepare(first)
            restarted, opener = self.producer(value)
            sender = LocalAcceptance()
            result = QuestionCoreRelay(
                producer=restarted, sender=sender, clock=self.clock
            ).run_once()
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(sender.bodies, [])
            self.assertEqual(len(opener.requests), 1)
            self.assertEqual(restarted.store.inspect(saved.message_id)["state"], "blocked")

    def test_uncertain_sqs_retry_rechecks_context_and_preserves_ids_and_body(self):
        producer, opener = self.producer(context_value(), context_value(), context_value())
        saved = self.prepare(producer)
        sender = LocalAcceptance(lose_responses=1)
        relay = QuestionCoreRelay(producer=producer, sender=sender, clock=self.clock)
        self.assertEqual(relay.run_once()["status"], "RETRY")
        self.clock.advance()
        self.assertEqual(relay.run_once()["status"], "SENT")
        self.assertEqual(sender.bodies, [saved.body, saved.body])
        self.assertEqual(len(opener.requests), 3)


if __name__ == "__main__":
    unittest.main()
