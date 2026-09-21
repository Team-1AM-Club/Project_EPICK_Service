from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.jobs import OutboxMessage
from app.models.sources import Source

SourceRegistryState = Literal[
    "PERMANENTLY_RETIRED",
    "TRANSIENTLY_UNAVAILABLE",
    "UNKNOWN",
]

_MESSAGE_TYPE = "w1.private.w3.source-retirement.v1"
_OUTBOX_SCHEMA_VERSION = "w1.w3.source-retirement/1"
_WIRE_SCHEMA_VERSION = "w1.private.w3-source-retirement/1.0"


class SourceRetirementError(RuntimeError):
    pass


class SourceRetirementNotFoundError(SourceRetirementError):
    pass


class SourceRetirementValidationError(SourceRetirementError):
    pass


class SourceRetirementService:
    """Stage a reference-only W3 command only for an authoritative permanent transition.

    The caller owns the surrounding registry transaction.  Locking the canonical
    Source makes repeated attempts converge on the first durable command without
    adding a second, independent retirement registry.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def stage_if_permanent(
        self,
        *,
        company_id: UUID,
        source_id: UUID,
        registry_revision: str,
        registry_state: SourceRegistryState | str,
        retired_at: datetime | None = None,
    ) -> OutboxMessage | None:
        if registry_state in {"TRANSIENTLY_UNAVAILABLE", "UNKNOWN"}:
            return None
        if registry_state != "PERMANENTLY_RETIRED":
            raise SourceRetirementValidationError("unsupported Source registry state")
        registry_revision = registry_revision.strip()
        if not registry_revision or len(registry_revision) > 128:
            raise SourceRetirementValidationError("registry revision must contain 1-128 chars")

        source = self.session.scalar(
            select(Source)
            .where(Source.id == source_id, Source.company_id == company_id)
            .with_for_update()
        )
        if source is None:
            raise SourceRetirementNotFoundError("Source does not exist for company")

        existing = self.session.scalar(
            select(OutboxMessage)
            .where(
                OutboxMessage.message_type == _MESSAGE_TYPE,
                OutboxMessage.aggregate_type == "W3_SOURCE_RETIREMENT",
                OutboxMessage.aggregate_id == source_id,
            )
            .order_by(OutboxMessage.created_at, OutboxMessage.id)
            .limit(1)
        )
        if existing is not None:
            return existing

        effective_retired_at = retired_at or datetime.now(UTC)
        if effective_retired_at.tzinfo is None or effective_retired_at.utcoffset() is None:
            raise SourceRetirementValidationError("retired_at must be timezone-aware")
        message = OutboxMessage(
            message_type=_MESSAGE_TYPE,
            schema_version=_OUTBOX_SCHEMA_VERSION,
            visibility_scope="PRIVATE",
            aggregate_type="W3_SOURCE_RETIREMENT",
            aggregate_id=source_id,
            aggregate_revision=1,
            payload={},
        )
        self.session.add(message)
        self.session.flush()
        message.payload = {
            "schema_version": _WIRE_SCHEMA_VERSION,
            "command_id": str(message.id),
            "operation": "RETIRE_SOURCE",
            "company_id": str(company_id),
            "source_id": str(source_id),
            "retired_at": effective_retired_at.isoformat().replace("+00:00", "Z"),
            "target_type": "W3_CORE_RUNTIME",
            "target_ref": str(source_id),
        }
        self.session.flush()
        return message
