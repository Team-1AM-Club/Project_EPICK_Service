from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_PATH = "w1/v1/w3-source-retirement.request.schema.json"


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        schema: dict[str, Any] = json.load(stream)
    Draft202012Validator.check_schema(schema)
    return schema


def _validator(contract_root: Path) -> Draft202012Validator:
    return Draft202012Validator(
        _load(contract_root / SCHEMA_PATH),
        format_checker=FormatChecker(),
    )


def _valid_command() -> dict[str, object]:
    return {
        "schema_version": "w1.private.w3-source-retirement/1.0",
        "command_id": "11111111-1111-4111-8111-111111111111",
        "operation": "RETIRE_SOURCE",
        "company_id": "22222222-2222-4222-8222-222222222222",
        "source_id": "33333333-3333-4333-8333-333333333333",
        "retired_at": "2026-09-20T09:00:00Z",
        "target_type": "W3_CORE_RUNTIME",
        "target_ref": "33333333-3333-4333-8333-333333333333",
    }


def test_source_retirement_schema_accepts_only_permanent_minimal_command(
    contract_root: Path,
) -> None:
    validator = _validator(contract_root)
    assert list(validator.iter_errors(_valid_command())) == []
    assert not validator.is_valid(_valid_command() | {"source_url": "https://example.invalid"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operation", "DELETE_OWNER"),
        ("target_type", "OTHER"),
        ("retired_at", 123),
        ("target_ref", "not-a-uuid"),
    ],
)
def test_source_retirement_schema_rejects_non_permanent_or_invalid_commands(
    contract_root: Path,
    field: str,
    value: object,
) -> None:
    command = _valid_command()
    command[field] = value
    assert not _validator(contract_root).is_valid(command)


def test_source_retirement_target_ref_is_documented_as_source_id(contract_root: Path) -> None:
    schema = _load(contract_root / SCHEMA_PATH)
    assert "equal source_id" in schema["properties"]["target_ref"]["description"]


def test_source_retirement_schema_matches_reviewed_candidate(contract_root: Path) -> None:
    candidate = (
        Path(__file__).parents[3]
        / "specs"
        / "007-w1-w3-runtime-integration"
        / "contracts"
        / "source-retirement-command.schema.json"
    )
    assert _load(contract_root / SCHEMA_PATH) == _load(candidate)
