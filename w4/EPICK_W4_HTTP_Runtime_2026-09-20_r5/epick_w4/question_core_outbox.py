"""Persistent SQLite producer outbox for one host on a durable local volume.

Decisions and outbound bodies commit together. A committed lease survives a
worker crash; expiration permits the SAME message to be sent again. Never place
an active database on a cloud-synced/network filesystem or share it across hosts.
"""

import json
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from .question_core_contract import QuestionCoreError, canonical_json, digest, require
from .question_core_producer import PreparedDecision

SAFE_ERRORS = frozenset(
    {
        "CORE_CURRENTNESS_UNAVAILABLE",
        "CORE_RECHECK_REJECTED",
        "CORE_SEND_UNCONFIRMED",
        "CORE_CONTEXT_NOT_CURRENT",
        "CORE_CONTEXT_EXPIRED",
        "CORE_CONTEXT_CHANGED",
        "CORE_CONTEXT_KEY_MISMATCH",
        "CORE_CONTEXT_AUTH_FAILED",
        "CORE_CONTEXT_NOT_FOUND",
        "CORE_CONTEXT_REQUEST_INVALID",
        "CORE_CONTEXT_RESPONSE_INVALID",
        "CORE_POLICY_PLAN_INVALID",
        "CORE_POLICY_NOT_APPROVED",
        "CORE_POLICY_CHANGED",
        "CORE_POLICY_REASON_MISMATCH",
        "CORE_REVISION_STALE",
        "CORE_REVISION_REGRESSED",
        "CORE_REAL_DATA_NOT_ENABLED",
        "CORE_TRUSTED_CONTEXT_INVALID",
        "CORE_SCHEMA_CHANGED",
        "CORE_STORED_DECISION_INVALID",
        "CORE_STORED_BODY_CHANGED",
        "CORE_EVENT_CONTEXT_MISMATCH",
        "CORE_TRANSPORT_NOT_ADOPTED",
        "CORE_SEND_DISABLED",
    }
)

_DDL = """
CREATE TABLE IF NOT EXISTS w4_decisions (
    decision_id TEXT PRIMARY KEY,
    submission_key TEXT NOT NULL UNIQUE,
    context_key TEXT NOT NULL,
    job_id TEXT NOT NULL,
    question_version_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    analysis_input_version TEXT NOT NULL,
    decision_version INTEGER NOT NULL CHECK(decision_version > 0),
    context_json TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    schema_sha256 TEXT NOT NULL,
    UNIQUE(job_id, question_version_id, source_id, analysis_input_version, decision_version)
);
CREATE TABLE IF NOT EXISTS w4_outbox (
    message_id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL UNIQUE REFERENCES w4_decisions(decision_id),
    body TEXT NOT NULL,
    body_digest TEXT NOT NULL,
    prepared_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'prepared' CHECK(state IN ('prepared', 'sent', 'blocked')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_attempt_at REAL,
    last_error TEXT,
    sent_at REAL,
    broker_message_id TEXT,
    lease_token TEXT,
    lease_until REAL,
    next_attempt_at REAL NOT NULL DEFAULT 0
);
CREATE TRIGGER IF NOT EXISTS w4_decision_immutable BEFORE UPDATE ON w4_decisions
BEGIN SELECT RAISE(ABORT, 'W4_IMMUTABLE_DECISION'); END;
CREATE TRIGGER IF NOT EXISTS w4_body_immutable
BEFORE UPDATE OF message_id, decision_id, body, body_digest, prepared_at ON w4_outbox
BEGIN SELECT RAISE(ABORT, 'W4_IMMUTABLE_BODY'); END;
"""
_JOIN = "SELECT d.*, o.* FROM w4_decisions d JOIN w4_outbox o USING(decision_id) "


@dataclass(frozen=True)
class DeliveryClaim:
    prepared: PreparedDecision
    token: str
    attempt: int


def _prepared(row):
    return PreparedDecision(**{key: row[key] for key in PreparedDecision.__dataclass_fields__})


def _same_decision(left, right):
    def comparison(item):
        context = json.loads(item.context_json)
        context.pop("valid_until", None)
        context.pop("current_decision_version", None)
        event = json.loads(item.body)
        for key in ("message_id", "decision_id", "occurred_at"):
            event.pop(key, None)
        return item.context_key, context, json.loads(item.policy_json), item.schema_sha256, event

    return comparison(left) == comparison(right)


def _time(value):
    require(
        type(value) in {int, float} and math.isfinite(value) and value >= 0, "CORE_CLOCK_INVALID"
    )
    return float(value)


