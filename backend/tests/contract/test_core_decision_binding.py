from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from app.runtime.core_decision_binding import (
    CoreDecisionBindingError,
    validate_core_pin_payload_binding,
    validate_database_core_binding,
)


def _load(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as stream:
        value: dict[str, object] = json.load(stream)
    return value


def _w2_command_validator(contract_root: Path) -> Draft202012Validator:
    schema = _load(contract_root / "w2/v1/source-collection.command.schema.json")
    return Draft202012Validator(schema, format_checker=FormatChecker())


def test_core_dispatch_and_lookup_retain_the_same_canonical_w2_command(contract_root: Path) -> None:
    dispatch = _load(contract_root / "fixtures/v1/w1/private-w2-command-dispatch.json")
    lookup = _load(contract_root / "fixtures/v1/w1/private-command-lookup-available.json")
    payload = dispatch["payload"]
    pin = dispatch["core_decision_pin"]
    lookup_command = lookup["command"]

    assert isinstance(payload, dict)
    assert isinstance(pin, dict)
    assert payload == lookup_command
    validate_core_pin_payload_binding(pin=pin, w2_command=payload)


@pytest.mark.parametrize(
    "fixture_name",
    [
        "invalid-core-decision-binding-source-mismatch.json",
        "invalid-core-decision-binding-core-mismatch.json",
        "invalid-core-decision-binding-revision-mismatch.json",
        "invalid-core-decision-binding-owner-mismatch.json",
    ],
)
def test_core_binding_rejects_individually_schema_valid_mismatches(
    contract_root: Path, fixture_name: str
) -> None:
    dispatch = _load(contract_root / "fixtures/v1/w1" / fixture_name)
    payload = dispatch["payload"]
    pin = dispatch["core_decision_pin"]

    assert isinstance(payload, dict)
    assert isinstance(pin, dict)
    # The W2 Schema cannot express cross-object equality.  The W1 binding validator must.
    assert list(_w2_command_validator(contract_root).iter_errors(payload)) == []
    with pytest.raises(CoreDecisionBindingError, match="CORE_DECISION_BINDING_MISMATCH"):
        validate_core_pin_payload_binding(pin=pin, w2_command=payload)


def test_database_binding_rejects_owner_that_conflicts_with_the_pinned_scope(
    contract_root: Path,
) -> None:
    dispatch = _load(contract_root / "fixtures/v1/w1/private-w2-command-dispatch.json")
    pin = dispatch["core_decision_pin"]
    payload = dispatch["payload"]

    assert isinstance(pin, dict)
    assert isinstance(payload, dict)
    decision = SimpleNamespace(
        id=pin["decision_id"],
        decision_scope=pin["decision_scope"],
        company_id=pin["company_id"],
        question_version_id=pin["question_version_id"],
        source_id=pin["source_id"],
        analysis_input_version=pin["analysis_input_version"],
        decision_version=pin["decision_version"],
        decision_code=pin["decision_code"],
        decision_owner="W4",
        reason_code=pin["reason_code"],
    )
    source_link = SimpleNamespace(source_id=pin["source_id"])

    with pytest.raises(CoreDecisionBindingError, match="CORE_DECISION_BINDING_MISMATCH"):
        validate_database_core_binding(
            decision=decision,
            pin=pin,
            w2_command=payload,
            job_analysis_input_version=pin["analysis_input_version"],
            source_link=source_link,
        )
