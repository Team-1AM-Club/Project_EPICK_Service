"""Single source of truth for the proposed 0.1-draft wire schemas."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, AfterValidator, model_validator

VERSION = "w3-restriction/0.1-draft"
Version = Literal["w3-restriction/0.1-draft"]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")]


def timestamp(value: str) -> str:
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


UTC = Annotated[
    str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"), AfterValidator(timestamp)
]
Revision = Annotated[int, Field(ge=1, le=2**63 - 1)]
Cursor = Annotated[int, Field(ge=0, le=2**63 - 1)]
Reason = Literal[
    "UNKNOWN_SOURCE",
    "CONFLICT",
    "EVENT_GAP",
    "RESTRICTED",
    "VERSION_UNAVAILABLE",
    "INDEX_PENDING",
    "INDEX_MISMATCH",
    "INDEX_FAILED",
    "BODY_EXPIRED",
    "READY",
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class IndexKey(Strict):
    source_version_id: Identifier
    extraction_revision_id: Identifier
    representation: Literal["text", "html", "markdown"]
    normalization_version: Identifier


class Restriction(Strict):
    source_id: Identifier
    restriction_id: Identifier
    status: Literal["RESTRICTED", "RELEASED"]
    accuracy: Literal["UNKNOWN", "CONFIRMED", "DISPUTED"]
    reason_code: Literal["POLICY_REVIEW", "RIGHTS_RESTRICTION", "ACCURACY_REVIEW", "RESOLVED"]
    replacement_source_id: Identifier | None
    index_key: IndexKey | None


class Event(Strict):
    schema_version: Version
    event_type: Literal["source.restriction.changed"]
    event_id: Identifier
    aggregate_id: Identifier
    aggregate_revision: Revision
    occurred_at: UTC
    published_at: UTC
    payload: Restriction

    @model_validator(mode="after")
    def consistency(self):
        if self.aggregate_id != self.payload.source_id or self.published_at < self.occurred_at:
            raise ValueError("EVENT_INCONSISTENT")
        return self


class Snapshot(Strict):
    schema_version: Version
    as_of: UTC
    event: Event

    @model_validator(mode="after")
    def consistency(self):
        if self.as_of < self.event.published_at:
            raise ValueError("SNAPSHOT_TIME_INVALID")
        return self


class ReplayBatch(Strict):
    schema_version: Version
    source_id: Identifier
    after_revision: Cursor
    high_watermark: Cursor
    retention_start: UTC
    events: Annotated[list[Event], Field(max_length=500)]

    @model_validator(mode="after")
    def consistency(self):
        revisions = [e.aggregate_revision for e in self.events]
        if (
            self.high_watermark < self.after_revision
            or revisions != sorted(set(revisions))
            or any(e.aggregate_id != self.source_id for e in self.events)
            or any(not self.after_revision < r <= self.high_watermark for r in revisions)
        ):
            raise ValueError("REPLAY_INCONSISTENT")
        return self


class Document(Strict):
    document_id: Identifier
    evidence_id: Identifier
    text: Annotated[str, Field(min_length=1, max_length=100_000)]


class IndexRequest(Strict):
    schema_version: Version
    source_id: Identifier
    restriction_revision: Revision
    index_key: IndexKey
    retention_scope: Literal["full", "excerpts_only", "none"]
    expires_at: UTC
    documents: Annotated[list[Document], Field(max_length=100)]

    @model_validator(mode="after")
    def consistency(self):
        ids = [d.document_id for d in self.documents]
        if len(ids) != len(set(ids)) or (self.retention_scope == "none" and self.documents):
            raise ValueError("DOCUMENT_SCOPE_INVALID")
        return self


class Signal(Strict):
    schema_version: Version
    event_type: Literal["w3.source.usability.changed"]
    signal_id: Identifier
    source_id: Identifier
    generation: Revision
    restriction_revision: Cursor
    required_revision: Cursor
    usable: bool
    reason: Reason
    index_key: IndexKey | None
    history_complete: bool

    @model_validator(mode="after")
    def consistency(self):
        if self.usable != (self.reason == "READY") or (
            self.usable
            and (self.index_key is None or self.required_revision != self.restriction_revision)
        ):
            raise ValueError("SIGNAL_READINESS_INCONSISTENT")
        return self


class Status(Strict):
    schema_version: Version
    source_id: Identifier
    restriction_revision: Cursor
    required_revision: Cursor
    generation: Cursor
    reason: Reason
    index_ack: bool
    index_key: IndexKey | None
    history_complete: bool

    @model_validator(mode="after")
    def consistency(self):
        if self.index_ack != (self.reason == "READY") or (
            self.index_ack
            and (self.index_key is None or self.required_revision != self.restriction_revision)
        ):
            raise ValueError("STATUS_READINESS_INCONSISTENT")
        return self


class ConsumeResponse(Status):
    receipt: Literal["COMMITTED"]
    event_id: Identifier
    outcome: Literal["APPLIED", "DUPLICATE", "STALE", "GAP", "CONFLICT"]


class RecoveryResponse(Status):
    outcome: Literal[
        "CURSOR_AHEAD",
        "REPLAYED",
        "SNAPSHOT_REQUIRED",
        "STALE_SNAPSHOT",
        "CONFLICT",
        "SNAPSHOT_APPLIED",
    ]


class DeliveryReceipt(Strict):
    signal_id: Identifier


SCHEMAS = {
    "event": Event,
    "snapshot": Snapshot,
    "replay": ReplayBatch,
    "index-request": IndexRequest,
    "signal": Signal,
    "delivery-receipt": DeliveryReceipt,
    "status": Status,
    "consume-response": ConsumeResponse,
    "recovery-response": RecoveryResponse,
}
