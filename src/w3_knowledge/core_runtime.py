"""Trusted analysis supply, durable transport handoff, and private event lifecycle."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Annotated, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .core_decision import CoreDecisionEvent, CoreDecisionProducer, DecisionContext
from .retention import DEFAULT_RETENTION_POLICY, POLICY_REVISION, RetentionPolicy


class Authorization(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    context: DecisionContext
    owner_id: UUID
    owner_epoch: Annotated[int, Field(strict=True, ge=0)]
    active: Annotated[bool, Field(strict=True)]


class AnalysisPlan(BaseModel):
    """Trusted analysis dependency plan; never accept arbitrary end-user classification."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    context: DecisionContext
    analysis_request_id: UUID
    analysis_request_issued_at: Annotated[float, Field(strict=True, ge=0)]
    required_sources: list[UUID]
    optional_sources: list[UUID]


class Authority(Protocol):
    def current(self, job_id: UUID, source_id: UUID) -> Authorization: ...


class Transport(Protocol):
    def send(self, body: str) -> str: ...


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _authorization(authority, context):
    result = authority.current(context.job_id, context.source_id)
    result = Authorization.model_validate(result.model_dump())
    if not result.active or result.context != context:
        raise ValueError("CURRENT_AUTHORIZATION_REJECTED")
    return result


