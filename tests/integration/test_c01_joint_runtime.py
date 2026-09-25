import copy
import threading
from http.server import HTTPServer
from uuid import UUID, uuid4

import pytest

from test_c01 import (
    SOURCE,
    SourceAuthority,
    allowed_version,
    fixture,
    index_request,
    parse,
    released,
    snapshot_value,
    store_at,
)
from test_c01_source_authority_http import AuthorityHandler
from test_restriction_http import TOKENS, call
from w3_knowledge.c01.source_authority_http import HTTPSourceAuthority


@pytest.fixture
def authority_server():
    AuthorityHandler.response_status = 200
    AuthorityHandler.response_body = {"source_id": SOURCE, "registered": True}
    AuthorityHandler.delay_seconds = 0.0
    AuthorityHandler.trickle_seconds = 0.0
    AuthorityHandler.seen = []
    server = HTTPServer(("127.0.0.1", 0), AuthorityHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    worker.join(timeout=3)
    server.server_close()


def _serve(store):
    from w3_knowledge.c01.http import make_server

    server = make_server(store, TOKENS, port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    return server, worker, f"http://127.0.0.1:{server.server_port}"


@pytest.mark.parametrize("case", ["false", "503", "timeout"])
def test_authority_false_unavailable_and_timeout_never_commit(case, authority_server, tmp_path):
    if case == "false":
        AuthorityHandler.response_body = {"source_id": SOURCE, "registered": False}
        expected = (422, "SOURCE_NOT_REGISTERED")
        authority = HTTPSourceAuthority(authority_server, "synthetic-token")
    elif case == "503":
        AuthorityHandler.response_status = 503
        expected = (503, "SOURCE_AUTHORITY_UNAVAILABLE")
        authority = HTTPSourceAuthority(authority_server, "synthetic-token")
    else:
        AuthorityHandler.delay_seconds = 0.2
        expected = (503, "SOURCE_AUTHORITY_UNAVAILABLE")
        authority = HTTPSourceAuthority(
            authority_server,
            "synthetic-token",
            read_timeout_seconds=0.02,
            total_timeout_seconds=0.05,
        )

    with store_at(tmp_path / f"authority-{case}.sqlite", authority=authority) as store:
        server, worker, base = _serve(store)
        try:
            code, body = call(base, "/c01/v1/events", "w2", allowed_version())
            assert (code, body["error"]) == expected
            assert body["index_ack"] is False
            assert store.db.execute("SELECT count(*) FROM c01_events").fetchone() == (0,)
        finally:
            server.shutdown()
            worker.join(timeout=3)
            server.server_close()


def test_restriction_clear_requires_exact_reindex_before_ready(tmp_path):
    with store_at(tmp_path / "restriction.sqlite") as store:
        store.consume(parse(allowed_version()))
        assert store.index(index_request(store))["reason"] == "READY"
        active = released(2, 1)
        active["payload"]["restriction_status"] = "active"
        assert store.consume(parse(active))["reason"] == "RESTRICTED"
        assert store.search("cloud") == []
        assert store.consume(parse(released(3, 2)))["reason"] == "INDEX_PENDING"
        assert store.status(SOURCE)["index_ack"] is False
        assert store.index(index_request(store))["reason"] == "READY"


def test_replacement_source_must_be_authoritative(tmp_path):
    replacement = uuid4()
    authority = SourceAuthority(allowed={UUID(SOURCE)})
    event = released(2, 1)
    event["payload"]["replacement_ref"] = str(replacement)
    with store_at(tmp_path / "replacement.sqlite", authority=authority) as store:
        server, worker, base = _serve(store)
        try:
            assert call(base, "/c01/v1/events", "w2", allowed_version())[0] == 200
            assert call(base, "/c01/v1/events", "w2", event)[0] == 422
            assert store.status(SOURCE)["event_cursor"] == 1
            authority.allowed.add(replacement)
            assert call(base, "/c01/v1/events", "w2", event)[0] == 200
        finally:
            server.shutdown()
            worker.join(timeout=3)
            server.server_close()


def test_restart_preserves_ready_cursor_restriction_generation_and_key(tmp_path):
    path = tmp_path / "restart.sqlite"
    with store_at(path) as store:
        store.consume(parse(allowed_version()))
        before = store.index(index_request(store))
        assert before["reason"] == "READY"
    with store_at(path) as store:
        after = store.status(SOURCE)
        for field in (
            "event_cursor",
            "restriction_revision",
            "generation",
            "index_key",
            "reason",
            "index_ack",
        ):
            assert after[field] == before[field]


def test_duplicate_gap_replay_and_conflict_are_monotonic(tmp_path):
    from w3_knowledge.c01.contracts import Replay, VERSION

    with store_at(tmp_path / "recovery.sqlite") as store:
        version = parse(allowed_version())
        assert store.consume(version)["event_cursor"] == 1
        assert store.consume(version)["outcome"] == "DUPLICATE"
        third = released(3, 2)
        assert store.consume(parse(third))["reason"] == "EVENT_GAP"
        second = released(2, 1)
        second["payload"]["restriction_status"] = "active"
        replay = Replay.model_validate(
            {
                "schema_version": VERSION,
                "source_id": SOURCE,
                "after_cursor": 1,
                "high_watermark": 3,
                "retention_floor_cursor": 0,
                "events": [second],
            }
        )
        recovered = store.replay(replay)
        assert (recovered["event_cursor"], recovered["restriction_revision"]) == (3, 2)
        changed = copy.deepcopy(allowed_version())
        changed["payload"]["title"] = "mutated immutable fact"
        assert store.consume(parse(changed))["reason"] == "CONFLICT"
        assert store.status(SOURCE)["index_ack"] is False


def test_snapshot_recovery_requires_reindex_and_keeps_history_incomplete(tmp_path):
    with store_at(tmp_path / "snapshot.sqlite") as store:
        store.consume(parse(allowed_version()))
        snapshot = snapshot_value(3, restrictions=[released(3, 2)], restriction_revision=2)
        result = store.snapshot(snapshot)
        assert result["outcome"] == "SNAPSHOT_APPLIED"
        assert result["history_complete"] is False
        assert result["index_ack"] is False
        assert store.index(index_request(store))["index_ack"] is True


def test_private_job_owner_fields_never_enter_public_c01(tmp_path):
    private = fixture("version-available")
    private["payload"]["authenticated_owner_ref"] = str(uuid4())
    with store_at(tmp_path / "private.sqlite") as store:
        server, worker, base = _serve(store)
        try:
            code, body = call(base, "/c01/v1/events", "w2", private)
            assert code == 422
            assert body == {"error": "CONTRACT_INVALID", "index_ack": False}
            assert store.db.execute("SELECT count(*) FROM c01_events").fetchone() == (0,)
        finally:
            server.shutdown()
            worker.join(timeout=3)
            server.server_close()
