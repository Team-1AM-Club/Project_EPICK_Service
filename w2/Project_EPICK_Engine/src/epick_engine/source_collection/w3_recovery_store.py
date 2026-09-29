"""Read-only reconstruction of complete public Source event history for W3 recovery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from epick_engine.source_collection.contracts import (
    SourceEvent,
    SourceEventType,
    SourceRestrictionSnapshot,
)
from epick_engine.source_collection.persistence import OutboxEvent, Source
from epick_engine.source_collection.w3_public_transport import source_event_to_w3_wire
from epick_engine.source_collection.worker import _source_event_from_outbox_row


class W3RecoveryHistoryError(RuntimeError):
    """Sanitized recovery-history failure without event payload or database details."""

    def __init__(self, code: str) -> None:
        super().__init__(f"W3 recovery history failed: {code}")
        self.code = code


@dataclass(frozen=True, slots=True)
class RecoveryHistory:
    """Immutable complete public event history fixed at one database snapshot."""

    source_id: UUID
    high_watermark: int
    restriction_revision: int
    events: tuple[SourceEvent, ...]
    as_of: datetime


class SqlAlchemyRecoveryHistoryStore:
    """Read one Source history without changing public outbox delivery state."""

    def __init__(self, *, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def load_history(self, *, source_id: UUID) -> RecoveryHistory:
        """Return a fully validated, contiguous public history from one read snapshot."""

        if not isinstance(source_id, UUID):
            raise W3RecoveryHistoryError("INVALID_SOURCE_ID")
        try:
            session = self._session_factory()
        except Exception:
            raise W3RecoveryHistoryError("READ_FAILED") from None

        try:
            session.begin()
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            source = session.get(Source, source_id)
            if source is None:
                raise W3RecoveryHistoryError("SOURCE_NOT_FOUND")

            rows = tuple(
                session.scalars(
                    select(OutboxEvent)
                    .where(OutboxEvent.aggregate_id == source_id)
                    .order_by(OutboxEvent.aggregate_revision)
                )
            )
            if not rows:
                raise W3RecoveryHistoryError("EMPTY_HISTORY")

            restriction_revision = 0
            events: list[SourceEvent] = []
            for ordinal, row in enumerate(rows, start=1):
                if row.aggregate_revision != ordinal:
                    raise W3RecoveryHistoryError("HISTORY_GAP")
                try:
                    source_event = _source_event_from_outbox_row(row)
                    source_event_to_w3_wire(source_event)
                except Exception:
                    raise W3RecoveryHistoryError("INVALID_EVENT") from None
                if source_event.event_type is SourceEventType.RESTRICTION_CHANGED:
                    payload = source_event.payload
                    if not isinstance(payload, SourceRestrictionSnapshot):
                        raise W3RecoveryHistoryError("INVALID_EVENT")
                    if payload.restriction_revision != restriction_revision + 1:
                        raise W3RecoveryHistoryError("RESTRICTION_REVISION_GAP")
                    restriction_revision = payload.restriction_revision
                events.append(source_event)

            as_of = max(source_event.occurred_at.astimezone(UTC) for source_event in events)
            return RecoveryHistory(
                source_id=source_id,
                high_watermark=rows[-1].aggregate_revision,
                restriction_revision=restriction_revision,
                events=tuple(events),
                as_of=as_of,
            )
        except W3RecoveryHistoryError:
            raise
        except Exception:
            raise W3RecoveryHistoryError("READ_FAILED") from None
        finally:
            try:
                session.rollback()
            except Exception:
                pass
            try:
                session.close()
            except Exception:
                pass
