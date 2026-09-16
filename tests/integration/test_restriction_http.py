import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from test_restriction_contract import event, parse
from test_restriction_runtime import index_request
from w3_knowledge.restriction.contracts import Event
from w3_knowledge.restriction.http import make_server
from w3_knowledge.restriction.store import Store
from w3_knowledge.restriction.w4 import W4Cache

TOKENS = {"w2": "w2-test-token", "operator": "operator-test-token", "w4": "w4-test-token"}


@pytest.fixture
def running(tmp_path):
    with Store(tmp_path / "http.sqlite", clock=lambda: "2026-09-13T01:00:00Z") as store:
        server = make_server(store, TOKENS, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        yield store, f"http://127.0.0.1:{server.server_port}"
        server.shutdown()
        worker.join(timeout=3)
        server.server_close()


def call(base, path, role="w4", payload=None):
    request = Request(
        base + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": f"Bearer {TOKENS.get(role, 'wrong')}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=3) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def test_http_real_consumer_fts_and_durable_w4_delivery(running, tmp_path):
    store, base = running
    assert call(base, "/health", "bad")[0] == 200
    assert call(base, "/v1/events", "bad", event())[0] == 401
    assert call(base, "/v1/events", "w4", event())[0] == 403
    assert call(base, "/v1/events", "w2", event())[1]["receipt"] == "COMMITTED"
    assert call(base, "/v1/index", "operator", index_request().model_dump())[1]["index_ack"] is True
    assert len(call(base, "/v1/search?q=semiconductor")[1]["results"]) == 1
    signals = call(base, "/v1/signals")[1]["signals"]
    with W4Cache(tmp_path / "w4.sqlite") as cache:
        for signal in signals:
            cache.apply(signal)
            assert call(base, "/v1/signals/ack", "w4", {"signal_id": signal["signal_id"]})[0] == 200
        ready_signal = signals[-1]
        cache.put("source-a", ready_signal["generation"], {"summary": "derived result"})
        assert (
            cache.get("source-a", lambda source: call(base, f"/v1/status/{source}")[1]) is not None
        )
        call(base, "/v1/events", "w2", event(2, "RESTRICTED"))
        # Safe even before the new invalidation signal has been consumed.
        assert cache.get("source-a", lambda source: call(base, f"/v1/status/{source}")[1]) is None
        assert call(base, "/v1/search?q=semiconductor")[1]["results"] == []
        restricted_signal = call(base, "/v1/signals")[1]["signals"][-1]
        assert cache.apply(restricted_signal) == "APPLIED"
        assert cache.apply(ready_signal) == "STALE"
        assert cache.apply(restricted_signal) == "DUPLICATE"
    with W4Cache(tmp_path / "w4.sqlite") as restarted:
        assert restarted.apply(ready_signal) == "STALE"
        assert restarted.get("source-a", store.status) is None


def test_http_invalid_payload_is_sanitized_and_does_not_change_state(running):
    _, base = running
    status, body = call(base, "/v1/events", "w2", {**event(), "secret": "never-echo-me"})
    assert status == 422
    assert "never-echo-me" not in json.dumps(body)
    assert call(base, "/v1/status/source-a")[1]["reason"] == "UNKNOWN_SOURCE"


def test_w4_same_generation_conflict_and_unavailable_status_fail_closed(running, tmp_path):
    store, _ = running
    store.consume(parse(Event, event()))
    store.index(index_request())
    signal = store.signals()[-1]
    with W4Cache(tmp_path / "cache.sqlite") as cache:
        cache.apply(signal)
        cache.put("source-a", signal["generation"], {"cached": True})
        changed = {**signal, "usable": False, "reason": "RESTRICTED"}
        assert cache.apply(changed) == "CONFLICT"
        assert cache.get("source-a", store.status) is None
        assert cache.apply(signal) == "CONFLICT"
        with pytest.raises(ValueError):
            cache.put("source-a", signal["generation"], {"cached": True})


def test_http_replay_and_snapshot_have_separate_authorization(running):
    _, base = running
    snapshot = {
        "schema_version": "w3-restriction/0.1-draft",
        "as_of": "2026-09-13T01:00:00Z",
        "event": event(3),
    }
    assert call(base, "/v1/snapshot", "w4", snapshot)[0] == 403
    assert call(base, "/v1/snapshot", "w2", snapshot)[1]["history_complete"] is False
    assert call(base, "/v1/purge", "w2", {})[0] == 403
    assert call(base, "/v1/purge", "operator", {})[0] == 200


def test_missing_or_shared_role_tokens_are_rejected(running):
    store, _ = running
    with pytest.raises(ValueError):
        make_server(store, {"w2": "same", "w4": "same", "operator": "same"}, port=0)


def test_concurrent_w4_connections_cannot_roll_back_newer_signal(running, tmp_path):
    store, _ = running
    store.consume(parse(Event, event()))
    store.index(index_request())
    store.consume(parse(Event, event(2, "RESTRICTED")))
    first, ready, restricted = store.signals()
    path = tmp_path / "concurrent-w4.sqlite"
    connected, go, waiting = threading.Event(), threading.Event(), threading.Event()
    outcomes = []

    def consume_ready():
        with W4Cache(path) as other:
            other.db.set_trace_callback(
                lambda sql: waiting.set() if sql.startswith("BEGIN") else None
            )
            connected.set()
            assert go.wait(timeout=3)
            outcomes.append(other.apply(ready))

    with W4Cache(path) as cache:
        cache.apply(first)
        worker = threading.Thread(target=consume_ready)
        worker.start()
        assert connected.wait(timeout=3)
        cache.db.execute("BEGIN IMMEDIATE")
        go.set()
        assert waiting.wait(timeout=3)
        cache.apply(restricted)
        cache.db.commit()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert outcomes == ["STALE"]
