"""Candidate W3 -> W1 decision producer. No queue or W1 DB writes."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

VERSION = "w3.private.core-decision/0.1-candidate"
InputVersion = Annotated[str, Field(min_length=1, max_length=64, pattern=r"\S")]
ReasonCode = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")]
DecisionCode = Literal["CORE_REQUIRED", "NON_CORE_OPTIONAL"]


class DecisionContext(BaseModel):
    """Current W1 binding supplied through a trusted caller, never from event claims."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    job_id: UUID
    company_id: UUID
    source_id: UUID
    analysis_input_version: InputVersion


class CoreDecisionEvent(DecisionContext):
    schema_version: Literal["w3.private.core-decision/0.1-candidate"]
    message_type: Literal["w3.private.w1.core-decision"]
    message_id: UUID
    occurred_at: AwareDatetime
    visibility_scope: Literal["PRIVATE"]
    producer: Literal["w3"]
    decision_scope: Literal["COMPANY_KNOWLEDGE"]
    decision_owner: Literal["W3"]
    question_version_id: None
    decision_version: Annotated[int, Field(strict=True, ge=1)]
    is_core: Annotated[bool, Field(strict=True)]
    decision_code: DecisionCode
    reason_code: ReasonCode

    @model_validator(mode="after")
    def consistent_decision(self):
        if self.is_core != (self.decision_code == "CORE_REQUIRED"):
            raise ValueError("DECISION_CODE_MISMATCH")
        return self

    @classmethod
    def model_json_schema(cls, *args, **kwargs):
        schema = super().model_json_schema(*args, **kwargs)
        schema["allOf"] = [
            {
                "if": {"properties": {"decision_code": {"const": "CORE_REQUIRED"}}},
                "then": {"properties": {"is_core": {"const": True}}},
                "else": {"properties": {"is_core": {"const": False}}},
            }
        ]
        return schema


def _context(value: DecisionContext) -> DecisionContext:
    return DecisionContext.model_validate(value.model_dump())


def validate_current_binding(
    event: CoreDecisionEvent, *, current: DecisionContext, authenticated_principal: str
) -> None:
    """Reference precondition only; W1 must repeat under its currentness lock."""
    if authenticated_principal != "w3":
        raise ValueError("UNAUTHENTICATED_PRODUCER")
    event = CoreDecisionEvent.model_validate(event.model_dump())
    current = _context(current)
    if any(
        getattr(event, field) != getattr(current, field) for field in DecisionContext.model_fields
    ):
        raise ValueError("CURRENT_BINDING_MISMATCH")


class CoreDecisionProducer:
    """Durable local outbox. Retries reuse stored bytes; no delivered/accepted claims.

    Caller supplies a domain decision and a separately authenticated W1 context.
    This module does not infer core status from C01 availability or model confidence.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        if not self.path or self.path == ":memory:":
            raise ValueError("DURABLE_PATH_REQUIRED")
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS w3_core_decision_outbox (
                job_id TEXT NOT NULL, source_id TEXT NOT NULL, company_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, request TEXT NOT NULL,
                revision INTEGER NOT NULL, event TEXT NOT NULL,
                PRIMARY KEY(job_id, source_id, idempotency_key),
                UNIQUE(company_id, source_id, revision))""")
            db.execute("""CREATE TABLE IF NOT EXISTS w3_core_counters (
                company_id TEXT, source_id TEXT, revision INTEGER NOT NULL,
                PRIMARY KEY(company_id, source_id))""")
            db.execute("""INSERT INTO w3_core_counters
                SELECT company_id, source_id, MAX(revision) FROM w3_core_decision_outbox
                WHERE 1 GROUP BY company_id, source_id
                ON CONFLICT(company_id,source_id) DO UPDATE
                SET revision=MAX(revision,excluded.revision)""")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def publish(
        self,
        *,
        requested: DecisionContext,
        current: DecisionContext,
        authenticated_principal: str,
        idempotency_key: str,
        decision_code: DecisionCode,
        reason_code: str,
        _db: sqlite3.Connection | None = None,
    ) -> CoreDecisionEvent:
        requested, current = _context(requested), _context(current)
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip()
            or len(idempotency_key) > 128
        ):
            raise ValueError("INVALID_IDEMPOTENCY_KEY")
        candidate = CoreDecisionEvent.model_validate(
            {
                **requested.model_dump(),
                "schema_version": VERSION,
                "message_type": "w3.private.w1.core-decision",
                "message_id": uuid4(),
                "occurred_at": datetime.now(UTC),
                "visibility_scope": "PRIVATE",
                "producer": "w3",
                "decision_scope": "COMPANY_KNOWLEDGE",
                "decision_owner": "W3",
                "question_version_id": None,
                "decision_version": 1,
                "is_core": decision_code == "CORE_REQUIRED",
                "decision_code": decision_code,
                "reason_code": reason_code,
            }
        )
        validate_current_binding(
            candidate, current=current, authenticated_principal=authenticated_principal
        )
        request = json.dumps(
            {
                **requested.model_dump(mode="json"),
                "decision_code": decision_code,
                "reason_code": reason_code,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        key = (str(requested.job_id), str(requested.source_id))
        with nullcontext(_db) if _db is not None else self._connect() as db:
            if _db is None:
                db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT request, event FROM w3_core_decision_outbox WHERE job_id=? AND source_id=? AND idempotency_key=?",
                (*key, idempotency_key),
            ).fetchone()
            if previous:
                if previous[0] != request:
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return CoreDecisionEvent.model_validate_json(previous[1])
            revision = db.execute(
                "SELECT COALESCE(MAX(revision),0)+1 FROM w3_core_counters WHERE company_id=? AND source_id=?",
                (str(requested.company_id), str(requested.source_id)),
            ).fetchone()[0]
            db.execute(
                """INSERT INTO w3_core_counters VALUES (?,?,?)
                ON CONFLICT(company_id,source_id) DO UPDATE SET revision=excluded.revision""",
                (str(requested.company_id), str(requested.source_id), revision),
            )
            event = CoreDecisionEvent.model_validate(
                {**candidate.model_dump(), "decision_version": revision}
            )
            db.execute(
                "INSERT INTO w3_core_decision_outbox VALUES (?,?,?,?,?,?,?)",
                (
                    *key,
                    str(requested.company_id),
                    idempotency_key,
                    request,
                    revision,
                    event.model_dump_json(),
                ),
            )
            return event

    def pending(self, current: DecisionContext) -> list[CoreDecisionEvent]:
        """Read unacknowledged events for a trusted current binding, in revision order.

        All records stay pending until a W1 receipt contract is adopted. A caller
        must recheck currentness at send time; this read is not authorization.
        """
        current = _context(current)
        with self._connect() as db:
            rows = db.execute(
                "SELECT event FROM w3_core_decision_outbox WHERE job_id=? AND source_id=? ORDER BY revision",
                (str(current.job_id), str(current.source_id)),
            ).fetchall()
        events = [CoreDecisionEvent.model_validate_json(row[0]) for row in rows]
        return [
            event
            for event in events
            if all(
                getattr(event, field) == getattr(current, field)
                for field in DecisionContext.model_fields
            )
        ]
