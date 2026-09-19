"""Atomic, durable C-01 consumer with separate transport and restriction cursors."""

import copy
import hashlib
import json
import sqlite3
from datetime import datetime
from uuid import UUID, uuid4

from ..restriction.store import Store as BaseStore, encode, utc_now
from .contracts import Event, IndexKey, Signal, VERSION


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def text_digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_ids(value, field=""):
    if isinstance(value, dict):
        return {key: normalize_ids(item, key) for key, item in value.items()}
    if isinstance(value, list):
        return [
            normalize_ids(item, "evidence_id" if field == "evidence_ids" else field)
            for item in value
        ]
    if isinstance(value, str) and field.endswith("_id"):
        return str(UUID(value))
    return value


def projection(event):
    value = normalize_ids(event.model_dump(mode="json"))
    if event.event_type == "source.version.available":
        p = value["payload"]
        for evidence in p["evidence_spans"]:
            evidence["text_sha256"] = text_digest(evidence.pop("text_excerpt"))
        for section in p["posting_sections"]:
            section.pop("text_raw")
    return value


class Store(BaseStore):
    def __init__(self, path, *, restriction_scope, max_ttl_seconds, clock=utc_now):
        if (
            restriction_scope != "version"
            or type(max_ttl_seconds) is not int
            or max_ttl_seconds <= 0
        ):
            raise ValueError("EXPLICIT_SCOPE_AND_POSITIVE_TTL_REQUIRED")
        self.scope, self.max_ttl_seconds = restriction_scope, max_ttl_seconds
        super().__init__(path, consumer_id="w3-c01/0.2-candidate/r2", clock=clock)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS c01_settings (id INTEGER PRIMARY KEY, scope TEXT, ttl INTEGER);
            CREATE TABLE IF NOT EXISTS c01_events (
                event_id TEXT PRIMARY KEY, source TEXT, revision INTEGER, hash TEXT,
                projection TEXT, UNIQUE(source,revision));
            CREATE TABLE IF NOT EXISTS c01_checkpoints (
                source TEXT PRIMARY KEY, cursor INTEGER, hash TEXT);
            CREATE TABLE IF NOT EXISTS c01_registered_sources (source TEXT PRIMARY KEY);
        """)
        try:
            with self.transaction():
                row = self.db.execute("SELECT scope,ttl FROM c01_settings WHERE id=1").fetchone()
                if row and row != (restriction_scope, max_ttl_seconds):
                    raise ValueError("DATABASE_CONFIGURATION_MISMATCH")
                self.db.execute(
                    "INSERT OR IGNORE INTO c01_settings VALUES (1,?,?)",
                    (restriction_scope, max_ttl_seconds),
                )
        except Exception:
            self.db.close()
            raise

    def _state(self, source):
        row = self.db.execute("SELECT state FROM sources WHERE source=?", (source,)).fetchone()
        return (
            json.loads(row[0])
            if row
            else dict(
                cursor=0,
                required=0,
                restriction=0,
                required_restriction=0,
                generation=0,
                versions={},
                restrictions={},
                observation=None,
                conflict=False,
                history_complete=True,
                index_error=None,
            )
        )

    def _version(self, state):
        return max(state["versions"].values(), key=lambda v: v["revision"], default=None)

    def _key(self, version):
        if not version:
            return None
        p = version["payload"]
        return IndexKey(
            source_version_id=p["source_version_id"],
            extraction_revision_id=p["extraction_revision_id"],
            representation=p["metadata"]["representation"],
            normalization_version=p["metadata"]["normalization_version"],
        ).model_dump()

    def _status(self, source, state):
        version = self._version(state)
        key = self._key(version)
        active = [
            v["payload"]
            for v in state["restrictions"].values()
            if v["payload"]["restriction_status"] == "active"
            or v["payload"]["accuracy_status"] in {"error_confirmed", "superseded"}
        ]
        restricted = any(
            p["source_version_id"] is None
            or (key and p["source_version_id"] == key["source_version_id"])
            for p in active
        )
        observation = state["observation"]
        indexed = self.db.execute(
            "SELECT key,revision,expires FROM indexed WHERE source=?", (source,)
        ).fetchone()
        policy = version["payload"]["policy"] if version else {}
        if state["conflict"]:
            reason = "CONFLICT"
        elif state["cursor"] < state["required"]:
            reason = "EVENT_GAP"
        elif state["restriction"] < state["required_restriction"]:
            reason = "RESTRICTION_GAP"
        elif restricted:
            reason = "RESTRICTED"
        elif not version:
            reason = "UNKNOWN_SOURCE"
        elif (
            observation
            and observation["revision"] > version["revision"]
            and (
                observation["payload"]["access_class"] != "public"
                or observation["payload"]["acquisition_status"]
                not in {"AVAILABLE", "PARTIALLY_EXTRACTED"}
            )
        ):
            reason = "OBSERVATION_BLOCKED"
        elif (
            policy.get("official_status") != "verified"
            or policy.get("access_class") != "public"
            or any(
                policy.get(k) != "allowed"
                for k in (
                    "collection_permission",
                    "excerpt_storage_permission",
                    "redistribution_permission",
                )
            )
            or version["payload"]["accuracy_status"] in {"error_confirmed", "superseded"}
        ):
            reason = "POLICY_BLOCKED"
        elif state["index_error"]:
            reason = state["index_error"]
        elif not indexed:
            reason = "INDEX_PENDING"
        elif json.loads(indexed[0]) != key or indexed[1] != state["cursor"]:
            reason = "INDEX_MISMATCH"
        elif indexed[2] <= self.clock():
            reason = "BODY_EXPIRED"
        else:
            reason = "READY"
        return dict(
            schema_version=VERSION,
            source_id=source,
            event_cursor=state["cursor"],
            required_event_cursor=state["required"],
            restriction_revision=state["restriction"],
            required_restriction_revision=state["required_restriction"],
            generation=state["generation"],
            restriction_scope=self.scope,
            reason=reason,
            index_ack=reason == "READY",
            index_key=key,
            history_complete=state["history_complete"],
        )

    def _emit(self, source, state):
        state["generation"] += 1
        self._save(source, state)
        status = self._status(source, state)
        signal = Signal(
            **status,
            signal_id=str(uuid4()),
            event_type="w3.source.usability.changed",
            usable=status["index_ack"],
        )
        self.db.execute(
            "INSERT INTO outbox(id,payload) VALUES (?,?)",
            (signal.signal_id, signal.model_dump_json()),
        )

    def _block_conflict(self, source, required):
        state = self._state(source)
        state["required"] = max(state["required"], required)
        state["conflict"] = True
        self._clear_index(source)
        self._emit(source, state)

    def _restriction_scope_conflict(self, source, incoming):
        scopes = {}
        identifiers = {
            v["payload"]["restriction_id"]
            for v in incoming
            if v["event_type"] == "source.restriction.changed"
        }
        if not identifiers:
            return False
        known = [json.loads(row[0]) for row in self.db.execute("SELECT projection FROM c01_events")]
        for value in [*known, *incoming]:
            if value["event_type"] != "source.restriction.changed":
                continue
            payload = value["payload"]
            key = payload["restriction_id"]
            if key not in identifiers:
                continue
            scope = (payload["source_id"], payload["source_version_id"])
            if key in scopes and scopes[key] != scope:
                return True
            scopes[key] = scope
        return False

    def _apply(self, state, value):
        p = value["payload"]
        if value["event_type"] == "source.version.available":
            state["versions"][p["source_version_id"]] = value
        elif value["event_type"] == "source.observation.changed":
            state["observation"] = value
        else:
            revision = p["restriction_revision"]
            state["required_restriction"] = max(state["required_restriction"], revision)
            if revision != state["restriction"] + 1:
                if revision <= state["restriction"]:
                    state["conflict"] = True
                # A missing restriction cannot be inferred from a later cleared state.
                return
            state["restriction"] = revision
            state["restrictions"][p["restriction_id"]] = value

    def _consume(self, event):
        # Revalidate nested mutable dicts even when caller hands us an existing model.
        event = Event.model_validate(event.model_dump(mode="json"))
        self._validate_replacement(event)
        source = event.aggregate_id.lower()
        original_hash = digest(event.model_dump(mode="json"))
        same_id = self.db.execute(
            "SELECT source,revision,hash FROM c01_events WHERE event_id=?",
            (event.event_id.lower(),),
        ).fetchone()
        same_revision = self.db.execute(
            "SELECT hash FROM c01_events WHERE source=? AND revision=?", (source, event.revision)
        ).fetchone()
        if (same_id and same_id[2] != original_hash) or (
            same_revision and same_revision[0] != original_hash
        ):
            self._block_conflict(source, event.revision)
            if same_id and same_id[0] != source:
                self._block_conflict(same_id[0], same_id[1])
            return "CONFLICT"
        if same_id:
            return "DUPLICATE"
        state = self._state(source)
        if event.revision <= state["cursor"]:
            return "STALE"
        if self._restriction_scope_conflict(source, [projection(event)]):
            self._block_conflict(source, event.revision)
            return "CONFLICT"
        self.db.execute(
            "INSERT INTO c01_events VALUES (?,?,?,?,?)",
            (
                event.event_id.lower(),
                source,
                event.revision,
                original_hash,
                encode(projection(event)),
            ),
        )
        self.db.execute("INSERT OR IGNORE INTO c01_registered_sources VALUES (?)", (source,))
        state["required"] = max(state["required"], event.revision)
        if event.event_type == "source.restriction.changed":
            state["required_restriction"] = max(
                state["required_restriction"], event.payload["restriction_revision"]
            )
        while not state["conflict"]:
            row = self.db.execute(
                "SELECT projection FROM c01_events WHERE source=? AND revision=?",
                (source, state["cursor"] + 1),
            ).fetchone()
            if not row:
                break
            self._apply(state, json.loads(row[0]))
            state["cursor"] += 1
        state["index_error"] = None
        self._clear_index(source)
        self._emit(source, state)
        if state["conflict"]:
            return "CONFLICT"
        return (
            "GAP"
            if state["cursor"] < state["required"]
            or state["restriction"] < state["required_restriction"]
            else "APPLIED"
        )

    def consume(self, event):
        with self.transaction():
            outcome = self._consume(event)
            return dict(
                self._status(event.aggregate_id.lower(), self._state(event.aggregate_id.lower())),
                receipt="COMMITTED",
                event_id=event.event_id.lower(),
                outcome=outcome,
            )

    def _validate_replacement(self, event):
        replacement = event.payload.get("replacement_ref")
        if (
            replacement is not None
            and not self.db.execute(
                "SELECT 1 FROM c01_registered_sources WHERE source=?", (replacement.lower(),)
            ).fetchone()
        ):
            raise ValueError("REPLACEMENT_SOURCE_UNREGISTERED")

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
        source = source.lower()
        with self.transaction():
            self.purge()
            return self._status(source, self._state(source))

    def replay(self, batch):
        with self.transaction():
            state = self._state(batch.source_id)
            if batch.after_cursor > state["cursor"]:
                return dict(self._status(batch.source_id, state), outcome="CURSOR_AHEAD")
            state["required"] = max(state["required"], batch.high_watermark)
            self._clear_index(batch.source_id)
            self._emit(batch.source_id, state)
            if state["cursor"] < batch.retention_floor_cursor:
                return dict(self._status(batch.source_id, state), outcome="SNAPSHOT_REQUIRED")
            for event in batch.events:
                self._consume(event)
            state = self._state(batch.source_id)
            status = self._status(batch.source_id, state)
            outcome = (
                "CONFLICT"
                if state["conflict"]
                else (
                    "INCOMPLETE"
                    if state["cursor"] < state["required"]
                    or state["restriction"] < state["required_restriction"]
                    else "REPLAYED"
                )
            )
            return dict(status, outcome=outcome)

    def snapshot(self, snapshot):
        source = snapshot.source_id
        with self.transaction():
            for event in snapshot.restrictions:
                self._validate_replacement(event)
            old = self._state(source)
            if snapshot.event_cursor < max(
                old["cursor"], old["required"]
            ) or snapshot.restriction_revision < max(
                old["restriction"], old["required_restriction"]
            ):
                return dict(self._status(source, old), outcome="STALE_SNAPSHOT")
            snapshot_hash = digest(snapshot.model_dump(mode="json"))
            checkpoint = self.db.execute(
                "SELECT cursor,hash FROM c01_checkpoints WHERE source=?", (source,)
            ).fetchone()
            if (
                checkpoint
                and checkpoint[0] == snapshot.event_cursor
                and checkpoint[1] != snapshot_hash
            ):
                self._block_conflict(source, snapshot.event_cursor)
                return dict(self._status(source, self._state(source)), outcome="CONFLICT")
            events = (
                snapshot.versions
                + snapshot.restrictions
                + ([snapshot.observation] if snapshot.observation else [])
            )
            inconsistent = self._restriction_scope_conflict(source, [projection(e) for e in events])
            for event in events:
                known = self.db.execute(
                    "SELECT hash FROM c01_events WHERE event_id=? OR (source=? AND revision=?)",
                    (event.event_id.lower(), source, event.revision),
                ).fetchall()
                if any(row[0] != digest(event.model_dump(mode="json")) for row in known):
                    inconsistent = True
            # Pending events already received during a transport gap are known facts too.
            known_state = copy.deepcopy(old)
            restriction_facts = {
                e.payload["restriction_revision"]: projection(e) for e in snapshot.restrictions
            }
            for (raw,) in self.db.execute(
                "SELECT projection FROM c01_events WHERE source=? AND revision<=? ORDER BY revision",
                (source, snapshot.event_cursor),
            ):
                value = json.loads(raw)
                p = value["payload"]
                if value["event_type"] == "source.version.available":
                    group, key = "versions", p["source_version_id"]
                elif value["event_type"] == "source.restriction.changed":
                    restriction_revision = p["restriction_revision"]
                    if (
                        restriction_revision in restriction_facts
                        and restriction_facts[restriction_revision] != value
                    ):
                        inconsistent = True
                    restriction_facts[restriction_revision] = value
                    group, key = "restrictions", p["restriction_id"]
                else:
                    if (
                        not known_state["observation"]
                        or known_state["observation"]["revision"] < value["revision"]
                    ):
                        known_state["observation"] = value
                    continue
                if (
                    key not in known_state[group]
                    or known_state[group][key]["revision"] < value["revision"]
                ):
                    known_state[group][key] = value
            restriction_order = [
                value["payload"]["restriction_revision"]
                for value in sorted(restriction_facts.values(), key=lambda v: v["revision"])
            ]
            if restriction_order != sorted(set(restriction_order)):
                inconsistent = True
            for name, incoming, key in [
                ("versions", snapshot.versions, "source_version_id"),
                ("restrictions", snapshot.restrictions, "restriction_id"),
            ]:
                by_id = {e.payload[key].lower(): e for e in incoming}
                for identifier, previous in known_state[name].items():
                    current = by_id.get(identifier)
                    if current is None or current.revision < previous["revision"]:
                        inconsistent = True
                    elif (
                        current.revision == previous["revision"] and projection(current) != previous
                    ):
                        inconsistent = True
            if known_state["observation"] and (
                snapshot.observation is None
                or snapshot.observation.revision < known_state["observation"]["revision"]
            ):
                inconsistent = True
            if inconsistent:
                self._block_conflict(source, snapshot.event_cursor)
                return dict(self._status(source, self._state(source)), outcome="CONFLICT")
            state = dict(
                cursor=snapshot.event_cursor,
                required=snapshot.event_cursor,
                restriction=snapshot.restriction_revision,
                required_restriction=snapshot.restriction_revision,
                generation=old["generation"],
                conflict=False,
                history_complete=False,
                index_error=None,
                versions={},
                restrictions={},
                observation=None,
            )
            for event in snapshot.versions:
                state["versions"][event.payload["source_version_id"].lower()] = projection(event)
            for event in snapshot.restrictions:
                state["restrictions"][event.payload["restriction_id"].lower()] = projection(event)
            if snapshot.observation:
                state["observation"] = projection(snapshot.observation)
            for event in events:
                self.db.execute(
                    "INSERT OR IGNORE INTO c01_events VALUES (?,?,?,?,?)",
                    (
                        event.event_id.lower(),
                        source,
                        event.revision,
                        digest(event.model_dump(mode="json")),
                        encode(projection(event)),
                    ),
                )
            self.db.execute(
                "INSERT OR REPLACE INTO c01_checkpoints VALUES (?,?,?)",
                (source, snapshot.event_cursor, snapshot_hash),
            )
            self.db.execute("INSERT OR IGNORE INTO c01_registered_sources VALUES (?)", (source,))
            self._clear_index(source)
            self._emit(source, state)
            return dict(self._status(source, state), outcome="SNAPSHOT_APPLIED")

    def index(self, request):
        source = request.source_id
        try:
            with self.transaction():
                state = self._state(source)
                status = self._status(source, state)
                if status["reason"] not in {
                    "INDEX_PENDING",
                    "READY",
                    "INDEX_MISMATCH",
                    "INDEX_FAILED",
                    "BODY_EXPIRED",
                }:
                    return status
                version = self._version(state)
                hashes = {
                    e["evidence_id"]: e["text_sha256"] for e in version["payload"]["evidence_spans"]
                }
                seconds = (
                    datetime.fromisoformat(request.expires_at)
                    - datetime.fromisoformat(self.clock())
                ).total_seconds()
                good = (
                    request.event_cursor == state["cursor"]
                    and request.restriction_revision == state["restriction"]
                    and request.index_key.model_dump() == status["index_key"]
                    and 0 < seconds <= self.max_ttl_seconds
                    and all(
                        hashes.get(d.evidence_id) == text_digest(d.text) for d in request.documents
                    )
                )
                with self._index_transaction():
                    self._clear_index(source)
                    state["index_error"] = None if good else "INDEX_MISMATCH"
                    if good:
                        self.db.execute(
                            "INSERT INTO indexed VALUES (?,?,?,?,?)",
                            (
                                source,
                                encode(status["index_key"]),
                                state["cursor"],
                                request.expires_at,
                                request.retention_scope,
                            ),
                        )
                        self.db.executemany(
                            "INSERT INTO documents VALUES (?,?,?,?)",
                            [
                                (source, d.document_id, d.evidence_id, d.text)
                                for d in request.documents
                            ],
                        )
                    self._emit(source, state)
                return self._status(source, state)
        except sqlite3.Error:
            with self.transaction():
                state = self._state(source)
                state["index_error"] = "INDEX_FAILED"
                self._clear_index(source)
                self._emit(source, state)
                return self._status(source, state)

    def search(self, query):
        with self.transaction():
            self.purge()
            rows = self.db.execute(
                "SELECT source,document_id,evidence_id,text FROM documents WHERE documents MATCH ? LIMIT 100",
                (query,),
            ).fetchall()
            return [
                dict(
                    source_id=s,
                    document_id=d,
                    evidence_id=e,
                    text=t,
                    index_key=self._status(s, self._state(s))["index_key"],
                )
                for s, d, e, t in rows
                if self._status(s, self._state(s))["index_ack"]
            ]

    def can_use_texts(self, source, texts):
        source = source.lower()
        with self.transaction():
            self.purge()
            if not self._status(source, self._state(source))["index_ack"]:
                return False
            retained = {
                row[0]
                for row in self.db.execute("SELECT text FROM documents WHERE source=?", (source,))
            }
            return bool(texts) and all(text in retained for text in texts)

    def signals(self):
        with self.transaction():
            self.purge()
            return [
                json.loads(row[0])
                for row in self.db.execute(
                    "SELECT payload FROM outbox WHERE delivered=0 ORDER BY sequence LIMIT 500"
                )
            ]
