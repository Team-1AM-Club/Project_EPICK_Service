import json
import threading
import sqlite3

import pytest

from test_restriction_contract import event, parse
from w3_knowledge.restriction.contracts import Event, IndexRequest, ReplayBatch, Snapshot
from w3_knowledge.restriction.store import Store


def index_request(value=None):
    value = value or event()
    return parse(
        IndexRequest,
        {
            "schema_version": "w3-restriction/0.1-draft",
            "source_id": value["aggregate_id"],
            "restriction_revision": value["aggregate_revision"],
            "index_key": value["payload"]["index_key"],
            "retention_scope": "excerpts_only",
            "expires_at": "2026-09-14T00:00:00Z",
            "documents": [
                {
                    "document_id": "doc-a",
                    "evidence_id": "evidence-a",
                    "text": "synthetic semiconductor evidence",
                }
            ],
        },
    )


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "w3.sqlite", clock=lambda: "2026-09-13T01:00:00Z") as value:
        yield value


def ready(store):
    store.consume(parse(Event, event()))
    assert store.index(index_request())["index_ack"] is True
    assert len(store.search("semiconductor")) == 1


def test_real_fts_restriction_release_requires_reindex_and_preserves_history(store):
    ready(store)
    assert store.consume(parse(Event, event(2, "RESTRICTED")))["receipt"] == "COMMITTED"
    assert store.search("semiconductor") == []
    assert store.db.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
    assert store.status("source-a")["index_ack"] is False
    store.consume(parse(Event, event(3)))
    assert store.status("source-a")["reason"] == "INDEX_PENDING"
    assert store.index(index_request(event(3)))["index_ack"] is True
    assert store.db.execute("SELECT count(*) FROM history").fetchone()[0] == 3


def test_persisted_dedup_and_stale_release_cannot_expose_text(store):
    ready(store)
    store.consume(parse(Event, event(2, "RESTRICTED")))
    assert store.consume(parse(Event, event(1)))["outcome"] == "DUPLICATE"
    assert store.consume(parse(Event, event(1, event_id="late")))["outcome"] == "STALE"
    path = store.path
    with Store(path, clock=store.clock) as restarted:
        assert restarted.search("semiconductor") == []
        assert restarted.consume(parse(Event, event(2, "RESTRICTED")))["outcome"] == "DUPLICATE"


@pytest.mark.parametrize("reuse_id", [False, True])
def test_conflicting_event_quarantines_until_newer_snapshot(store, reuse_id):
    ready(store)
    conflict = event(1, "RESTRICTED", "evt-1" if reuse_id else "conflict")
    assert store.consume(parse(Event, conflict))["outcome"] == "CONFLICT"
    assert store.search("semiconductor") == []
    assert (
        store.snapshot(
            parse(
                Snapshot,
                {
                    "schema_version": "w3-restriction/0.1-draft",
                    "as_of": "2026-09-13T01:00:00Z",
                    "event": event(),
                },
            )
        )["outcome"]
        == "STALE_SNAPSHOT"
    )
    assert (
        store.snapshot(
            parse(
                Snapshot,
                {
                    "schema_version": "w3-restriction/0.1-draft",
                    "as_of": "2026-09-13T01:00:00Z",
                    "event": event(2),
                },
            )
        )["reason"]
        == "INDEX_PENDING"
    )


def batch(events, cutoff="2026-09-12T00:00:00Z"):
    return parse(
        ReplayBatch,
        {
            "schema_version": "w3-restriction/0.1-draft",
            "source_id": "source-a",
            "after_revision": 1,
            "high_watermark": 3,
            "retention_start": cutoff,
            "events": events,
        },
    )


def test_gap_replay_and_expiry_require_actual_reindex(store):
    ready(store)
    assert store.consume(parse(Event, event(3)))["reason"] == "EVENT_GAP"
    assert store.search("semiconductor") == []
    assert store.replay(batch([event(2, "RESTRICTED"), event(3)]))["outcome"] == "REPLAYED"
    assert store.status("source-a")["reason"] == "INDEX_PENDING"
    assert store.index(index_request(event(3)))["index_ack"] is True


def test_expired_replay_snapshot_marks_history_gap(store):
    ready(store)
    store.consume(parse(Event, event(3)))
    result = store.replay(batch([event(2, "RESTRICTED"), event(3)], "2026-09-13T00:00:01Z"))
    assert result["outcome"] == "SNAPSHOT_REQUIRED"
    assert result["index_ack"] is False
    result = store.snapshot(
        parse(
            Snapshot,
            {
                "schema_version": "w3-restriction/0.1-draft",
                "as_of": "2026-09-13T01:00:00Z",
                "event": event(3),
            },
        )
    )
    assert result["history_complete"] is False
    assert result["index_ack"] is False
    assert store.index(index_request(event(3)))["index_ack"] is True


@pytest.mark.parametrize(
    "field",
    ["source_version_id", "extraction_revision_id", "representation", "normalization_version"],
)
def test_index_mismatch_fails_closed_for_each_key(store, field):
    ready(store)
    request = index_request().model_dump()
    request["index_key"][field] = "html" if field == "representation" else "other"
    assert store.index(parse(IndexRequest, request))["reason"] == "INDEX_MISMATCH"
    assert store.search("semiconductor") == []
    assert store.index(index_request())["index_ack"] is True


