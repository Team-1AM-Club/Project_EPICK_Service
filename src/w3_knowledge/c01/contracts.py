"""Strict envelope plus the unmodified W2 payload JSON Schema."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import AfterValidator, Field, model_validator

from ..restriction.contracts import Cursor, Revision, Strict, UTC
from ..models import StructureResponse

VERSION = "w3-c01/0.2-candidate"
Version = Literal["w3-c01/0.2-candidate"]
RawUUIDText = Annotated[
    str,
    Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"),
]
UUIDText = Annotated[RawUUIDText, AfterValidator(str.lower)]
PAYLOAD_SCHEMA = json.loads(
    Path(__file__).with_name("source-event-payload.schema.json").read_text(encoding="utf-8")
)
Draft202012Validator.check_schema(PAYLOAD_SCHEMA)
CHECKER = FormatChecker()
PAYLOAD_VALIDATOR = Draft202012Validator(PAYLOAD_SCHEMA, format_checker=CHECKER)
TYPES = {
    "source.version.available": "SourceVersionPayload",
    "source.observation.changed": "SourceObservationPayload",
    "source.restriction.changed": "SourceRestrictionPayload",
}


def inline_schema(value):
    """Inline acyclic W2 refs so they cannot collide with Pydantic's definitions."""
    if isinstance(value, list):
        return [inline_schema(v) for v in value]
    if isinstance(value, dict):
        if "$ref" in value:
            return inline_schema(PAYLOAD_SCHEMA["$defs"][value["$ref"].split("/")[-1]])
        return {k: inline_schema(v) for k, v in value.items() if k not in {"$defs", "$id"}}
    return value


def validate_payload(value, event_type=None):
    schema = PAYLOAD_SCHEMA
    if event_type is not None:
        schema = {"$defs": PAYLOAD_SCHEMA["$defs"], "$ref": "#/$defs/" + TYPES[event_type]}
    if not Draft202012Validator(schema, format_checker=CHECKER).is_valid(value):
        raise ValueError("W2_PAYLOAD_INVALID")
    return value


def aware_time(value):
    if not CHECKER.conforms(value, "date-time"):
        raise ValueError("DATE_TIME_INVALID")
    return value


DateTime = Annotated[str, AfterValidator(aware_time)]


class Event(Strict):
    schema_version: Literal["1.0"]
    event_id: RawUUIDText
    event_type: Literal[
        "source.version.available", "source.observation.changed", "source.restriction.changed"
    ]
    producer: Literal["w2"]
    occurred_at: DateTime
    aggregate_type: Literal["source"]
    aggregate_id: RawUUIDText
    revision: Revision
    payload: dict

    @model_validator(mode="after")
    def consistent(self):
        p = validate_payload(self.payload, self.event_type)
        replacement = p.get("replacement_ref")
        if replacement is not None and not re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            replacement,
        ):
            raise ValueError("REPLACEMENT_MUST_BE_SOURCE_UUID")
        if self.aggregate_id.lower() != p["source_id"].lower():
            raise ValueError("SOURCE_MISMATCH")
        if p.get("restriction_revision", 0) > 2**63 - 1:
            raise ValueError("REVISION_OVERFLOW")
        if self.event_type == "source.version.available":
            evidence = p["evidence_spans"]
            ids = [e["evidence_id"].lower() for e in evidence]
            if len(ids) != len(set(ids)) or any(
                e["source_version_id"].lower() != p["source_version_id"].lower() for e in evidence
            ):
                raise ValueError("EVIDENCE_VERSION_MISMATCH")
            if any(
                not {i.lower() for i in s["evidence_ids"]} <= set(ids)
                for s in p["posting_sections"]
            ):
                raise ValueError("SECTION_EVIDENCE_MISSING")
        return self

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        schema = handler(core_schema)
        # Keep the producer schema embedded with local refs, including in nested DTOs.
        schema["properties"]["payload"] = inline_schema(PAYLOAD_SCHEMA)
        schema["allOf"] = [
            {
                "if": {"properties": {"event_type": {"const": event_type}}},
                "then": {"properties": {"payload": inline_schema(PAYLOAD_SCHEMA["$defs"][name])}},
            }
            for event_type, name in TYPES.items()
        ]
        restriction_schema = schema["allOf"][-1]["then"]["properties"]["payload"]
        restriction_schema["properties"]["replacement_ref"] = {
            "type": ["string", "null"],
            "format": "uuid",
            "pattern": r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
        }
        return schema


class IndexKey(Strict):
    source_version_id: UUIDText
    extraction_revision_id: UUIDText
    representation: Annotated[str, Field(min_length=1)]
    normalization_version: Annotated[str, Field(min_length=1)]


class Replay(Strict):
    schema_version: Version
    source_id: UUIDText
    after_cursor: Cursor
    high_watermark: Cursor
    retention_floor_cursor: Cursor
    events: Annotated[list[Event], Field(max_length=500)]

    @model_validator(mode="after")
    def consistent(self):
        revisions = [e.revision for e in self.events]
        if (
            self.high_watermark < max(self.after_cursor, self.retention_floor_cursor)
            or revisions != sorted(set(revisions))
            or any(e.aggregate_id.lower() != self.source_id for e in self.events)
            or any(not self.after_cursor < n <= self.high_watermark for n in revisions)
        ):
            raise ValueError("REPLAY_INCONSISTENT")
        return self


