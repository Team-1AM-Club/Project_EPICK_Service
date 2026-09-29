"""Versioned W2-private deletion payload contract tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from epick_engine.source_collection import worker
from epick_engine.source_collection.worker import WorkerContractViolation

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "w2_private_deletion"
CONTRACTS = Path(__file__).resolve().parents[3] / "contracts" / "w2-private"


def _load(name: str) -> dict[str, Any]:
    directory = CONTRACTS if name.endswith(".schema.json") else FIXTURES
    value: dict[str, Any] = json.loads((directory / name).read_text(encoding="utf-8"))
    return value


def _validator(name: str) -> Draft202012Validator:
    schema = _load(name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _command_type() -> Any:
    command_type = getattr(worker, "PrivateDeletionCommand", None)
    assert command_type is not None, "PrivateDeletionCommand is missing"
    return command_type


def _ack_type() -> Any:
    ack_type = getattr(worker, "PrivateDeletionAcknowledgement", None)
    assert ack_type is not None, "PrivateDeletionAcknowledgement is missing"
    return ack_type


def test_private_deletion_command_fixture_validates_without_coercion() -> None:
    command_type = _command_type()
    raw = _load("valid-command.json")

    _validator("private-deletion-command.schema.json").validate(raw)
    command = command_type.from_mapping(raw)

    assert command.deletion_id == UUID(raw["deletion_id"])
    assert command.owner_user_id == UUID(raw["owner_user_id"])
    assert command.deletion_epoch == 7
    assert command.attempt_ids == frozenset({UUID(raw["attempt_ids"][0])})
    assert command.request_deduplication_ids == frozenset(
        {UUID(raw["request_deduplication_ids"][0])}
    )
    assert command.private_reference_keys == frozenset(raw["private_reference_keys"])


def test_private_deletion_command_accepts_signed_64_bit_maximum_epoch() -> None:
    command_type = _command_type()
    raw = deepcopy(_load("valid-command.json"))
    raw["deletion_epoch"] = 9_223_372_036_854_775_807

    _validator("private-deletion-command.schema.json").validate(raw)
    command = command_type.from_mapping(raw)

    assert command.deletion_epoch == 9_223_372_036_854_775_807


def test_private_deletion_command_rejects_epoch_above_signed_64_bit_maximum() -> None:
    command_type = _command_type()
    raw = deepcopy(_load("valid-command.json"))
    raw["deletion_epoch"] = 9_223_372_036_854_775_808

    assert list(_validator("private-deletion-command.schema.json").iter_errors(raw))
    with pytest.raises(WorkerContractViolation):
        command_type.from_mapping(raw)


def test_private_deletion_ack_fixture_validates_without_coercion() -> None:
    ack_type = _ack_type()
    raw = _load("valid-ack-applied.json")

    _validator("private-deletion-ack.schema.json").validate(raw)
    acknowledgement = ack_type.from_mapping(raw)

    assert acknowledgement.deletion_id == UUID(raw["deletion_id"])
    assert acknowledgement.owner_user_id == UUID(raw["owner_user_id"])
    assert acknowledgement.deletion_epoch == 7
    assert acknowledgement.outcome == "APPLIED"


def test_private_deletion_ack_accepts_signed_64_bit_maximum_epoch() -> None:
    ack_type = _ack_type()
    raw = deepcopy(_load("valid-ack-applied.json"))
    raw["deletion_epoch"] = 9_223_372_036_854_775_807

    _validator("private-deletion-ack.schema.json").validate(raw)
    acknowledgement = ack_type.from_mapping(raw)
    direct_acknowledgement = ack_type(
        deletion_id=UUID(raw["deletion_id"]),
        owner_user_id=UUID(raw["owner_user_id"]),
        deletion_epoch=9_223_372_036_854_775_807,
        outcome="APPLIED",
    )

    assert acknowledgement.deletion_epoch == 9_223_372_036_854_775_807
    assert direct_acknowledgement.deletion_epoch == 9_223_372_036_854_775_807


def test_private_deletion_ack_rejects_epoch_above_signed_64_bit_maximum() -> None:
    ack_type = _ack_type()
    raw = deepcopy(_load("valid-ack-applied.json"))
    raw["deletion_epoch"] = 9_223_372_036_854_775_808

    assert list(_validator("private-deletion-ack.schema.json").iter_errors(raw))
    with pytest.raises(WorkerContractViolation):
        ack_type.from_mapping(raw)
    with pytest.raises(WorkerContractViolation):
        ack_type(
            deletion_id=UUID(raw["deletion_id"]),
            owner_user_id=UUID(raw["owner_user_id"]),
            deletion_epoch=9_223_372_036_854_775_808,
            outcome="APPLIED",
        )


@pytest.mark.parametrize("fixture", ["invalid-zero-epoch.json", "invalid-public-reference.json"])
def test_private_deletion_rejects_invalid_or_public_scope(fixture: str) -> None:
    command_type = _command_type()
    raw = _load(fixture)

    assert list(_validator("private-deletion-command.schema.json").iter_errors(raw))
    with pytest.raises(WorkerContractViolation):
        command_type.from_mapping(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("deletion_epoch", True),
        ("deletion_epoch", "7"),
        ("deletion_id", "71000000000040008000000000000001"),
        ("attempt_ids", ["72000000-0000-4000-8000-000000000001"] * 2),
        ("private_reference_keys", [""]),
    ],
)
def test_private_deletion_command_rejects_coercion_and_duplicate_values(
    field: str, value: object
) -> None:
    command_type = _command_type()
    raw = deepcopy(_load("valid-command.json"))
    raw[field] = value

    with pytest.raises(WorkerContractViolation):
        command_type.from_mapping(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "w2.private-deletion-ack.v2"),
        ("deletion_epoch", 0),
        ("deletion_epoch", True),
        ("outcome", "REJECTED"),
        ("source_id", "74000000-0000-4000-8000-000000000001"),
    ],
)
def test_private_deletion_ack_rejects_invalid_or_public_scope(field: str, value: object) -> None:
    ack_type = _ack_type()
    raw = deepcopy(_load("valid-ack-applied.json"))
    raw[field] = value

    assert list(_validator("private-deletion-ack.schema.json").iter_errors(raw))
    with pytest.raises(WorkerContractViolation):
        ack_type.from_mapping(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("deletion_id", "71000000-0000-4000-8000-000000000001"),
        ("owner_user_id", "00000000-0000-4000-8000-000000006301"),
        ("deletion_epoch", 0),
        ("deletion_epoch", True),
        ("deletion_epoch", 9_223_372_036_854_775_808),
        ("attempt_ids", frozenset({"72000000-0000-4000-8000-000000000001"})),
        (
            "request_deduplication_ids",
            frozenset({"73000000-0000-4000-8000-000000000001"}),
        ),
        ("private_reference_keys", frozenset({""})),
    ],
)
def test_private_deletion_command_direct_construction_enforces_invariants(
    field: str, value: object
) -> None:
    command_type = _command_type()
    values = {
        "deletion_id": UUID("71000000-0000-4000-8000-000000000001"),
        "owner_user_id": UUID("00000000-0000-4000-8000-000000006301"),
        "deletion_epoch": 7,
        "attempt_ids": frozenset({UUID("72000000-0000-4000-8000-000000000001")}),
        "request_deduplication_ids": frozenset({UUID("73000000-0000-4000-8000-000000000001")}),
        "private_reference_keys": frozenset({"w1-private:attempt:72000000"}),
    }
    values[field] = value

    with pytest.raises(WorkerContractViolation):
        command_type(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("deletion_id", "71000000-0000-4000-8000-000000000001"),
        ("owner_user_id", "00000000-0000-4000-8000-000000006301"),
        ("deletion_epoch", 0),
        ("deletion_epoch", True),
        ("outcome", "REJECTED"),
    ],
)
def test_private_deletion_ack_direct_construction_enforces_invariants(
    field: str, value: object
) -> None:
    ack_type = _ack_type()
    values = {
        "deletion_id": UUID("71000000-0000-4000-8000-000000000001"),
        "owner_user_id": UUID("00000000-0000-4000-8000-000000006301"),
        "deletion_epoch": 7,
        "outcome": "APPLIED",
    }
    values[field] = value

    with pytest.raises(WorkerContractViolation):
        ack_type(**values)


@pytest.mark.parametrize(
    ("fixture", "schema", "field"),
    [
        ("valid-command.json", "private-deletion-command.schema.json", "deletion_id"),
        ("valid-command.json", "private-deletion-command.schema.json", "owner_user_id"),
        ("valid-command.json", "private-deletion-command.schema.json", "attempt_ids"),
        (
            "valid-command.json",
            "private-deletion-command.schema.json",
            "request_deduplication_ids",
        ),
        ("valid-ack-applied.json", "private-deletion-ack.schema.json", "deletion_id"),
        ("valid-ack-applied.json", "private-deletion-ack.schema.json", "owner_user_id"),
    ],
)
def test_uppercase_letter_bearing_uuid_is_rejected_by_schema_and_python(
    fixture: str, schema: str, field: str
) -> None:
    raw = deepcopy(_load(fixture))
    uppercase_uuid = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    raw[field] = [uppercase_uuid] if field.endswith("_ids") else uppercase_uuid
    payload_type = _command_type() if fixture == "valid-command.json" else _ack_type()

    assert list(_validator(schema).iter_errors(raw))
    with pytest.raises(WorkerContractViolation):
        payload_type.from_mapping(raw)
