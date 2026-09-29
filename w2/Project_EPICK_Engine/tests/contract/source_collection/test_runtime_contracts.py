from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ValidationError

from epick_engine.source_collection.contracts import (
    CollectionCommand,
    CollectionResult,
    SourceEnvelope,
    SourceEvent,
)

ENGINE_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_DIR = ENGINE_ROOT.parent / "specs" / "001-official-source-collection" / "contracts"


def _load_json(name: str) -> dict[str, Any]:
    with (CONTRACT_DIR / name).open(encoding="utf-8") as stream:
        value: dict[str, Any] = json.load(stream)
    return value


EXAMPLES = _load_json("examples.json")["examples"]
EXAMPLES_BY_NAME = {example["name"]: example["value"] for example in EXAMPLES}
SCHEMA_VALIDATOR = Draft202012Validator(
    _load_json("source-collection.schema.json"),
    format_checker=FormatChecker(),
)


def _runtime_model(value: dict[str, Any]) -> type[BaseModel]:
    if "event_type" in value:
        return SourceEvent
    if "authenticated_owner_ref" in value:
        return CollectionCommand
    if "completion_kind" in value:
        return CollectionResult
    return SourceEnvelope


@pytest.mark.parametrize(
    "example",
    EXAMPLES,
    ids=[example["name"] for example in EXAMPLES],
)
def test_stored_examples_round_trip_through_runtime_models(example: dict[str, Any]) -> None:
    model = _runtime_model(example["value"]).model_validate(example["value"])
    serialized = model.model_dump(mode="json")

    assert list(SCHEMA_VALIDATOR.iter_errors(serialized)) == []


def test_command_core_decision_must_match_input_version() -> None:
    command = deepcopy(EXAMPLES_BY_NAME["initial-policy-command"])
    command["core_source_decision"]["analysis_input_version"] += 1

    with pytest.raises(ValidationError, match="analysis_input_version"):
        CollectionCommand.model_validate(command)


def test_command_does_not_default_a_missing_core_decision() -> None:
    command = deepcopy(EXAMPLES_BY_NAME["initial-policy-command"])
    del command["core_source_decision"]

    with pytest.raises(ValidationError, match="core_source_decision"):
        CollectionCommand.model_validate(command)


def test_non_policy_resume_requires_a_positive_policy_revision() -> None:
    command = deepcopy(EXAMPLES_BY_NAME["initial-policy-command"])
    command["resume_stage"] = "fetch"

    with pytest.raises(ValidationError, match="policy_revision"):
        CollectionCommand.model_validate(command)


def test_result_rejects_duplicate_action_codes() -> None:
    result = deepcopy(EXAMPLES_BY_NAME["core-failure-and-rate-limit"])
    result["required_actions"].append(deepcopy(result["required_actions"][0]))

    with pytest.raises(ValidationError, match="duplicate required action"):
        CollectionResult.model_validate(result)


def test_result_action_and_failure_must_reference_the_result_source() -> None:
    result = deepcopy(EXAMPLES_BY_NAME["core-failure-and-rate-limit"])
    result["required_actions"][0]["context"]["source_id"] = "00000000-0000-4000-8000-000000000999"

    with pytest.raises(ValidationError, match="required action source_id"):
        CollectionResult.model_validate(result)

    result = deepcopy(EXAMPLES_BY_NAME["core-failure-and-rate-limit"])
    result["failures"][0]["source_id"] = "00000000-0000-4000-8000-000000000999"
    with pytest.raises(ValidationError, match="failure source_id"):
        CollectionResult.model_validate(result)


def test_event_aggregate_must_match_payload_source() -> None:
    event = deepcopy(EXAMPLES_BY_NAME["source.observation.changed"])
    event["aggregate_id"] = "00000000-0000-4000-8000-000000000999"

    with pytest.raises(ValidationError, match="aggregate_id"):
        SourceEvent.model_validate(event)


def test_normalized_locator_end_must_be_greater_than_start() -> None:
    envelope = deepcopy(EXAMPLES_BY_NAME["source-envelope"])
    locator = envelope["evidence_spans"][0]["locator"]
    locator.update(
        {
            "kind": "normalized_text",
            "value": "normalized body",
            "normalization_version": "text-v1",
            "start": 10,
            "end": 10,
        }
    )

    with pytest.raises(ValidationError, match="end must be greater"):
        SourceEnvelope.model_validate(envelope)
