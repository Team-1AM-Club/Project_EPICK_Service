"""Versioned W2-owned private deletion scope contract tests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from epick_engine.source_collection.private_deletion_v2 import (
    PrivateDeletionAckV2,
    PrivateDeletionCommandV2,
    PrivateDeletionScope,
    command_digest_v2,
)
from epick_engine.source_collection.worker import WorkerContractViolation

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "w2_private_deletion_v2"
V1_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "w2_private_deletion"
CONTRACTS = Path(__file__).resolve().parents[3] / "contracts" / "w2-private"
PROJECT_ID = UUID("75000000-0000-4000-8000-000000000001")
SIGNED_64_MAX = 9_223_372_036_854_775_807


def _load_fixture(name: str) -> dict[str, Any]:
    value: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return value


def _load_v1_fixture(name: str) -> dict[str, Any]:
    value: dict[str, Any] = json.loads((V1_FIXTURES / name).read_text(encoding="utf-8"))
    return value


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.mark.parametrize(
    ("fixture", "scope"),
    [
        ("valid-account-command.json", PrivateDeletionScope(kind="ACCOUNT", project_id=None)),
        (
            "valid-project-command.json",
            PrivateDeletionScope(kind="PROJECT", project_id=PROJECT_ID),
        ),
    ],
)
def test_v2_command_fixtures_validate_and_round_trip_exact_scope(
    fixture: str, scope: PrivateDeletionScope
) -> None:
    raw = _load_fixture(fixture)

    _validator("private-deletion-command-v2.schema.json").validate(raw)
    command = PrivateDeletionCommandV2.from_mapping(raw)

    assert command.deletion_id == UUID(raw["deletion_id"])
    assert command.owner_user_id == UUID(raw["owner_user_id"])
    assert command.deletion_epoch == 7
    assert command.scope == scope
    assert command.to_mapping() == raw


@pytest.mark.parametrize(
    ("fixture", "outcome", "scope"),
    [
        (
            "valid-account-ack-applied.json",
            "APPLIED",
            PrivateDeletionScope(kind="ACCOUNT", project_id=None),
        ),
        (
            "valid-project-ack-duplicate.json",
            "DUPLICATE",
            PrivateDeletionScope(kind="PROJECT", project_id=PROJECT_ID),
        ),
    ],
)
def test_v2_ack_fixtures_validate_and_echo_scope_without_coercion(
    fixture: str, outcome: str, scope: PrivateDeletionScope
) -> None:
    raw = _load_fixture(fixture)

    _validator("private-deletion-ack-v2.schema.json").validate(raw)
    acknowledgement = PrivateDeletionAckV2.from_mapping(raw)

    assert acknowledgement.scope == scope
    assert acknowledgement.outcome == outcome
    assert acknowledgement.to_mapping() == raw


def test_v2_command_digest_distinguishes_changed_scope_for_same_deletion_id() -> None:
    account = PrivateDeletionCommandV2.from_mapping(_load_fixture("valid-account-command.json"))
    project = PrivateDeletionCommandV2.from_mapping(_load_fixture("valid-project-command.json"))

    assert account.deletion_id == project.deletion_id
    assert account.owner_user_id == project.owner_user_id
    assert account.deletion_epoch == project.deletion_epoch
    assert command_digest_v2(account) == (
        "0f9b44c884cb98625abfbc812db6d977bc64931f3366229f8cdbefe8df07393a"
    )
    assert command_digest_v2(project) == (
        "0f018db8f1bb836c085cbb81f62698803c8e7b6d34199b5d29f70cf78053345b"
    )
    assert command_digest_v2(account) != command_digest_v2(project)


@pytest.mark.parametrize(
    "scope",
    [
        {},
        {"type": "ACCOUNT", "project_id": str(PROJECT_ID)},
        {"type": "PROJECT"},
        {"type": "PROJECT", "project_id": str(PROJECT_ID), "extra": True},
        {"type": "ORGANIZATION"},
    ],
)
def test_v2_command_rejects_nonexclusive_or_incomplete_scope(scope: dict[str, object]) -> None:
    raw = deepcopy(_load_fixture("valid-account-command.json"))
    raw["scope"] = scope

    assert list(_validator("private-deletion-command-v2.schema.json").iter_errors(raw))
    with pytest.raises(WorkerContractViolation):
        PrivateDeletionCommandV2.from_mapping(raw)


@pytest.mark.parametrize(
    ("fixture", "schema", "payload_type", "extra_field"),
    [
        (
            "valid-account-command.json",
            "private-deletion-command-v2.schema.json",
            PrivateDeletionCommandV2,
            "attempt_ids",
        ),
        (
            "valid-account-ack-applied.json",
            "private-deletion-ack-v2.schema.json",
            PrivateDeletionAckV2,
            "receipt_id",
        ),
    ],
)
def test_v2_payloads_reject_extra_top_level_keys(
    fixture: str, schema: str, payload_type: Any, extra_field: str
) -> None:
    raw = deepcopy(_load_fixture(fixture))
    raw[extra_field] = []

    assert list(_validator(schema).iter_errors(raw))
    with pytest.raises(WorkerContractViolation):
        payload_type.from_mapping(raw)


@pytest.mark.parametrize(
    ("fixture", "schema", "payload_type", "field"),
    [
        (
            "valid-project-command.json",
            "private-deletion-command-v2.schema.json",
            PrivateDeletionCommandV2,
            "deletion_id",
        ),
        (
            "valid-project-command.json",
            "private-deletion-command-v2.schema.json",
            PrivateDeletionCommandV2,
            "owner_user_id",
        ),
        (
            "valid-project-ack-duplicate.json",
            "private-deletion-ack-v2.schema.json",
            PrivateDeletionAckV2,
            "deletion_id",
        ),
    ],
)
def test_v2_payloads_reject_noncanonical_top_level_uuid_text(
    fixture: str, schema: str, payload_type: Any, field: str
) -> None:
    raw = deepcopy(_load_fixture(fixture))
    raw[field] = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"

    assert list(_validator(schema).iter_errors(raw))
    with pytest.raises(WorkerContractViolation):
        payload_type.from_mapping(raw)


@pytest.mark.parametrize(
    ("fixture", "schema", "payload_type"),
    [
        (
            "valid-project-command.json",
            "private-deletion-command-v2.schema.json",
            PrivateDeletionCommandV2,
        ),
        (
            "valid-project-ack-duplicate.json",
            "private-deletion-ack-v2.schema.json",
            PrivateDeletionAckV2,
        ),
    ],
)
def test_v2_payloads_reject_noncanonical_project_uuid_text(
    fixture: str, schema: str, payload_type: Any
) -> None:
    raw = deepcopy(_load_fixture(fixture))
    raw["scope"]["project_id"] = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"

    assert list(_validator(schema).iter_errors(raw))
    with pytest.raises(WorkerContractViolation):
        payload_type.from_mapping(raw)


@pytest.mark.parametrize("epoch", [True, 0, 9_223_372_036_854_775_808])
@pytest.mark.parametrize(
    ("fixture", "schema", "payload_type"),
    [
        (
            "valid-account-command.json",
            "private-deletion-command-v2.schema.json",
            PrivateDeletionCommandV2,
        ),
        (
            "valid-account-ack-applied.json",
            "private-deletion-ack-v2.schema.json",
            PrivateDeletionAckV2,
        ),
    ],
)
def test_v2_payloads_reject_bool_zero_and_overflow_epochs(
    epoch: object, fixture: str, schema: str, payload_type: Any
) -> None:
    raw = deepcopy(_load_fixture(fixture))
    raw["deletion_epoch"] = epoch

    assert list(_validator(schema).iter_errors(raw))
    with pytest.raises(WorkerContractViolation):
        payload_type.from_mapping(raw)


@pytest.mark.parametrize(
    ("fixture", "schema", "payload_type"),
    [
        (
            "valid-account-command.json",
            "private-deletion-command-v2.schema.json",
            PrivateDeletionCommandV2,
        ),
        (
            "valid-account-ack-applied.json",
            "private-deletion-ack-v2.schema.json",
            PrivateDeletionAckV2,
        ),
    ],
)
def test_v2_payloads_accept_signed_64_bit_maximum_epoch(
    fixture: str, schema: str, payload_type: Any
) -> None:
    raw = deepcopy(_load_fixture(fixture))
    raw["deletion_epoch"] = SIGNED_64_MAX

    _validator(schema).validate(raw)
    assert payload_type.from_mapping(raw).deletion_epoch == SIGNED_64_MAX


def test_v2_ack_rejects_stale_outcome() -> None:
    raw = deepcopy(_load_fixture("valid-account-ack-applied.json"))
    raw["outcome"] = "STALE"

    assert list(_validator("private-deletion-ack-v2.schema.json").iter_errors(raw))
    with pytest.raises(WorkerContractViolation):
        PrivateDeletionAckV2.from_mapping(raw)


def test_v2_scope_direct_construction_enforces_account_project_exclusivity() -> None:
    with pytest.raises(WorkerContractViolation):
        PrivateDeletionScope(kind="ACCOUNT", project_id=PROJECT_ID)
    with pytest.raises(WorkerContractViolation):
        PrivateDeletionScope(kind="PROJECT", project_id=None)


def test_v2_command_parser_rejects_v1_payload() -> None:
    with pytest.raises(WorkerContractViolation):
        PrivateDeletionCommandV2.from_mapping(_load_v1_fixture("valid-command.json"))
