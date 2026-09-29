from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from epick_engine.source_collection.contracts import AccuracyStatus
from epick_engine.source_collection.restriction_service import (
    RestrictionMutation,
    RestrictionMutationDenied,
    SourceRestrictionService,
    _canonical_request_bytes,
    _canonical_request_hash,
)

REQUEST_ID = UUID("10000000-0000-0000-0000-000000000001")
SOURCE_ID = UUID("20000000-0000-0000-0000-000000000002")
RESTRICTION_ID = UUID("30000000-0000-0000-0000-000000000003")
VERSION_ID = UUID("40000000-0000-0000-0000-000000000004")
REPLACEMENT_ID = UUID("50000000-0000-0000-0000-000000000005")
CHANGED_AT = datetime(2026, 9, 20, 10, 30, 15, 123456, tzinfo=UTC)


def _mutation(**updates: object) -> RestrictionMutation:
    values: dict[str, object] = {
        "request_id": REQUEST_ID,
        "kind": "create",
        "source_id": SOURCE_ID,
        "source_version_id": None,
        "restriction_id": None,
        "accuracy_status": AccuracyStatus.ERROR_CONFIRMED,
        "reason_code": "CONFIRMED_오류",
        "evidence_refs": ("fixture:첫째", "fixture:둘째"),
        "changed_at": CHANGED_AT,
        "replacement_ref": None,
    }
    values.update(updates)
    return RestrictionMutation(**values)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"restriction_id": RESTRICTION_ID}, "create"),
        ({"kind": "clear"}, "restriction"),
        ({"kind": "reactivate"}, "restriction"),
        ({"reason_code": "   "}, "reason"),
        ({"reason_code": "BAD\u0000REASON"}, "reason"),
        ({"changed_at": CHANGED_AT.replace(tzinfo=None)}, "timezone"),
    ],
)
def test_mutation_rejects_invalid_kind_identity_reason_and_timestamp(
    updates: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        _mutation(**updates)


def test_mutation_forbids_unknown_input_fields() -> None:
    with pytest.raises(ValidationError, match="extra"):
        _mutation(authorized=True)


def test_canonical_bytes_are_sorted_compact_utf8_and_include_profile() -> None:
    request = _mutation(
        kind="reactivate",
        source_version_id=VERSION_ID,
        restriction_id=RESTRICTION_ID,
        replacement_ref=REPLACEMENT_ID,
    )

    assert (
        _canonical_request_bytes(request)
        == (
            '{"accuracy_status":"error_confirmed","changed_at":"2026-09-20T10:30:15.123456+00:00",'
            '"evidence_refs":["fixture:첫째","fixture:둘째"],"kind":"reactivate",'
            '"profile":"w2.restriction.mutation.v1","reason_code":"CONFIRMED_오류",'
            '"replacement_ref":"50000000-0000-0000-0000-000000000005",'
            '"restriction_id":"30000000-0000-0000-0000-000000000003",'
            '"source_id":"20000000-0000-0000-0000-000000000002",'
            '"source_version_id":"40000000-0000-0000-0000-000000000004"}'
        ).encode()
    )


def test_hash_excludes_request_id_and_normalizes_equal_instants_to_utc() -> None:
    offset = timezone(timedelta(hours=9))
    same_instant = CHANGED_AT.astimezone(offset)

    first = _mutation(request_id=REQUEST_ID, changed_at=CHANGED_AT)
    second = _mutation(
        request_id=UUID("10000000-0000-0000-0000-000000000099"),
        changed_at=same_instant,
    )

    assert _canonical_request_hash(first) == _canonical_request_hash(second)


@pytest.mark.parametrize(
    "updates",
    [
        {"source_id": UUID("20000000-0000-0000-0000-000000000099")},
        {"source_version_id": VERSION_ID},
        {"accuracy_status": AccuracyStatus.SUPERSEDED},
        {"reason_code": "OTHER_REASON"},
        {"evidence_refs": ("fixture:둘째", "fixture:첫째")},
        {"changed_at": CHANGED_AT + timedelta(microseconds=1)},
        {"replacement_ref": REPLACEMENT_ID},
    ],
)
def test_hash_changes_when_any_create_semantic_input_changes(
    updates: dict[str, object],
) -> None:
    assert _canonical_request_hash(_mutation()) != _canonical_request_hash(_mutation(**updates))


@pytest.mark.parametrize(
    "updates",
    [
        {"kind": "clear"},
        {"restriction_id": UUID("30000000-0000-0000-0000-000000000099")},
    ],
)
def test_hash_includes_existing_target_identity_and_operation(
    updates: dict[str, object],
) -> None:
    original_values: dict[str, object] = {
        "kind": "reactivate",
        "restriction_id": RESTRICTION_ID,
    }
    changed_values = {**original_values, **updates}
    original = _mutation(**original_values)
    changed = _mutation(**changed_values)

    assert _canonical_request_hash(original) != _canonical_request_hash(changed)


class _RejectingAuthority:
    def authorize(self, *, request: RestrictionMutation) -> str:
        raise RestrictionMutationDenied()


class _ExplodingSessionFactory:
    def begin(self) -> object:
        raise AssertionError("database access happened before authority validation")


def test_denied_authority_prevents_session_creation() -> None:
    service = SourceRestrictionService(
        session_factory=_ExplodingSessionFactory(),  # type: ignore[arg-type]
        authority=_RejectingAuthority(),
    )

    with pytest.raises(RestrictionMutationDenied, match="^restriction mutation denied$"):
        service.apply(_mutation())


def test_authority_dependency_is_mandatory() -> None:
    with pytest.raises(TypeError):
        SourceRestrictionService(session_factory=_ExplodingSessionFactory())  # type: ignore[call-arg,arg-type]
