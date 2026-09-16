"""Offline synthetic validation lab. Not an adopted event or ACK contract."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def _event(value: dict) -> dict:
    fields = {
        "event_id",
        "source_id",
        "revision",
        "blocked",
        "version",
        "extraction",
        "normalization",
    }
    if set(value) != fields:
        raise ValueError("LAB_EVENT_FIELDS_INVALID")
    if type(value["revision"]) is not int or value["revision"] < 1:
        raise ValueError("LAB_REVISION_INVALID")
    if type(value["blocked"]) is not bool:
        raise ValueError("LAB_RESTRICTION_INVALID")
    if any(
        not isinstance(value[k], str) or not value[k].strip()
        for k in fields - {"revision", "blocked"}
    ):
        raise ValueError("LAB_REFERENCE_INVALID")
    return dict(value)


class Lab:
    """Durable single-consumer lab with an independent synthetic producer journal."""

    def __init__(self, database: str | Path = ":memory:", *, consumer_id: str = "lab-w3"):
        self.consumer_id = consumer_id
        self.db = sqlite3.connect(database)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS journal (
                event_id TEXT PRIMARY KEY, source_id TEXT, revision INTEGER,
                at INTEGER, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS receipt (
                consumer TEXT, event_id TEXT, PRIMARY KEY (consumer, event_id)
            );
            CREATE TABLE IF NOT EXISTS source (
                consumer TEXT, source_id TEXT, state TEXT NOT NULL,
                PRIMARY KEY (consumer, source_id)
            );
            CREATE TABLE IF NOT EXISTS body (
                consumer TEXT, source_id TEXT, content TEXT, expires INTEGER,
                PRIMARY KEY (consumer, source_id)
            );
        """)

    def close(self):
        self.db.close()

    def _state(self, source_id):
        row = self.db.execute(
            "SELECT state FROM source WHERE consumer=? AND source_id=?",
            (self.consumer_id, source_id),
        ).fetchone()
        return (
            json.loads(row[0])
            if row
            else {
                "revision": 0,
                "required_revision": 0,
                "blocked": True,
                "expected": None,
                "indexed": None,
                "index_failed": False,
                "limitations": [],
                "body_available": None,
            }
        )

    def _save(self, source_id, state):
        self.db.execute(
            "INSERT OR REPLACE INTO source VALUES (?, ?, ?)",
            (self.consumer_id, source_id, json.dumps(state)),
        )

    def _invalidate_body(self, event, state):
        key = [event[k] for k in ("version", "extraction", "normalization")]
        if key != state["expected"] and state["body_available"] is not None:
            self.db.execute(
                "DELETE FROM body WHERE consumer=? AND source_id=?",
                (self.consumer_id, event["source_id"]),
            )
            state["body_available"] = False

    def publish(self, event, *, at):
        event = _event(event)
        payload = json.dumps(event, sort_keys=True)
        existing = self.db.execute(
            "SELECT payload FROM journal WHERE event_id=?", (event["event_id"],)
        ).fetchone()
        if existing and existing[0] != payload:
            raise ValueError("EVENT_ID_COLLISION")
        previous = self.db.execute(
            "SELECT payload FROM journal WHERE source_id=? AND revision=?",
            (event["source_id"], event["revision"]),
        ).fetchall()
        semantic = {k: v for k, v in event.items() if k != "event_id"}
        if any(
            {k: v for k, v in json.loads(row[0]).items() if k != "event_id"} != semantic
            for row in previous
        ):
            raise ValueError("REVISION_COLLISION")
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO journal VALUES (?, ?, ?, ?, ?)",
                (event["event_id"], event["source_id"], event["revision"], at, payload),
            )

    def consume(self, event, *, now):
        self.publish(event, at=now)
        with self.db:
            if self.db.execute(
                "SELECT 1 FROM receipt WHERE consumer=? AND event_id=?",
                (self.consumer_id, event["event_id"]),
            ).fetchone():
                return "DUPLICATE"
            state = self._state(event["source_id"])
            state["required_revision"] = max(state["required_revision"], event["revision"])
            if event["revision"] > state["revision"] + 1:
                self._save(event["source_id"], state)
                return "GAP"
            outcome = "STALE"
            if event["revision"] > state["revision"]:
                self._invalidate_body(event, state)
                state.update(
                    revision=event["revision"],
                    blocked=event["blocked"],
                    expected=[event[k] for k in ("version", "extraction", "normalization")],
                )
                outcome = "APPLIED"
            self._save(event["source_id"], state)
            self.db.execute(
                "INSERT INTO receipt VALUES (?, ?)", (self.consumer_id, event["event_id"])
            )
            return outcome

    def query(self, source_id, *, now=None):
        if now is not None:
            self.read_body(source_id, now=now)
        state = self._state(source_id)
        if state["revision"] == 0:
            reason = "EVENT_GAP" if state["required_revision"] else "UNKNOWN_SOURCE"
        elif state["required_revision"] > state["revision"]:
            reason = "EVENT_GAP"
        elif state["blocked"]:
            reason = "RESTRICTED"
        elif state["body_available"] is False:
            reason = "BODY_UNAVAILABLE"
        elif state["index_failed"]:
            reason = "INDEX_FAILED"
        elif state["expected"] != state["indexed"]:
            reason = "INDEX_MISMATCH"
        else:
            reason = "READY"
        return {"lab_only": True, "ack": reason == "READY", "reason": reason, **state}

    def index(self, source_id, key, *, fail=False, now=None):
        state = self._state(source_id)
        state["index_failed"] = fail
        state["indexed"] = None if fail else list(key)
        with self.db:
            self._save(source_id, state)
        return self.query(source_id, now=now)

    def replay(self, source_id, *, now, retention):
        if retention <= 0:
            raise ValueError("RETENTION_MUST_BE_POSITIVE")
        state = self._state(source_id)
        events = self.db.execute(
            "SELECT payload FROM journal WHERE source_id=? AND revision>? AND at>? AND at<=? ORDER BY revision, event_id",
            (source_id, state["revision"], now - retention, now),
        ).fetchall()
        for row in events:
            if self.consume(json.loads(row[0]), now=now) == "GAP":
                break
        state = self._state(source_id)
        if state["required_revision"] > state["revision"]:
            return "SNAPSHOT_REQUIRED"
        return "REPLAYED"

    def snapshot(self, event, *, now):
        event = _event(event)
        state = self._state(event["source_id"])
        if event["revision"] < max(state["revision"], state["required_revision"]):
            raise ValueError("STALE_SNAPSHOT")
        if event["revision"] == state["revision"] and (
            event["blocked"] != state["blocked"]
            or [event[k] for k in ("version", "extraction", "normalization")] != state["expected"]
        ):
            raise ValueError("SNAPSHOT_COLLISION")
        with self.db:
            self._invalidate_body(event, state)
            state.update(
                revision=event["revision"],
                required_revision=event["revision"],
                blocked=event["blocked"],
                expected=[event[k] for k in ("version", "extraction", "normalization")],
                indexed=None,
                index_failed=False,
            )
            if "HISTORY_UNAVAILABLE" not in state["limitations"]:
                state["limitations"].append("HISTORY_UNAVAILABLE")
            self._save(event["source_id"], state)
        return self.query(event["source_id"], now=now)

    def retain(self, source_id, *, full, excerpt, scope, now, ttl):
        if scope not in {"FULL", "EXCERPT", "NONE"} or ttl <= 0:
            raise ValueError("BODY_POLICY_INVALID")
        content = full if scope == "FULL" else excerpt if scope == "EXCERPT" else None
        state = self._state(source_id)
        state["body_available"] = content is not None
        with self.db:
            self.db.execute(
                "DELETE FROM body WHERE consumer=? AND source_id=?", (self.consumer_id, source_id)
            )
            if content is not None:
                self.db.execute(
                    "INSERT INTO body VALUES (?, ?, ?, ?)",
                    (self.consumer_id, source_id, content, now + ttl),
                )
            self._save(source_id, state)

    def read_body(self, source_id, *, now):
        row = self.db.execute(
            "SELECT content, expires FROM body WHERE consumer=? AND source_id=?",
            (self.consumer_id, source_id),
        ).fetchone()
        if row is None:
            return None
        state = self._state(source_id)
        if now >= row[1]:
            with self.db:
                self.db.execute(
                    "DELETE FROM body WHERE consumer=? AND source_id=?",
                    (self.consumer_id, source_id),
                )
                state["body_available"] = False
                self._save(source_id, state)
            return None
        if state["blocked"] or state["required_revision"] > state["revision"]:
            return None
        return row[0]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Offline synthetic W3 validation lab; not a production contract"
    )
    parser.add_argument("--scenario", type=Path, required=True)
    parser.add_argument("--db", default=":memory:")
    args = parser.parse_args(argv)
    lab = Lab(args.db)
    results = []
    try:
        scenario = json.loads(args.scenario.read_text(encoding="utf-8"))
        for step in scenario["steps"]:
            operation = step["op"]
            if operation not in {
                "publish",
                "consume",
                "query",
                "index",
                "replay",
                "snapshot",
                "retain",
                "read_body",
            }:
                raise ValueError("LAB_OPERATION_INVALID")
            kwargs = {k: v for k, v in step.items() if k not in {"op", "expect"}}
            actual = getattr(lab, operation)(**kwargs)
            expected = step["expect"]
            passed = (
                all(actual.get(k) == v for k, v in expected.items())
                if isinstance(expected, dict) and isinstance(actual, dict)
                else actual == expected
            )
            results.append({"step": len(results) + 1, "op": operation, "passed": passed})
        success = all(item["passed"] for item in results)
        print(
            json.dumps(
                {
                    "lab_only": True,
                    "passed": sum(item["passed"] for item in results),
                    "total": len(results),
                    "results": results,
                }
            )
        )
        return 0 if success else 1
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
        print(
            json.dumps(
                {"lab_only": True, "error": "INVALID_SCENARIO_OR_STATE", "completed": len(results)}
            )
        )
        return 2
    finally:
        lab.close()


if __name__ == "__main__":
    raise SystemExit(main())