class Snapshot(Strict):
    schema_version: Version
    source_id: UUIDText
    as_of: DateTime
    event_cursor: Cursor
    restriction_revision: Cursor
    complete: Literal[True]
    versions: Annotated[list[Event], Field(max_length=500)]
    restrictions: Annotated[list[Event], Field(max_length=500)]
    observation: Event | None

    @model_validator(mode="after")
    def consistent(self):
        groups = [
            (self.versions, "source.version.available", "source_version_id"),
            (self.restrictions, "source.restriction.changed", "restriction_id"),
        ]
        for events, kind, key in groups:
            if any(e.event_type != kind for e in events):
                raise ValueError("SNAPSHOT_TYPE_INVALID")
            ids = [e.payload[key].lower() for e in events]
            if len(set(ids)) != len(ids):
                raise ValueError("SNAPSHOT_DUPLICATE_STATE")
        if self.observation and self.observation.event_type != "source.observation.changed":
            raise ValueError("SNAPSHOT_TYPE_INVALID")
        restriction_order = [
            e.payload["restriction_revision"]
            for e in sorted(self.restrictions, key=lambda e: e.revision)
        ]
        if restriction_order != sorted(set(restriction_order)):
            raise ValueError("SNAPSHOT_RESTRICTION_ORDER_INVALID")
        events = (
            self.versions + self.restrictions + ([self.observation] if self.observation else [])
        )
        if (
            len({e.revision for e in events}) != len(events)
            or len({e.event_id.lower() for e in events}) != len(events)
            or any(
                e.aggregate_id.lower() != self.source_id
                or e.revision > self.event_cursor
                or datetime.fromisoformat(e.occurred_at) > datetime.fromisoformat(self.as_of)
                for e in events
            )
            or max((e.payload["restriction_revision"] for e in self.restrictions), default=0)
            != self.restriction_revision
            or self.restriction_revision > self.event_cursor
        ):
            raise ValueError("SNAPSHOT_INCONSISTENT")
        return self


class Document(Strict):
    document_id: UUIDText
    evidence_id: UUIDText
    text: Annotated[str, Field(min_length=1, max_length=100_000)]


class IndexRequest(Strict):
    schema_version: Version
    source_id: UUIDText
    event_cursor: Cursor
    restriction_revision: Cursor
    index_key: IndexKey
    retention_scope: Literal["excerpts_only"]
    expires_at: UTC
    documents: Annotated[list[Document], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def consistent(self):
        if len({d.document_id for d in self.documents}) != len(self.documents):
            raise ValueError("DUPLICATE_DOCUMENT")
        return self


Reason = Literal[
    "UNKNOWN_SOURCE",
    "CONFLICT",
    "EVENT_GAP",
    "RESTRICTION_GAP",
    "RESTRICTED",
    "OBSERVATION_BLOCKED",
    "POLICY_BLOCKED",
    "VERSION_UNAVAILABLE",
    "INDEX_PENDING",
    "INDEX_MISMATCH",
    "INDEX_FAILED",
    "BODY_EXPIRED",
    "READY",
]


class Status(Strict):
    schema_version: Version
    source_id: UUIDText
    event_cursor: Cursor
    required_event_cursor: Cursor
    restriction_revision: Cursor
    required_restriction_revision: Cursor
    generation: Cursor
    restriction_scope: Literal["source", "version"]
    reason: Reason
    index_ack: bool
    index_key: IndexKey | None
    history_complete: bool

    @model_validator(mode="after")
    def consistent(self):
        if self.index_ack != (self.reason == "READY") or (
            self.index_ack
            and (
                self.index_key is None
                or self.event_cursor != self.required_event_cursor
                or self.restriction_revision != self.required_restriction_revision
            )
        ):
            raise ValueError("READINESS_INCONSISTENT")
        return self


class Signal(Status):
    event_type: Literal["w3.source.usability.changed"]
    signal_id: UUIDText
    usable: bool

    @model_validator(mode="after")
    def usable_consistent(self):
        if self.usable != self.index_ack:
            raise ValueError("SIGNAL_INCONSISTENT")
        return self


class Receipt(Status):
    receipt: Literal["COMMITTED"]
    event_id: UUIDText
    outcome: Literal["APPLIED", "DUPLICATE", "STALE", "GAP", "CONFLICT"]


class RecoveryResponse(Status):
    outcome: Literal[
        "REPLAYED",
        "SNAPSHOT_REQUIRED",
        "CURSOR_AHEAD",
        "CONFLICT",
        "STALE_SNAPSHOT",
        "SNAPSHOT_APPLIED",
        "INCOMPLETE",
    ]


class DeliveryReceipt(Strict):
    signal_id: UUIDText


class DeliveryResponse(Strict):
    delivered: Literal[True]


SCHEMAS = dict(
    event=Event,
    replay=Replay,
    snapshot=Snapshot,
    status=Status,
    signal=Signal,
    receipt=Receipt,
    recovery=RecoveryResponse,
    knowledge=StructureResponse,
    **{
        "index-request": IndexRequest,
        "delivery-receipt": DeliveryReceipt,
        "delivery-response": DeliveryResponse,
    },
)

ReplayBatch = Replay
