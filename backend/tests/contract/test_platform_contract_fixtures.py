from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value: dict[str, Any] = json.load(stream)
    return value


def _validator(contract_root: Path, relative_schema_path: str) -> Draft202012Validator:
    schema = _load(contract_root / relative_schema_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.mark.parametrize(
    "fixture_path",
    [
        "fixtures/v1/common/source-restriction-changed.json",
        "fixtures/v1/common/source-restriction-replay.json",
        "fixtures/v1/common/source-revision-gap.json",
    ],
)
def test_common_event_fixtures_match_the_public_envelope(
    contract_root: Path, fixture_path: str
) -> None:
    validator = _validator(contract_root, "common/v1/event-envelope.schema.json")

    assert list(validator.iter_errors(_load(contract_root / fixture_path))) == []


def test_common_envelope_rejects_private_fields_in_a_public_payload(contract_root: Path) -> None:
    validator = _validator(contract_root, "common/v1/event-envelope.schema.json")
    fixture = _load(contract_root / "fixtures/v1/common/invalid-public-private-field.json")

    assert list(validator.iter_errors(fixture))
