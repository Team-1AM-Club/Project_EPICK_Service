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
    def __init__(self, path: str | Path, *, retention_seconds: int, max_attempts: int = 8):
        if type(retention_seconds) is not int or retention_seconds < 1:
            raise ValueError("EXPLICIT_RETENTION_REQUIRED")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 100:
            raise ValueError("INVALID_MAX_ATTEMPTS")
        self.producer = CoreDecisionProducer(path)
        self.retention_seconds = retention_seconds
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
            """)

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
    def _body(db, event_id):
        row = db.execute(
            "SELECT event FROM w3_core_decision_outbox WHERE json_extract(event,'$.message_id')=?",
            (event_id,),
        ).fetchone()
        return row[0] if row else None

    @staticmethod
    def _purge(db, event_id, state):
        db.execute("PRAGMA secure_delete=ON")
        db.execute(
            "DELETE FROM w3_core_decision_outbox WHERE json_extract(event,'$.message_id')=?",
            (event_id,),
        )
        db.execute("UPDATE core_delivery SET state=? WHERE event_id=?", (state, event_id))

    def supply(self, plan: AnalysisPlan, authority: Authority, idempotency_key: str, *, now: float):
        plan = AnalysisPlan.model_validate(plan.model_dump())
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
            authorization = _authorization(authority, plan.context)
            owner_hash = _hash(str(authorization.owner_id))
            self._owner_allowed(db, owner_hash)
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
                (key_hash,owner_hash,epoch,event_id,digest,request_hash,state,next_attempt)
                VALUES (?,?,?,?,?,?,'PENDING',?)""",
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
                ),
            )
            return event

    def relay_once(self, authority: Authority, transport: Transport, *, now: float) -> str:
        # A bounded network call under this lock serializes local deletion with sending.
        # A crash after SendMessage but before commit intentionally retries identical bytes.
        with self.producer._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._enabled(db)
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
                current = _authorization(authority, context)
                if _hash(str(current.owner_id)) != owner_hash or current.owner_epoch != epoch:
                    raise ValueError("CURRENT_AUTHORIZATION_REJECTED")
            except ValueError:
                self._purge(db, event_id, "BLOCKED")
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
            "UPDATE core_delivery SET state=?,attempts=attempts+1,next_attempt=? WHERE event_id=?",
            (state, now + min(300, 2 ** min(attempts + 1, 9)), event_id),
        )
        return state

    def replay(self, event_id: UUID, authority: Authority, *, now: float):
        """Explicit operator replay, never a new event or an inferred W1 acceptance."""
        with self.producer._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._enabled(db)
            body = self._body(db, str(event_id))
            if body is None:
                raise ValueError("EVENT_RETIRED")
            event = CoreDecisionEvent.model_validate_json(body)
            context = DecisionContext.model_validate(
                {k: getattr(event, k) for k in DecisionContext.model_fields}
            )
            current = _authorization(authority, context)
            self._owner_allowed(db, _hash(str(current.owner_id)))
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

    def delete_owner(self, owner_id: UUID, *, deletion_epoch: int) -> int:
        owner_hash = _hash(str(UUID(str(owner_id))))
        if type(deletion_epoch) is not int or deletion_epoch < 0:
            raise ValueError("INVALID_DELETION_EPOCH")
        with self.producer._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            maximum = db.execute(
                "SELECT MAX(epoch) FROM core_delivery WHERE owner_hash=?", (owner_hash,)
            ).fetchone()[0]
            if maximum is not None and deletion_epoch <= maximum:
                raise ValueError("STALE_DELETION_EPOCH")
            db.execute(
                """INSERT INTO core_deleted_owners VALUES (?,?) ON CONFLICT(owner_hash)
                DO UPDATE SET epoch=MAX(epoch,excluded.epoch)""",
                (owner_hash, deletion_epoch),
            )
            rows = db.execute(
                "SELECT event_id FROM core_delivery WHERE owner_hash=? AND state!='DELETED'",
                (owner_hash,),
            ).fetchall()
            for (event_id,) in rows:
                self._purge(db, event_id, "DELETED")
            return len(rows)

    def expire(self, *, now: float) -> int:
        with self.producer._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT event_id FROM core_delivery WHERE state='TRANSPORT_HANDOFF' AND published_at<=?",
                (now - self.retention_seconds,),
            ).fetchall()
            for (event_id,) in rows:
                self._purge(db, event_id, "EXPIRED")
            return len(rows)

    def inspect(self):
        """Metadata-only test/operator hook; no private event body or errors."""
        with self.producer._connect() as db:
            db.row_factory = sqlite3.Row
            return [
                dict(row)
                for row in db.execute(
                    "SELECT event_id,digest,state,attempts,next_attempt,published_at FROM core_delivery ORDER BY event_id"
                )
            ]

    def backup(self, destination: Path):
        destination = Path(destination)
        if destination.exists():
            raise ValueError("BACKUP_DESTINATION_EXISTS")
        temporary = destination.with_name(destination.name + "." + uuid4().hex + ".tmp")
        snapshot = sqlite3.connect(":memory:")
        try:
            with self.producer._connect() as db:
                db.backup(snapshot)
            with snapshot:
                snapshot.execute(
                    "UPDATE core_runtime_settings SET value='1' WHERE key='quarantined'"
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