class CoreRuntime:
    def __init__(
        self,
        path: str | Path,
        *,
        policy: RetentionPolicy = DEFAULT_RETENTION_POLICY,
        retention_seconds: int | None = None,
        max_attempts: int = 8,
    ):
        self.policy = RetentionPolicy.model_validate(policy.model_dump())
        if retention_seconds is not None and retention_seconds != self.policy.handoff_body_seconds:
            raise ValueError("RETENTION_POLICY_MISMATCH")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 100:
            raise ValueError("INVALID_MAX_ATTEMPTS")
        self.producer = CoreDecisionProducer(path)
        self.max_attempts = max_attempts
        with self.producer._connect() as db:
            db.execute("PRAGMA secure_delete=ON")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS core_runtime_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO core_runtime_settings VALUES ('quarantined','0');
                CREATE TABLE IF NOT EXISTS core_deleted_owners (owner_hash TEXT PRIMARY KEY, epoch INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS core_delivery (
                    key_hash TEXT PRIMARY KEY, owner_hash TEXT NOT NULL, epoch INTEGER NOT NULL,
                    event_id TEXT UNIQUE NOT NULL, digest TEXT NOT NULL, request_hash TEXT NOT NULL,
                    state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL, published_at REAL);
                CREATE TABLE IF NOT EXISTS core_retired_sources (
                    company_id TEXT NOT NULL, source_id TEXT NOT NULL, retired_at REAL NOT NULL,
                    PRIMARY KEY(company_id,source_id));
            """)
            self._add_column(db, "core_delivery", "created_at", "REAL")
            self._add_column(db, "core_delivery", "held_at", "REAL")
            self._add_column(db, "core_delivery", "terminal_at", "REAL")
            self._add_column(db, "core_deleted_owners", "deleted_at", "REAL")
            db.execute(
                """INSERT INTO core_runtime_settings(key,value) VALUES ('policy_revision',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (POLICY_REVISION,),
            )
            self._migrate_delivery_timestamps(db)

    @staticmethod
    def _add_column(db, table, name, declaration):
        columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if name not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")

    def _migrate_delivery_timestamps(self, db):
        rows = db.execute(
            "SELECT event_id,state,published_at FROM core_delivery WHERE created_at IS NULL"
        ).fetchall()
        for event_id, state, published_at in rows:
            body = self._body(db, event_id)
            if body is None:
                db.execute("DELETE FROM core_delivery WHERE event_id=?", (event_id,))
                continue
            try:
                created_at = CoreDecisionEvent.model_validate_json(body).occurred_at.timestamp()
            except Exception:
                db.execute(
                    "DELETE FROM w3_core_decision_outbox WHERE json_extract(event,'$.message_id')=?",
                    (event_id,),
                )
                db.execute(
                    """UPDATE core_delivery SET state='MIGRATION_BLOCKED',created_at=0,
                    terminal_at=COALESCE(terminal_at,0) WHERE event_id=?""",
                    (event_id,),
                )
                continue
            terminal_at = None
            if state in {"BLOCKED", "DELETED", "EXPIRED", "DELIVERY_EXPIRED"}:
                terminal_at = published_at if published_at is not None else 0
            db.execute(
                "UPDATE core_delivery SET created_at=?,terminal_at=COALESCE(terminal_at,?) WHERE event_id=?",
                (created_at, terminal_at, event_id),
            )

    @staticmethod
    def _enabled(db):
        if (
            db.execute(
                "SELECT value FROM core_runtime_settings WHERE key='quarantined'"
            ).fetchone()[0]
            != "0"
        ):
            raise ValueError("RESTORE_QUARANTINED")

    @staticmethod
    def _owner_allowed(db, owner_hash):
        if db.execute(
            "SELECT 1 FROM core_deleted_owners WHERE owner_hash=?", (owner_hash,)
        ).fetchone():
            raise ValueError("OWNER_DELETED")

    @staticmethod
    def _source_allowed(db, company_id, source_id):
        if db.execute(
            "SELECT 1 FROM core_retired_sources WHERE company_id=? AND source_id=?",
            (str(company_id), str(source_id)),
        ).fetchone():
            raise ValueError("SOURCE_RETIRED")

    @staticmethod
    def _body(db, event_id):
        row = db.execute(
            "SELECT event FROM w3_core_decision_outbox WHERE json_extract(event,'$.message_id')=?",
            (event_id,),
        ).fetchone()
        return row[0] if row else None

    @staticmethod
    def _purge(db, event_id, state, *, terminal_at):
        db.execute("PRAGMA secure_delete=ON")
        db.execute(
            "DELETE FROM w3_core_decision_outbox WHERE json_extract(event,'$.message_id')=?",
            (event_id,),
        )
        db.execute(
            "UPDATE core_delivery SET state=?,terminal_at=COALESCE(terminal_at,?) WHERE event_id=?",
            (state, terminal_at, event_id),
        )

    def _expire_locked(self, db, now):
        counts = {"bodies": 0, "metadata": 0, "tombstones": 0, "counters": 0}
        rows = db.execute(
            """SELECT event_id,state,created_at,published_at FROM core_delivery
            WHERE state IN ('PENDING','RETRY','HELD','TRANSPORT_HANDOFF')"""
        ).fetchall()
        for event_id, state, created_at, published_at in rows:
            if created_at is None:
                self._purge(db, event_id, "MIGRATION_BLOCKED", terminal_at=0)
                counts["bodies"] += 1
                continue
            deadline = self.policy.body_deadline(
                created_at, published_at if state == "TRANSPORT_HANDOFF" else None
            )
            if now >= deadline:
                terminal = "EXPIRED" if state == "TRANSPORT_HANDOFF" else "DELIVERY_EXPIRED"
                self._purge(db, event_id, terminal, terminal_at=deadline)
                counts["bodies"] += 1
        cursor = db.execute(
            """DELETE FROM core_delivery WHERE terminal_at IS NOT NULL
            AND terminal_at + ? <= ?""",
            (self.policy.terminal_metadata_seconds, now),
        )
        counts["metadata"] = cursor.rowcount
        cursor = db.execute(
            """DELETE FROM core_deleted_owners WHERE deleted_at IS NOT NULL
            AND deleted_at + ? <= ?""",
            (self.policy.owner_tombstone_seconds, now),
        )
        counts["tombstones"] = cursor.rowcount
        cursor = db.execute(
            """DELETE FROM w3_core_counters WHERE EXISTS (
                SELECT 1 FROM core_retired_sources retired
                WHERE retired.company_id=w3_core_counters.company_id
                  AND retired.source_id=w3_core_counters.source_id
                  AND retired.retired_at + ? <= ?)""",
            (self.policy.retired_counter_seconds, now),
        )
        counts["counters"] = cursor.rowcount
        return counts

    def supply(self, plan: AnalysisPlan, authority: Authority, idempotency_key: str, *, now: float):
        plan = AnalysisPlan.model_validate(plan.model_dump())
        if plan.analysis_request_issued_at > now:
            raise ValueError("ANALYSIS_REQUEST_FROM_FUTURE")
        if now >= plan.analysis_request_issued_at + self.policy.private_body_max_seconds:
            raise ValueError("ANALYSIS_REQUEST_EXPIRED")
        source = plan.context.source_id
        required, optional = source in plan.required_sources, source in plan.optional_sources
        if required == optional:
            raise ValueError("SOURCE_DEPENDENCY_UNRESOLVED")
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip()
            or len(idempotency_key) > 128
        ):
            raise ValueError("INVALID_IDEMPOTENCY_KEY")
        with self.producer._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._enabled(db)
            self._expire_locked(db, now)
            authorization = _authorization(authority, plan.context)
            owner_hash = _hash(str(authorization.owner_id))
            self._owner_allowed(db, owner_hash)
            self._source_allowed(db, plan.context.company_id, source)
            key_hash = _hash(json.dumps([str(plan.context.job_id), str(source), idempotency_key]))
            request_hash = _hash(
                json.dumps(
                    {
                        "plan": plan.model_dump(mode="json"),
                        "authorization": authorization.model_dump(mode="json"),
                    },
                    sort_keys=True,
                )
            )
            previous = db.execute(
                "SELECT request_hash,event_id,state FROM core_delivery WHERE key_hash=?",
                (key_hash,),
            ).fetchone()
            if previous:
                if previous[0] != request_hash:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                body = self._body(db, previous[1])
                if body is None:
                    raise ValueError("EVENT_RETIRED")
                return CoreDecisionEvent.model_validate_json(body)
            event = self.producer.publish(
                requested=plan.context,
                current=authorization.context,
                authenticated_principal="w3",
                idempotency_key=idempotency_key,
                decision_code="CORE_REQUIRED" if required else "NON_CORE_OPTIONAL",
                reason_code="REQUIRED_ANALYSIS_DEPENDENCY"
                if required
                else "OPTIONAL_ANALYSIS_CONTEXT",
                _db=db,
            )
            body = event.model_dump_json()
            db.execute(
                """INSERT INTO core_delivery
                (key_hash,owner_hash,epoch,event_id,digest,request_hash,state,next_attempt,created_at)
                VALUES (?,?,?,?,?,?,'PENDING',?,?)""",
                (
                    key_hash,
                    owner_hash,
                    authorization.owner_epoch,
                    str(event.message_id),
                    _hash(
                        json.dumps(
                            json.loads(body),
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                            allow_nan=False,
                        )
                    ),
                    request_hash,
                    now,
                    now,
                ),
            )
            return event

    def relay_once(self, authority: Authority, transport: Transport, *, now: float) -> str:
        # A bounded network call under this lock serializes local deletion with sending.
        # A crash after SendMessage but before commit intentionally retries identical bytes.
        with self.producer._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._enabled(db)
            self._expire_locked(db, now)
            row = db.execute(
                """SELECT event_id,owner_hash,epoch,attempts FROM core_delivery
                WHERE state IN ('PENDING','RETRY') AND next_attempt<=? ORDER BY next_attempt,event_id LIMIT 1""",
                (now,),
            ).fetchone()
            if not row:
                return "IDLE"
            event_id, owner_hash, epoch, attempts = row
            body = self._body(db, event_id)
            if body is None:
                raise ValueError("OUTBOX_BODY_MISSING")
            event = CoreDecisionEvent.model_validate_json(body)
            context = DecisionContext.model_validate(
                {k: getattr(event, k) for k in DecisionContext.model_fields}
            )
            try:
                self._owner_allowed(db, owner_hash)
                self._source_allowed(db, context.company_id, context.source_id)
                current = _authorization(authority, context)
                if _hash(str(current.owner_id)) != owner_hash or current.owner_epoch != epoch:
                    raise ValueError("CURRENT_AUTHORIZATION_REJECTED")
            except ValueError:
                self._purge(db, event_id, "BLOCKED", terminal_at=now)
                return "BLOCKED"
            except Exception:
                return self._retry(db, event_id, attempts, now)
            try:
                receipt = transport.send(body)
                if not isinstance(receipt, str) or not receipt:
                    raise ValueError("INVALID_TRANSPORT_RECEIPT")
            except Exception:
                return self._retry(db, event_id, attempts, now)
            db.execute(
                "UPDATE core_delivery SET state='TRANSPORT_HANDOFF',attempts=attempts+1,published_at=? WHERE event_id=?",
                (now, event_id),
            )
            return "TRANSPORT_HANDOFF"

    def _retry(self, db, event_id, attempts, now):
        state = "HELD" if attempts + 1 >= self.max_attempts else "RETRY"
        db.execute(
            """UPDATE core_delivery SET state=?,attempts=attempts+1,next_attempt=?,
            held_at=CASE WHEN ?='HELD' THEN COALESCE(held_at,?) ELSE held_at END
            WHERE event_id=?""",
            (state, now + min(300, 2 ** min(attempts + 1, 9)), state, now, event_id),
        )
        return state

    def replay(self, event_id: UUID, authority: Authority, *, now: float):
        """Explicit operator replay, never a new event or an inferred W1 acceptance."""
        with self.producer._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._enabled(db)
            self._expire_locked(db, now)
            body = self._body(db, str(event_id))
            if body is None:
                raise ValueError("EVENT_RETIRED")
            event = CoreDecisionEvent.model_validate_json(body)
            context = DecisionContext.model_validate(
                {k: getattr(event, k) for k in DecisionContext.model_fields}
            )
            current = _authorization(authority, context)
            self._owner_allowed(db, _hash(str(current.owner_id)))
            self._source_allowed(db, context.company_id, context.source_id)
            row = db.execute(
                "SELECT owner_hash,epoch,state FROM core_delivery WHERE event_id=?",
                (str(event_id),),
            ).fetchone()
            if not row or row[:2] != (_hash(str(current.owner_id)), current.owner_epoch):
                raise ValueError("CURRENT_AUTHORIZATION_REJECTED")
            db.execute(
                "UPDATE core_delivery SET state='PENDING',attempts=0,next_attempt=?,published_at=NULL WHERE event_id=?",
                (now, str(event_id)),
            )

    def delete_owner(self, owner_id: UUID, *, deletion_epoch: int, now: float) -> int:
        owner_hash = _hash(str(UUID(str(owner_id))))
        if type(deletion_epoch) is not int or deletion_epoch < 0:
            raise ValueError("INVALID_DELETION_EPOCH")
        with self.producer._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            maximum = db.execute(
                "SELECT MAX(epoch) FROM core_delivery WHERE owner_hash=?", (owner_hash,)
            ).fetchone()[0]
            tombstone = db.execute(
                "SELECT epoch FROM core_deleted_owners WHERE owner_hash=?", (owner_hash,)
            ).fetchone()
            if tombstone is not None:
                maximum = max(maximum if maximum is not None else -1, tombstone[0])
            if maximum is not None and deletion_epoch <= maximum:
                raise ValueError("STALE_DELETION_EPOCH")
            db.execute(
                """INSERT INTO core_deleted_owners(owner_hash,epoch,deleted_at) VALUES (?,?,?)
                ON CONFLICT(owner_hash) DO UPDATE SET
                epoch=MAX(epoch,excluded.epoch),deleted_at=core_deleted_owners.deleted_at""",
                (owner_hash, deletion_epoch, now),
            )
            rows = db.execute(
                "SELECT event_id FROM core_delivery WHERE owner_hash=? AND state!='DELETED'",
                (owner_hash,),
            ).fetchall()
            for (event_id,) in rows:
                self._purge(db, event_id, "DELETED", terminal_at=now)
            return len(rows)

    def retire_source(self, company_id: UUID, source_id: UUID, *, now: float) -> None:
        with self.producer._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO core_retired_sources(company_id,source_id,retired_at)
                VALUES (?,?,?) ON CONFLICT(company_id,source_id) DO NOTHING""",
                (str(company_id), str(source_id), now),
            )

    def expire(self, *, now: float) -> dict[str, int]:
        with self.producer._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return self._expire_locked(db, now)

    def inspect(self):
        """Metadata-only test/operator hook; no private event body or errors."""
        with self.producer._connect() as db:
            db.row_factory = sqlite3.Row
            return [
                {
                    **dict(row),
                    "policy_revision": POLICY_REVISION,
                    "expires_at": self.policy.body_deadline(row["created_at"], row["published_at"])
                    if row["created_at"] is not None
                    and row["state"] in {"PENDING", "RETRY", "HELD", "TRANSPORT_HANDOFF"}
                    else None,
                }
                for row in db.execute(
                    """SELECT event_id,digest,state,attempts,next_attempt,published_at,
                    created_at,held_at,terminal_at FROM core_delivery ORDER BY event_id"""
                )
            ]

    def inspect_report(self):
        with self.producer._connect() as db:
            missing_tombstone_dates = db.execute(
                "SELECT COUNT(*) FROM core_deleted_owners WHERE deleted_at IS NULL"
            ).fetchone()[0]
        return {
            "policy_revision": POLICY_REVISION,
            "migration_blockers": {"owner_tombstone_without_deleted_at": missing_tombstone_dates},
            "deliveries": self.inspect(),
        }

    def backup(self, destination: Path, *, now: float):
        destination = Path(destination)
        if destination.exists():
            raise ValueError("BACKUP_DESTINATION_EXISTS")
        self.expire(now=now)
        temporary = destination.with_name(destination.name + "." + uuid4().hex + ".tmp")
        snapshot = sqlite3.connect(":memory:")
        try:
            with self.producer._connect() as db:
                db.backup(snapshot)
            with snapshot:
                deadlines = [now + self.policy.backup_seconds]
                deadlines.extend(
                    value + self.policy.terminal_metadata_seconds
                    for (value,) in snapshot.execute(
                        "SELECT terminal_at FROM core_delivery WHERE terminal_at IS NOT NULL"
                    )
                )
                deadlines.extend(
                    value + self.policy.owner_tombstone_seconds
                    for (value,) in snapshot.execute(
                        "SELECT deleted_at FROM core_deleted_owners WHERE deleted_at IS NOT NULL"
                    )
                )
                deadlines.extend(
                    value + self.policy.retired_counter_seconds
                    for (value,) in snapshot.execute(
                        """SELECT retired.retired_at FROM core_retired_sources retired
                        JOIN w3_core_counters counters
                          ON counters.company_id=retired.company_id
                         AND counters.source_id=retired.source_id"""
                    )
                )
                expires_at = min(deadlines)
                snapshot.execute(
                    "UPDATE core_runtime_settings SET value='1' WHERE key='quarantined'"
                )
                snapshot.executemany(
                    """INSERT INTO core_runtime_settings(key,value) VALUES (?,?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                    [
                        ("backup_created_at", str(now)),
                        ("backup_expires_at", str(expires_at)),
                    ],
                )
                # Supported backups contain no recoverable private event body/request.
                snapshot.execute("PRAGMA secure_delete=ON")
                snapshot.execute("DELETE FROM w3_core_decision_outbox")
                snapshot.execute("UPDATE core_delivery SET state='BACKUP_REDACTED'")
            snapshot.execute("VACUUM")
            target = sqlite3.connect(temporary)
            try:
                snapshot.backup(target)
            finally:
                target.close()
        finally:
            snapshot.close()
        temporary.rename(destination)
        return {"created_at": now, "expires_at": expires_at}


class SqsTransport:
    """Send-only adapter; uses workload credentials from the SDK provider chain."""

    def __init__(self, client, queue_url: str):
        if not queue_url.startswith("https://") or queue_url.endswith(".fifo"):
            raise ValueError("STANDARD_SQS_HTTPS_URL_REQUIRED")
        self.client, self.queue_url = client, queue_url

    def send(self, body: str) -> str:
        result = self.client.send_message(QueueUrl=self.queue_url, MessageBody=body)
        if result.get("ResponseMetadata", {}).get("HTTPStatusCode") != 200 or not result.get(
            "MessageId"
        ):
            raise ValueError("SQS_HANDOFF_UNCONFIRMED")
        return result["MessageId"]