def test_real_sql_failure_blocks_ack_preserves_authoritative_event_and_retries(store):
    ready(store)
    store.db.executescript(
        "CREATE TRIGGER fail_index BEFORE INSERT ON indexed BEGIN SELECT RAISE(ABORT, 'disk write failed'); END;"
    )
    assert store.index(index_request())["reason"] == "INDEX_FAILED"
    assert store.status("source-a")["index_ack"] is False
    assert store.search("semiconductor") == []
    assert (
        json.loads(store.db.execute("SELECT payload FROM history LIMIT 1").fetchone()[0])[
            "payload"
        ]["index_key"]["source_version_id"]
        == "version-a"
    )
    store.db.execute("DROP TRIGGER fail_index")
    assert store.index(index_request())["index_ack"] is True


def test_expiry_purges_fts_and_emits_same_revision_new_generation(store):
    ready(store)
    before = store.status("source-a")
    store.clock = lambda: "2026-09-14T00:00:00Z"
    after = store.status("source-a")
    assert after["reason"] == "BODY_EXPIRED"
    assert after["generation"] > before["generation"]
    assert store.search("semiconductor") == []
    assert store.db.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
    assert store.signals()[-1]["usable"] is False


def test_snapshot_same_revision_cannot_rewrite_immutable_restriction(store):
    ready(store)
    conflicting = parse(
        Snapshot,
        {
            "schema_version": "w3-restriction/0.1-draft",
            "as_of": "2026-09-13T01:00:00Z",
            "event": event(1, "RESTRICTED"),
        },
    )
    assert store.snapshot(conflicting)["outcome"] == "CONFLICT"
    assert store.status("source-a")["index_ack"] is False


def test_replay_with_lower_watermark_cannot_hide_a_known_larger_gap(store):
    ready(store)
    store.consume(parse(Event, event(5)))
    result = store.replay(batch([event(2), event(3)]))
    assert result["outcome"] == "SNAPSHOT_REQUIRED"
    assert result["reason"] == "EVENT_GAP"


def test_snapshot_cannot_reuse_an_event_id_for_different_revision(store):
    ready(store)
    snapshot = parse(
        Snapshot,
        {
            "schema_version": "w3-restriction/0.1-draft",
            "as_of": "2026-09-13T01:00:00Z",
            "event": event(2, event_id="evt-1"),
        },
    )
    assert store.snapshot(snapshot)["outcome"] == "CONFLICT"
    assert store.search("semiconductor") == []


def test_shared_database_serializes_state_read_with_event_commit(store):
    ready(store)
    with Store(store.path, clock=store.clock) as other:
        waiting = threading.Event()
        outcomes = []

        def trace(sql):
            if sql.startswith("BEGIN IMMEDIATE") or sql.startswith("INSERT OR IGNORE INTO history"):
                waiting.set()

        other.db.set_trace_callback(trace)
        store.db.execute("BEGIN IMMEDIATE")
        worker = threading.Thread(
            target=lambda: outcomes.append(other.consume(parse(Event, event(3))))
        )
        worker.start()
        assert waiting.wait(timeout=3)
        store.consume(parse(Event, event(2, "RESTRICTED")))
        store.db.commit()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert outcomes[0]["outcome"] == "APPLIED"
        assert store.status("source-a")["restriction_revision"] == 3
        assert store.status("source-a")["index_ack"] is False


def test_concurrent_index_cannot_undo_another_connections_restriction(store):
    ready(store)
    with Store(store.path, clock=store.clock) as other:
        waiting = threading.Event()
        outcomes = []

        def trace(sql):
            if sql.startswith("BEGIN IMMEDIATE") or sql.startswith("DELETE FROM documents"):
                waiting.set()

        other.db.set_trace_callback(trace)
        store.db.execute("BEGIN IMMEDIATE")
        worker = threading.Thread(target=lambda: outcomes.append(other.index(index_request())))
        worker.start()
        assert waiting.wait(timeout=3)
        store.consume(parse(Event, event(2, "RESTRICTED")))
        store.db.commit()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert outcomes[0]["reason"] == "RESTRICTED"
        assert store.status("source-a")["restriction_revision"] == 2
        assert store.search("semiconductor") == []


def test_full_sql_rollback_cannot_overwrite_concurrent_restriction(store, monkeypatch):
    ready(store)
    with Store(store.path, clock=store.clock) as other:
        other.db.execute("PRAGMA busy_timeout=1")
        store.db.executescript(
            "CREATE TRIGGER full_rollback BEFORE INSERT ON indexed BEGIN SELECT RAISE(ROLLBACK, 'transaction aborted'); END;"
        )
        original = store._state
        reads = 0
        blocked = False

        def interleave(source):
            # Schedule a real competing writer after the failure handler's SQL read.
            # Return the original DB result unchanged; no fake consumer/index result.
            nonlocal reads, blocked
            state = original(source)
            reads += 1
            if reads == 2:
                try:
                    other.consume(parse(Event, event(2, "RESTRICTED")))
                except sqlite3.OperationalError as error:
                    assert "locked" in str(error)
                    blocked = True
            return state

        monkeypatch.setattr(store, "_state", interleave)
        assert store.index(index_request())["index_ack"] is False
        if blocked:
            other.consume(parse(Event, event(2, "RESTRICTED")))
        store.db.execute("DROP TRIGGER full_rollback")
        assert store.index(index_request())["index_ack"] is False
        assert store.status("source-a")["restriction_revision"] == 2
        assert store.search("semiconductor") == []
