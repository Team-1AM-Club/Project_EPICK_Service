"""Pure replay and atomic snapshot payloads from complete W2 public history."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from epick_engine.source_collection.contracts import (
    SourceEnvelope,
    SourceEvent,
    SourceEventType,
    SourceObservationSnapshot,
    SourceRestrictionSnapshot,
)
from epick_engine.source_collection.w3_public_transport import source_event_to_w3_wire
from epick_engine.source_collection.w3_recovery_store import RecoveryHistory

_SCHEMA_VERSION = "w3-c01/0.2-candidate"
_MAX_BATCH_SIZE = 500
_MAX_CURSOR = 2**63 - 1


class W3RecoveryPayloadError(RuntimeError):
    """Sanitized payload construction error with no event or Source data."""

    def __init__(self, code: str) -> None:
        super().__init__(f"W3 recovery payload failed: {code}")
        self.code = code


def _wire_revision(wire: dict[str, object]) -> int:
    revision = wire.get("revision")
    if type(revision) is not int:
        raise W3RecoveryPayloadError("INVALID_HISTORY")
    return revision


def _validated_wires(history: RecoveryHistory) -> tuple[dict[str, object], ...]:
    if (
        not isinstance(history, RecoveryHistory)
        or not isinstance(history.source_id, UUID)
        or type(history.high_watermark) is not int
        or not 1 <= history.high_watermark <= _MAX_CURSOR
        or type(history.restriction_revision) is not int
        or not 0 <= history.restriction_revision <= history.high_watermark
        or not isinstance(history.events, tuple)
        or len(history.events) != history.high_watermark
        or not isinstance(history.as_of, datetime)
        or history.as_of.utcoffset() is None
    ):
        raise W3RecoveryPayloadError("INVALID_HISTORY")

    event_ids: set[UUID] = set()
    restriction_revision = 0
    wires: list[dict[str, object]] = []
    for expected_revision, event in enumerate(history.events, start=1):
        if (
            not isinstance(event, SourceEvent)
            or not isinstance(event.event_id, UUID)
            or event.event_id in event_ids
            or type(event.aggregate_revision) is not int
            or event.aggregate_revision != expected_revision
            or event.aggregate_id != history.source_id
            or not isinstance(event.occurred_at, datetime)
            or event.occurred_at.utcoffset() is None
            or not isinstance(
                event.payload,
                (SourceEnvelope, SourceObservationSnapshot, SourceRestrictionSnapshot),
            )
            or event.payload.source_id != history.source_id
        ):
            raise W3RecoveryPayloadError("INVALID_HISTORY")
        event_ids.add(event.event_id)
        valid_payload = (
            event.event_type is SourceEventType.VERSION_AVAILABLE
            and isinstance(event.payload, SourceEnvelope)
            or event.event_type is SourceEventType.OBSERVATION_CHANGED
            and isinstance(event.payload, SourceObservationSnapshot)
            or event.event_type is SourceEventType.RESTRICTION_CHANGED
            and isinstance(event.payload, SourceRestrictionSnapshot)
        )
        if not valid_payload:
            raise W3RecoveryPayloadError("INVALID_HISTORY")
        if isinstance(event.payload, SourceRestrictionSnapshot):
            restriction_revision += 1
            if event.payload.restriction_revision != restriction_revision:
                raise W3RecoveryPayloadError("INVALID_HISTORY")
        try:
            wires.append(source_event_to_w3_wire(event))
        except Exception:
            raise W3RecoveryPayloadError("INVALID_HISTORY") from None
    if restriction_revision != history.restriction_revision:
        raise W3RecoveryPayloadError("INVALID_HISTORY")
    return tuple(wires)


def make_replay_batch(history: RecoveryHistory, *, after_cursor: int) -> dict[str, object]:
    """Return at most 500 contiguous events after W3's durable cursor."""

    if type(after_cursor) is not int or after_cursor < 0 or after_cursor > _MAX_CURSOR:
        raise W3RecoveryPayloadError("INVALID_CURSOR")
    wires = _validated_wires(history)
    if after_cursor > history.high_watermark:
        raise W3RecoveryPayloadError("INVALID_CURSOR")
    return {
        "schema_version": _SCHEMA_VERSION,
        "source_id": str(history.source_id),
        "after_cursor": after_cursor,
        "high_watermark": history.high_watermark,
        "retention_floor_cursor": 0,
        "events": list(wires[after_cursor : after_cursor + _MAX_BATCH_SIZE]),
    }


def make_snapshot(history: RecoveryHistory) -> dict[str, object]:
    """Fold complete history to the latest immutable event for each state ID."""

    wires = _validated_wires(history)
    versions: dict[UUID, dict[str, object]] = {}
    restrictions: dict[UUID, dict[str, object]] = {}
    observation: dict[str, object] | None = None
    for event, wire in zip(history.events, wires, strict=True):
        if isinstance(event.payload, SourceEnvelope):
            versions[event.payload.source_version_id] = wire
        elif isinstance(event.payload, SourceRestrictionSnapshot):
            restrictions[event.payload.restriction_id] = wire
        else:
            observation = wire
    if len(versions) > _MAX_BATCH_SIZE or len(restrictions) > _MAX_BATCH_SIZE:
        raise W3RecoveryPayloadError("SNAPSHOT_TOO_LARGE")

    selected = [*versions.values(), *restrictions.values()]
    if observation is not None:
        selected.append(observation)
    selected_revisions = {_wire_revision(event) for event in selected}
    included_at = [
        event.occurred_at
        for event in history.events
        if event.aggregate_revision in selected_revisions
    ]
    as_of = max(history.as_of, *included_at)
    return {
        "schema_version": _SCHEMA_VERSION,
        "source_id": str(history.source_id),
        "as_of": as_of.isoformat(),
        "event_cursor": history.high_watermark,
        "restriction_revision": history.restriction_revision,
        "complete": True,
        "versions": sorted(versions.values(), key=_wire_revision),
        "restrictions": sorted(restrictions.values(), key=_wire_revision),
        "observation": observation,
    }
