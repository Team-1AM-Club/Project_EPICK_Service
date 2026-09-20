"""Durable C01 signal handling and scoped recommendation caching for W4."""

from contextlib import contextmanager
import json
import sqlite3
import threading
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .c01_adapter import status_of, wire
from .c01_contract import COMMIT, PROFILE, timestamp
from .synthetic_policy import content_hash


class C01Error(RuntimeError):
    def __init__(self, code, status=503):
        self.code, self.status = code, status
        super().__init__(code)


class C01Consumer:
    """One logical consumer; host must authorize every cached-result read."""

    def __init__(self, path, *, transport, max_cache_ttl_seconds, clock=time.time):
        if type(max_cache_ttl_seconds) is not int or max_cache_ttl_seconds <= 0:
            raise ValueError("C01_CACHE_TTL_REQUIRED")
        self.transport, self.clock, self.max_ttl = transport, clock, max_cache_ttl_seconds
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        tables = {
            row[0]
            for row in self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if tables and tables != {"config", "sources", "receipts", "results", "dependencies"}:
            self.db.close()
            raise ValueError("C01_CACHE_PROFILE_MISMATCH")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT);
            CREATE TABLE IF NOT EXISTS sources (source TEXT PRIMARY KEY, payload TEXT NOT NULL, conflicted INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, source TEXT NOT NULL, digest TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS results (key TEXT PRIMARY KEY, owner TEXT NOT NULL, project TEXT NOT NULL,
                expires REAL NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS dependencies (key TEXT REFERENCES results(key) ON DELETE CASCADE,
                source TEXT NOT NULL, PRIMARY KEY(key,source));
            CREATE INDEX IF NOT EXISTS by_source ON dependencies(source);
        """)
        config = json.dumps(["w4-c01-consumer/0.1-candidate", PROFILE, COMMIT, self.max_ttl])
        try:
            with self.transaction():
                row = self.db.execute("SELECT value FROM config WHERE id=1").fetchone()
                if (row is None and tables) or (row is not None and row[0] != config):
                    raise ValueError("C01_CACHE_PROFILE_MISMATCH")
                self.db.execute("INSERT OR IGNORE INTO config VALUES (1,?)", (config,))
        except Exception:
            self.db.close()
            raise

    def close(self):
        with self.lock:
            self.db.close()

    @contextmanager
    def transaction(self):
        with self.lock:
            if self.db.in_transaction:
                yield
            else:
                self.db.execute("BEGIN IMMEDIATE")
                with self.db:
                    yield

    def _invalidate(self, source):
        self.db.execute(
            "DELETE FROM results WHERE key IN (SELECT key FROM dependencies WHERE source=?)",
            (source,),
        )

    def invalidate_source(self, source):
        with self.transaction():
            self._invalidate(source)

    def invalidate_scope(self, owner, project):
        with self.transaction():
            self.db.execute("DELETE FROM results WHERE owner=? AND project=?", (owner, project))

    def purge_expired(self):
        with self.transaction():
            return self.db.execute("DELETE FROM results WHERE expires<=?", (self.clock(),)).rowcount

    def apply(self, value):
        signal = wire("signal", value)
        source, generation = signal["source_id"], signal["generation"]
        canonical = json.dumps(signal, sort_keys=True, separators=(",", ":"))
        digest = content_hash(signal)
        with self.transaction():
            self.db.execute("DELETE FROM results WHERE expires<=?", (self.clock(),))
            previous = self.db.execute(
                "SELECT payload,conflicted FROM sources WHERE source=?", (source,)
            ).fetchone()
            receipt = self.db.execute(
                "SELECT source,digest FROM receipts WHERE id=?", (signal["signal_id"],)
            ).fetchone()
            conflict = receipt is not None and receipt != (source, digest)
            old = json.loads(previous[0]) if previous else None
            if old and not conflict:
                if generation < old["generation"]:
                    self.db.execute(
                        "INSERT OR IGNORE INTO receipts VALUES (?,?,?)",
                        (signal["signal_id"], source, digest),
                    )
                    return "STALE"
                if generation == old["generation"]:
                    if previous[0] == canonical and not previous[1]:
                        return "DUPLICATE"
                    conflict = True
                elif any(
                    signal[k] < old[k]
                    for k in (
                        "event_cursor",
                        "required_event_cursor",
                        "restriction_revision",
                        "required_restriction_revision",
                    )
                ):
                    conflict = True
            if conflict:
                for affected in {source, receipt[0] if receipt else source}:
                    self._invalidate(affected)
                    self.db.execute("UPDATE sources SET conflicted=1 WHERE source=?", (affected,))
                if previous is None:
                    self.db.execute("INSERT INTO sources VALUES (?,?,1)", (source, canonical))
                return "CONFLICT"
            self._invalidate(source)
            self.db.execute("INSERT OR REPLACE INTO sources VALUES (?,?,0)", (source, canonical))
            self.db.execute(
                "INSERT OR IGNORE INTO receipts VALUES (?,?,?)",
                (signal["signal_id"], source, digest),
            )
            return "APPLIED"

    def drain(self):
        """Try one page. Retry scheduling and dead-letter handling belong to host."""
        try:
            page = self.transport.signals()
            if (
                not isinstance(page, dict)
                or set(page) != {"signals"}
                or not isinstance(page["signals"], list)
                or len(page["signals"]) > 500
            ):
                raise ValueError("C01_SIGNAL_PAGE_INVALID")
            count = 0
            for value in page["signals"]:
                if self.apply(value) == "CONFLICT":
                    raise C01Error("C01_SIGNAL_CONFLICT", 409)
                # apply() commits invalidation before a delivery receipt is sent.
                wire("delivery-response", self.transport.ack(value["signal_id"]))
                count += 1
            return count
        except C01Error:
            raise
        except Exception:
            raise C01Error("C01_DELIVERY_FAILED") from None

    def _local_matches(self, signal):
        row = self.db.execute(
            "SELECT payload,conflicted FROM sources WHERE source=?", (signal["source_id"],)
        ).fetchone()
        return row is not None and not row[1] and json.loads(row[0]) == signal

    def check_current(self, knowledge):
        for binding in knowledge["sources"]:
            expected = wire("signal", binding["signal"])
            source = expected["source_id"]
            if (
                not expected["usable"]
                or binding["knowledge_generation"] != expected["generation"]
                or binding["metadata"]["source_id"] != source
                or binding["metadata"]["index_key"] != expected["index_key"]
            ):
                self.invalidate_source(source)
                raise C01Error("C01_SOURCE_NOT_READY", 409)
            if timestamp(binding["metadata"]["expires_at"]) <= self.clock():
                self.invalidate_source(source)
                raise C01Error("C01_SOURCE_EXPIRED", 409)
            try:
                current = wire("status", self.transport.status(source))
            except Exception:
                self.invalidate_source(source)
                raise C01Error("C01_STATUS_UNAVAILABLE") from None
            with self.transaction():
                valid = current == status_of(expected) and self._local_matches(expected)
                if not valid:
                    self._invalidate(source)
            if not valid:
                raise C01Error("C01_CONTEXT_CHANGED", 409)

    @staticmethod
    def result_key(owner, context, request):
        return content_hash({"owner": owner, "context": context, "request": request})

    def get_result(self, owner, context, request):
        self.check_current(context["company_knowledge"])
        key = self.result_key(owner, context, request)
        with self.transaction():
            self.db.execute("DELETE FROM results WHERE expires<=?", (self.clock(),))
            row = self.db.execute(
                "SELECT payload FROM results WHERE key=? AND owner=? AND project=?",
                (key, owner, context["project"]["project_id"]),
            ).fetchone()
            return json.loads(row[0]) if row else None

    def put_result(self, owner, context, request, result):
        knowledge = context["company_knowledge"]
        self.check_current(knowledge)
        key = self.result_key(owner, context, request)
        expiry = min(
            self.clock() + self.max_ttl,
            *(timestamp(s["metadata"]["expires_at"]) for s in knowledge["sources"]),
        )
        with self.transaction():
            if expiry <= self.clock() or not all(
                self._local_matches(wire("signal", b["signal"])) for b in knowledge["sources"]
            ):
                raise C01Error("C01_CONTEXT_CHANGED", 409)
            self.db.execute(
                "INSERT OR REPLACE INTO results VALUES (?,?,?,?,?)",
                (
                    key,
                    owner,
                    context["project"]["project_id"],
                    expiry,
                    json.dumps(result, ensure_ascii=False),
                ),
            )
            self.db.executemany(
                "INSERT INTO dependencies VALUES (?,?)",
                [(key, wire("signal", b["signal"])["source_id"]) for b in knowledge["sources"]],
            )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class C01HTTPTransport:
    """Configured service-to-service endpoint; never read its URL/token from users."""

    def __init__(self, base_url, token, *, timeout=5):
        parts = urlsplit(base_url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.path not in {"", "/"}
            or (parts.scheme == "http" and parts.hostname not in {"127.0.0.1", "localhost", "::1"})
        ):
            raise ValueError("C01_ENDPOINT_INVALID")
        if (
            not token
            or not token.isascii()
            or any(char.isspace() or ord(char) < 32 for char in token)
        ):
            raise ValueError("C01_TOKEN_INVALID")
        self.base, self.token, self.timeout = base_url.rstrip("/"), token, timeout
        self.opener = build_opener(_NoRedirect())

    def _request(self, path, payload=None):
        request = Request(
            self.base + "/c01/v1" + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
        )
        with self.opener.open(request, timeout=self.timeout) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError("C01_RESPONSE_TOO_LARGE")
        from .llm_contract import parse_json_object

        return parse_json_object(raw.decode("utf-8"), "c01", allow_fence=False)

    def signals(self):
        return self._request("/signals")

    def status(self, source):
        # Prevent path/query injection even for a host accidentally passing an ID.
        from uuid import UUID

        return self._request("/status/" + str(UUID(source)))

    def ack(self, signal_id):
        return self._request("/signals/ack", wire("delivery-receipt", {"signal_id": signal_id}))