class QuestionCoreOutbox:
    def __init__(self, path):
        require(
            str(path) not in {"", ":memory:"}
            and not str(path).startswith("file:")
            and Path(path).is_absolute(),
            "CORE_DURABLE_DB_PATH_REQUIRED",
        )
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            require(version in {0, 1}, "CORE_OUTBOX_VERSION_UNSUPPORTED")
            # Do not silently adopt an unrelated existing SQLite database.
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            require(
                not tables or tables == {"w4_decisions", "w4_outbox"},
                "CORE_OUTBOX_DATABASE_MISMATCH",
            )
            require(
                db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal",
                "CORE_DURABLE_DB_REQUIRED",
            )
            db.executescript("BEGIN IMMEDIATE;\n" + _DDL + "\nPRAGMA user_version=1;\nCOMMIT;")

    @contextmanager
    def _connect(self):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            yield db
        except sqlite3.Error:
            raise QuestionCoreError("CORE_OUTBOX_DB_FAILURE") from None
        finally:
            if db is not None:
                if db.in_transaction:
                    db.rollback()
                db.close()

    def get_submission(self, submission_key):
        with self._connect() as db:
            row = db.execute(_JOIN + "WHERE submission_key=?", (submission_key,)).fetchone()
            return _prepared(row) if row else None

    def save(self, prepared):
        event = json.loads(prepared.body)
        require(
            canonical_json(event).decode("utf-8") == prepared.body
            and digest(event) == prepared.body_digest
            and event["message_id"] == prepared.message_id
            and event["decision_id"] == prepared.decision_id
            and event["occurred_at"] == prepared.prepared_at,
            "CORE_STORED_BODY_CHANGED",
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                _JOIN + "WHERE submission_key=?", (prepared.submission_key,)
            ).fetchone()
            if existing:
                value = _prepared(existing)
                if (
                    prepared.message_id == value.message_id
                    or prepared.decision_id == value.decision_id
                ):
                    require(
                        (
                            prepared.message_id,
                            prepared.decision_id,
                            prepared.body,
                            prepared.body_digest,
                        )
                        == (value.message_id, value.decision_id, value.body, value.body_digest),
                        "CORE_ID_OR_REVISION_CONFLICT",
                    )
                require(_same_decision(value, prepared), "CORE_SUBMISSION_CONFLICT")
                db.commit()
                return value
            try:
                db.execute(
                    """INSERT INTO w4_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        prepared.decision_id,
                        prepared.submission_key,
                        prepared.context_key,
                        event["job_id"],
                        event["question_version_id"],
                        event["source_id"],
                        event["analysis_input_version"],
                        event["decision_version"],
                        prepared.context_json,
                        prepared.policy_json,
                        prepared.schema_sha256,
                    ),
                )
                db.execute(
                    """INSERT INTO w4_outbox
                    (message_id, decision_id, body, body_digest, prepared_at) VALUES (?, ?, ?, ?, ?)""",
                    (
                        prepared.message_id,
                        prepared.decision_id,
                        prepared.body,
                        prepared.body_digest,
                        prepared.prepared_at,
                    ),
                )
            except sqlite3.IntegrityError:
                raise QuestionCoreError("CORE_ID_OR_REVISION_CONFLICT") from None
            db.commit()
        return prepared

    def claim(self, *, now, lease_seconds=60):
        now = _time(now)
        require(
            type(lease_seconds) is int and 15 <= lease_seconds <= 300, "CORE_LEASE_CONFIG_INVALID"
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                _JOIN
                + """WHERE state='prepared' AND next_attempt_at<=?
                AND (lease_until IS NULL OR lease_until<=?) ORDER BY prepared_at, message_id LIMIT 1""",
                (now, now),
            ).fetchone()
            if row is None:
                db.commit()
                return None
            token = str(uuid4())
            db.execute(
                """UPDATE w4_outbox SET lease_token=?, lease_until=?,
                attempt_count=attempt_count+1, last_attempt_at=? WHERE message_id=?""",
                (token, now + lease_seconds, now, row["message_id"]),
            )
            db.commit()
            return DeliveryClaim(_prepared(row), token, row["attempt_count"] + 1)

    def owns(self, claim, *, now):
        with self._connect() as db:
            return (
                db.execute(
                    """SELECT 1 FROM w4_outbox WHERE message_id=? AND lease_token=?
                AND state='prepared' AND lease_until>?""",
                    (claim.prepared.message_id, claim.token, _time(now)),
                ).fetchone()
                is not None
            )

    def _finish(self, claim, sql, parameters):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                sql + " WHERE message_id=? AND lease_token=? AND state='prepared'",
                (*parameters, claim.prepared.message_id, claim.token),
            ).rowcount
            require(changed == 1, "CORE_LEASE_LOST")
            db.commit()

    def mark_sent(self, claim, *, now, broker_message_id):
        require(
            isinstance(broker_message_id, str)
            and 0 < len(broker_message_id) <= 100
            and all(c.isalnum() or c in "-_" for c in broker_message_id),
            "CORE_ACCEPTANCE_INVALID",
        )
        self._finish(
            claim,
            """UPDATE w4_outbox SET state='sent', sent_at=?, broker_message_id=?,
            last_error=NULL, lease_token=NULL, lease_until=NULL""",
            (_time(now), broker_message_id),
        )

    def release(self, claim, *, now, error_code):
        require(error_code in SAFE_ERRORS, "CORE_UNSAFE_ERROR_CODE")
        delay = min(300, 2 ** min(claim.attempt, 9))
        self._finish(
            claim,
            """UPDATE w4_outbox SET last_error=?, next_attempt_at=?,
            lease_token=NULL, lease_until=NULL""",
            (error_code, _time(now) + delay),
        )

    def block(self, claim, *, error_code):
        require(error_code in SAFE_ERRORS, "CORE_UNSAFE_ERROR_CODE")
        self._finish(
            claim,
            """UPDATE w4_outbox SET state='blocked', last_error=?,
            lease_token=NULL, lease_until=NULL""",
            (error_code,),
        )

    def inspect(self, message_id):
        """Safe local operational metadata, without body, context or credentials."""
        with self._connect() as db:
            row = db.execute(
                """SELECT message_id, decision_id, body_digest, state, attempt_count,
                last_attempt_at, last_error, prepared_at, sent_at, broker_message_id
                FROM w4_outbox WHERE message_id=?""",
                (message_id,),
            ).fetchone()
            return dict(row) if row else None
