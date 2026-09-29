from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

ENGINE_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_DIR = ENGINE_ROOT.parent / "specs" / "001-official-source-collection" / "contracts"
SCHEMA_PATH = CONTRACT_DIR / "source-collection.schema.json"
EXAMPLES_PATH = CONTRACT_DIR / "examples.json"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value: dict[str, Any] = json.load(stream)
    return value


SCHEMA = _load_json(SCHEMA_PATH)
EXAMPLES = _load_json(EXAMPLES_PATH)["examples"]
EXAMPLES_BY_NAME = {example["name"]: example["value"] for example in EXAMPLES}
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def _assert_valid(value: dict[str, Any]) -> None:
    errors = sorted(VALIDATOR.iter_errors(value), key=lambda error: list(error.path))
    assert errors == []


def _assert_invalid(value: dict[str, Any]) -> None:
    assert list(VALIDATOR.iter_errors(value))


def test_contract_schema_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize(
    "example",
    EXAMPLES,
    ids=[example["name"] for example in EXAMPLES],
)
def test_all_stored_synthetic_examples_match_the_contract(example: dict[str, Any]) -> None:
    _assert_valid(example["value"])


def test_initial_policy_command_allows_null_policy_revision() -> None:
    command = deepcopy(EXAMPLES_BY_NAME["initial-policy-command"])

    assert command["resume_stage"] == "policy"
    assert command["policy_revision"] is None
    _assert_valid(command)


def test_non_policy_resume_rejects_null_policy_revision() -> None:
    command = deepcopy(EXAMPLES_BY_NAME["initial-policy-command"])
    command["resume_stage"] = "fetch"

    _assert_invalid(command)


def test_null_result_policy_revision_only_allows_policy_stage_failure() -> None:
    result = deepcopy(EXAMPLES_BY_NAME["unsupported-pdf"])
    result["policy_revision"] = None
    result["failures"][0]["stage"] = "policy"
    result["required_actions"] = []
    _assert_valid(result)

    wrong_stage = deepcopy(result)
    wrong_stage["failures"][0]["stage"] = "fetch"
    _assert_invalid(wrong_stage)

    false_partial = deepcopy(result)
    false_partial["completion_kind"] = "partial"
    _assert_invalid(false_partial)


def test_required_actions_are_typed_by_code() -> None:
    result = deepcopy(EXAMPLES_BY_NAME["core-failure-and-rate-limit"])
    core_action = result["required_actions"][0]
    del core_action["context"]["choices"]

    _assert_invalid(result)


def test_source_event_discriminator_rejects_a_different_payload_shape() -> None:
    event = deepcopy(EXAMPLES_BY_NAME["source.version.available"])
    event["event_type"] = "source.observation.changed"

    _assert_invalid(event)


def test_source_envelope_rejects_body_reference_without_body_retention() -> None:
    envelope = deepcopy(EXAMPLES_BY_NAME["source-envelope"])
    envelope["normalized_body_ref"] = "private://synthetic/body"

    _assert_invalid(envelope)


@pytest.mark.parametrize("field", ["user_id", "job_id", "project_id"])
def test_public_source_envelope_rejects_private_fields(field: str) -> None:
    envelope = deepcopy(EXAMPLES_BY_NAME["source-envelope"])
    envelope[field] = "00000000-0000-4000-8000-000000000999"

    _assert_invalid(envelope)


def test_complete_extraction_rejects_empty_evidence() -> None:
    envelope = deepcopy(EXAMPLES_BY_NAME["source-envelope"])
    envelope["evidence_spans"] = []

    _assert_invalid(envelope)


def test_public_event_rejects_private_fields() -> None:
    event = deepcopy(EXAMPLES_BY_NAME["source.observation.changed"])
    event["payload"]["authenticated_owner_ref"] = "private-owner"

    _assert_invalid(event)
