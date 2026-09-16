"""Durable W4-side cache gate; host application must adopt and call this adapter."""

import argparse
import json
import os
import sqlite3
from contextlib import contextmanager
from urllib.request import Request, urlopen

from .contracts import Signal, Status


class W4Cache:
    signal_model = Signal
    status_model = Status

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS state (
                source TEXT PRIMARY KEY, generation INTEGER, payload TEXT,
                conflicted INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS cache (source TEXT PRIMARY KEY, generation INTEGER, payload TEXT);
        """)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    @contextmanager
    def transaction(self):
        if self.db.in_transaction:
            yield
        else:
            self.db.execute("BEGIN IMMEDIATE")
            with self.db:
                yield

    def apply(self, payload):
        signal = self.signal_model.model_validate_json(json.dumps(payload))
        canonical = signal.model_dump_json()
        with self.transaction():
            row = self.db.execute(
                "SELECT generation,payload,conflicted FROM state WHERE source=?",
                (signal.source_id,),
            ).fetchone()
            if row and signal.generation < row[0]:
                return "STALE"
            if row and signal.generation == row[0]:
                if row[1] != canonical or row[2]:
                    self.db.execute("DELETE FROM cache WHERE source=?", (signal.source_id,))
                    self.db.execute(
                        "UPDATE state SET conflicted=1 WHERE source=?", (signal.source_id,)
                    )
                    return "CONFLICT"
                return "DUPLICATE"
            self.db.execute("DELETE FROM cache WHERE source=?", (signal.source_id,))
            self.db.execute(
                "INSERT OR REPLACE INTO state VALUES (?, ?, ?, 0)",
                (signal.source_id, signal.generation, canonical),
            )
        return "APPLIED"

    def put(self, source, generation, payload):
        with self.transaction():
            row = self.db.execute(
                "SELECT generation,payload,conflicted FROM state WHERE source=?", (source,)
            ).fetchone()
            if not row or row[0] != generation or row[2] or not json.loads(row[1])["usable"]:
                raise ValueError("CACHE_NOT_USABLE")
            self.db.execute(
                "INSERT OR REPLACE INTO cache VALUES (?, ?, ?)",
                (source, generation, json.dumps(payload)),
            )

    def get(self, source, current_status):
        with self.transaction():
            return self._get(source, current_status)

    def _get(self, source, current_status):
        row = self.db.execute(
            "SELECT c.generation,c.payload,s.conflicted FROM cache c JOIN state s ON c.source=s.source WHERE c.source=?",
            (source,),
        ).fetchone()
        if not row:
            return None
        try:
            current = self.status_model.model_validate_json(
                json.dumps(current_status(source))
            ).model_dump()
            valid = (
                not row[2]
                and current["source_id"] == source
                and current["generation"] == row[0]
                and current["index_ack"] is True
            )
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
            valid = False
        if not valid:
            self.db.execute("DELETE FROM cache WHERE source=?", (source,))
            return None
        return json.loads(row[1])


def request(base, token, path, payload=None):
    req = Request(
        base.rstrip("/") + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urlopen(req, timeout=5) as response:
        return json.load(response)


def drain(base, token, cache):
    count = 0
    for signal in request(base, token, "/v1/signals")["signals"]:
        outcome = cache.apply(signal)
        if outcome == "CONFLICT":
            raise ValueError("SIGNAL_CONFLICT_OPERATOR_REQUIRED")
        request(base, token, "/v1/signals/ack", {"signal_id": signal["signal_id"]})
        count += 1
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Consume one page of W3 signals into a durable W4 adapter"
    )
    parser.add_argument("--url", default="http://127.0.0.1:8763")
    parser.add_argument("--db", required=True)
    args = parser.parse_args(argv)
    token = os.environ.get("W3_W4_TOKEN", "")
    if not token:
        print('{"error":"W3_W4_TOKEN_REQUIRED"}')
        return 2
    try:
        with W4Cache(args.db) as cache:
            count = drain(args.url, token, cache)
        print(json.dumps({"consumed": count}))
        return 0
    except (ValueError, OSError, sqlite3.Error, KeyError):
        print('{"error":"SIGNAL_DELIVERY_FAILED"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
