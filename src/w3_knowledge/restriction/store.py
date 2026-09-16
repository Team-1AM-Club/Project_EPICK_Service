"""Durable restriction consumer and real SQLite FTS index for local integration."""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, UTC
from pathlib import Path
from uuid import uuid4

from .contracts import VERSION, Event, IndexRequest, ReplayBatch, Signal, Snapshot


def utc_now():
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class Store:
    def __init__(self, path: str | Path, *, consumer_id="w3-restriction", clock=utc_now):
        self.path, self.clock, self.consumer_id = path, clock, consumer_id
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS identity (consumer TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS history (
                event_id TEXT PRIMARY KEY, source TEXT NOT NULL, revision INTEGER NOT NULL,
                payload TEXT NOT NULL, semantic TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS receipt (
                consumer TEXT, event_id TEXT, PRIMARY KEY(consumer, event_id));
            CREATE TABLE IF NOT EXISTS sources (source TEXT PRIMARY KEY, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS conflicts (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS indexed (
                source TEXT PRIMARY KEY, key TEXT NOT NULL, revision INTEGER,
                expires TEXT NOT NULL, scope TEXT NOT NULL);
            CREATE VIRTUAL TABLE IF NOT EXISTS documents USING fts5(
                source UNINDEXED, document_id UNINDEXED, evidence_id UNINDEXED, text);
            CREATE TABLE IF NOT EXISTS outbox (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE,
                payload TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0);
        """)
        identity = self.db.execute("SELECT consumer FROM identity").fetchone()
        if identity and identity[0] != consumer_id:
            self.db.close()
            raise ValueError("CONSUMER_DATABASE_MISMATCH")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO identity VALUES (?)", (consumer_id,))

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    @contextmanager
    def transaction(self):
        """Acquire SQLite's writer lock before reading mutable state, across processes."""
        with self.lock:
            if self.db.in_transaction:
                yield
            else:
                self.db.execute("BEGIN IMMEDIATE")
                with self.db:
                    yield

    @contextmanager
    def _index_transaction(self):
        self.db.execute("SAVEPOINT index_write")
        try:
            yield
        except sqlite3.Error:
            # Some SQLite faults abort the whole transaction and remove savepoints.
            if self.db.in_transaction:
                self.db.execute("ROLLBACK TO index_write")
                self.db.execute("RELEASE index_write")
            raise
        else:
            self.db.execute("RELEASE index_write")

    def _state(self, source):
        row = self.db.execute("SELECT state FROM sources WHERE source=?", (source,)).fetchone()
        return (
            json.loads(row[0])
            if row
            else {
                "revision": 0,
                "required": 0,
                "generation": 0,
                "payload": None,
                "conflict": False,
                "history_complete": True,
                "index_error": None,
            }
        )

    def _save(self, source, state):
        self.db.execute("INSERT OR REPLACE INTO sources VALUES (?, ?)", (source, encode(state)))

    def _clear_index(self, source):
        self.db.execute("DELETE FROM documents WHERE source=?", (source,))
        self.db.execute("DELETE FROM indexed WHERE source=?", (source,))

    def _status(self, source, state):
        payload = state["payload"]
        key = payload["index_key"] if payload else None
        indexed = self.db.execute(
            "SELECT key, revision, expires FROM indexed WHERE source=?", (source,)
        ).fetchone()
        if state["conflict"]:
            reason = "CONFLICT"
        elif state["required"] > state["revision"]:
            reason = "EVENT_GAP"
        elif payload is None:
            reason = "UNKNOWN_SOURCE"
        elif payload["status"] == "RESTRICTED":
            reason = "RESTRICTED"
        elif key is None:
            reason = "VERSION_UNAVAILABLE"
        elif state["index_error"]:
            reason = state["index_error"]
        elif not indexed:
            reason = "INDEX_PENDING"
        elif json.loads(indexed[0]) != key or indexed[1] != state["revision"]:
            reason = "INDEX_MISMATCH"
        elif indexed[2] <= self.clock():
            reason = "BODY_EXPIRED"
        else:
            reason = "READY"
        return {
            "schema_version": VERSION,
            "source_id": source,
            "restriction_revision": state["revision"],
            "required_revision": state["required"],
            "generation": state["generation"],
            "reason": reason,
            "index_ack": reason == "READY",
            "index_key": key,
            "history_complete": state["history_complete"],
        }

    def _emit(self, source, state):
        state["generation"] += 1
        self._save(source, state)
        status = self._status(source, state)
        signal = Signal(
            schema_version=VERSION,
            event_type="w3.source.usability.changed",
            signal_id=f"sig-{uuid4()}",
            source_id=source,
            generation=state["generation"],
            restriction_revision=state["revision"],
            required_revision=state["required"],
            usable=status["index_ack"],
            reason=status["reason"],
            index_key=status["index_key"],
            history_complete=state["history_complete"],
        )
        self.db.execute(
            "INSERT INTO outbox(id,payload) VALUES (?, ?)",
            (signal.signal_id, signal.model_dump_json()),
        )

    def _quarantine(self, source, state, value, revision):
        state["conflict"] = True
        state["required"] = max(state["required"], revision)
        self._clear_index(source)
        self.db.execute("INSERT INTO conflicts VALUES (?, ?)", (str(uuid4()), encode(value)))
        self._emit(source, state)

    def _consume(self, event):
        source, revision = event.aggregate_id, event.aggregate_revision
        value = event.model_dump(mode="json")
        canonical, semantic = encode(value), encode(value["payload"])
        state = self._state(source)
        same_id = self.db.execute(
            "SELECT payload,source,revision FROM history WHERE event_id=?", (event.event_id,)
        ).fetchone()
        revisions = self.db.execute(
            "SELECT semantic FROM history WHERE source=? AND revision=?", (source, revision)
        ).fetchall()
        if (same_id and same_id[0] != canonical) or any(row[0] != semantic for row in revisions):
            self._quarantine(source, state, value, revision)
            if same_id and same_id[1] != source:
                self._quarantine(same_id[1], self._state(same_id[1]), value, same_id[2])
            return "CONFLICT"
        self.db.execute(
            "INSERT OR IGNORE INTO history VALUES (?, ?, ?, ?, ?)",
            (event.event_id, source, revision, canonical, semantic),
        )
        if self.db.execute(
            "SELECT 1 FROM receipt WHERE consumer=? AND event_id=?",
            (self.consumer_id, event.event_id),
        ).fetchone():
            return "DUPLICATE"
        state["required"] = max(state["required"], revision)
        if state["conflict"]:
            self._save(source, state)
            return "CONFLICT"
        if revision > state["revision"] + 1:
            self._clear_index(source)
            self._emit(source, state)
            return "GAP"
        if revision == state["revision"] and state["payload"] != value["payload"]:
            self._quarantine(source, state, value, revision)
            return "CONFLICT"
        outcome = "STALE"
        if revision > state["revision"]:
            self._clear_index(source)
            state.update(revision=revision, payload=value["payload"], index_error=None)
            self._emit(source, state)
            outcome = "APPLIED"
        self._save(source, state)
        self.db.execute("INSERT INTO receipt VALUES (?, ?)", (self.consumer_id, event.event_id))
        return outcome

    def consume(self, event: Event):
        with self.transaction():
            outcome = self._consume(event)
            result = self._status(event.aggregate_id, self._state(event.aggregate_id))
        return {**result, "receipt": "COMMITTED", "event_id": event.event_id, "outcome": outcome}

    def replay(self, batch: ReplayBatch):
        with self.transaction():
            source = batch.source_id
            state = self._state(source)
            if batch.after_revision > state["revision"]:
                return {**self._status(source, state), "outcome": "CURSOR_AHEAD"}
            if batch.high_watermark > state["required"]:
                state["required"] = batch.high_watermark
                self._clear_index(source)
                self._emit(source, state)
            for event in batch.events:
                if event.published_at > batch.retention_start:
                    self._consume(event)
            state = self._state(source)
            outcome = (
                "REPLAYED"
                if state["revision"] >= state["required"] and not state["conflict"]
                else "SNAPSHOT_REQUIRED"
            )
            return {**self._status(source, state), "outcome": outcome}

    def snapshot(self, snapshot: Snapshot):
        event = snapshot.event
        source, revision = event.aggregate_id, event.aggregate_revision
        with self.transaction():
            state = self._state(source)
            minimum = max(state["revision"] + int(state["conflict"]), state["required"])
            if revision < minimum:
                return {**self._status(source, state), "outcome": "STALE_SNAPSHOT"}
            semantic = encode(event.payload.model_dump(mode="json"))
            canonical = encode(event.model_dump(mode="json"))
            same_id = self.db.execute(
                "SELECT payload,source,revision FROM history WHERE event_id=?", (event.event_id,)
            ).fetchone()
            old = self.db.execute(
                "SELECT semantic FROM history WHERE source=? AND revision=?", (source, revision)
            ).fetchall()
            if (
                (same_id and same_id[0] != canonical)
                or any(row[0] != semantic for row in old)
                or (
                    revision == state["revision"]
                    and state["payload"] != event.payload.model_dump(mode="json")
                )
            ):
                self._quarantine(source, state, snapshot.model_dump(mode="json"), revision)
                if same_id and same_id[1] != source:
                    self._quarantine(
                        same_id[1],
                        self._state(same_id[1]),
                        snapshot.model_dump(mode="json"),
                        same_id[2],
                    )
                return {**self._status(source, state), "outcome": "CONFLICT"}
            self._clear_index(source)
            state.update(
                revision=revision,
                required=revision,
                payload=event.payload.model_dump(mode="json"),
                conflict=False,
                history_complete=False,
                index_error=None,
            )
            self.db.execute(
                "INSERT INTO snapshots VALUES (?, ?)", (str(uuid4()), snapshot.model_dump_json())
            )
            self.db.execute(
                "INSERT OR IGNORE INTO history VALUES (?, ?, ?, ?, ?)",
                (event.event_id, source, revision, canonical, semantic),
            )
            self._emit(source, state)
            return {**self._status(source, state), "outcome": "SNAPSHOT_APPLIED"}

    def index(self, request: IndexRequest):
        source = request.source_id
        with self.transaction():
            self.purge()
            state = self._state(source)
            status = self._status(source, state)
            if status["reason"] in {
                "UNKNOWN_SOURCE",
                "EVENT_GAP",
                "CONFLICT",
                "RESTRICTED",
                "VERSION_UNAVAILABLE",
            }:
                return status
            key = request.index_key.model_dump()
            error = None
            if (
                key != state["payload"]["index_key"]
                or request.restriction_revision != state["revision"]
            ):
                error = "INDEX_MISMATCH"
            elif (
                request.retention_scope == "none"
                or not request.documents
                or request.expires_at <= self.clock()
            ):
                error = "BODY_EXPIRED"
            try:
                with self._index_transaction():
                    self._clear_index(source)
                    state["index_error"] = error
                    if error is None:
                        self.db.executemany(
                            "INSERT INTO documents VALUES (?, ?, ?, ?)",
                            [
                                (source, d.document_id, d.evidence_id, d.text)
                                for d in request.documents
                            ],
                        )
                        self.db.execute(
                            "INSERT INTO indexed VALUES (?, ?, ?, ?, ?)",
                            (
                                source,
                                encode(key),
                                state["revision"],
                                request.expires_at,
                                request.retention_scope,
                            ),
                        )
                    self._emit(source, state)
            except sqlite3.Error:
                # A full SQLite rollback also releases the writer lock. Reacquire it
                # before reading current state; never reuse the pre-failure snapshot.
                with self.transaction():
                    state = self._state(source)
                    state["index_error"] = "INDEX_FAILED"
                    self._emit(source, state)
                    return self._status(source, state)
            return self._status(source, state)

    def purge(self):
        with self.transaction():
            expired = self.db.execute(
                "SELECT source FROM indexed WHERE expires<=?", (self.clock(),)
            ).fetchall()
            for (source,) in expired:
                self._clear_index(source)
                state = self._state(source)
                state["index_error"] = "BODY_EXPIRED"
                self._emit(source, state)
            return {"purged_sources": len(expired)}

    def status(self, source):
        with self.transaction():
            self.purge()
            return self._status(source, self._state(source))

    def search(self, query):
        with self.transaction():
            self.purge()
            if not query.strip():
                return []
            # Treat input as a literal FTS phrase, never executable MATCH syntax.
            phrase = '"' + query.replace('"', '""') + '"'
            rows = self.db.execute(
                "SELECT source,document_id,evidence_id,text FROM documents WHERE documents MATCH ?",
                (phrase,),
            ).fetchall()
            result = []
            for source, document_id, evidence_id, text in rows:
                status = self._status(source, self._state(source))
                if status["index_ack"]:
                    result.append(
                        {
                            "source_id": source,
                            "document_id": document_id,
                            "evidence_id": evidence_id,
                            "text": text,
                            "generation": status["generation"],
                            "index_key": status["index_key"],
                        }
                    )
                if len(result) >= 100:
                    break
            return result

    def signals(self):
        with self.transaction():
            self.purge()
            return [
                json.loads(row[0])
                for row in self.db.execute(
                    "SELECT payload FROM outbox WHERE delivered=0 ORDER BY sequence LIMIT 500"
                )
            ]

    def acknowledge(self, signal_id):
        with self.transaction():
            row = self.db.execute("SELECT id FROM outbox WHERE id=?", (signal_id,)).fetchone()
            if not row:
                return False
            self.db.execute("UPDATE outbox SET delivered=1 WHERE id=?", (signal_id,))
            return True
