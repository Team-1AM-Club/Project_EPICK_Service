from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value: dict[str, Any] = json.load(stream)
    return value


@pytest.fixture
def result_validator(contract_root: Path) -> Draft202012Validator:
    schema = _load(contract_root / "w2/v1/source-collection.result.schema.json")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.mark.parametrize(
    "fixture_name",
    [
        "source-collection-result-complete.json",
        "source-collection-result-partial.json",
        "source-collection-result-failure.json",
        "source-collection-result-policy-failure.json",
    ],
)
def test_w2_result_fixtures_match_the_adopted_runtime_shape(
    contract_root: Path, result_validator: Draft202012Validator, fixture_name: str
) -> None:
    payload = _load(contract_root / "fixtures/v1/w2" / fixture_name)

    assert list(result_validator.iter_errors(payload)) == []


def test_partial_result_expresses_continue_limited_as_a_w2_core_action_choice(
    contract_root: Path, result_validator: Draft202012Validator
) -> None:
    payload = _load(contract_root / "fixtures/v1/w2/source-collection-result-partial.json")
    action = payload["required_actions"][0]

    assert action == {
        "code": "core_failure_decision",
        "label_ko": "결정 필요",
        "context": {
            "source_id": payload["source_id"],
            "core_decision_revision": 1,
            "choices": ["continue_limited", "stop", "retry"],
        },
    }
    standalone_action = deepcopy(payload)
    standalone_action["required_actions"][0]["code"] = "continue_limited"
    assert list(result_validator.iter_errors(standalone_action))


def test_null_policy_revision_is_limited_to_a_policy_stage_failure(
    contract_root: Path, result_validator: Draft202012Validator
) -> None:
    payload = _load(
        contract_root / "fixtures/v1/w2/source-collection-result-policy-failure.json"
    )

    assert payload["policy_revision"] is None
    assert payload["completion_kind"] == "none"
    assert [failure["stage"] for failure in payload["failures"]] == ["policy"]
    assert list(result_validator.iter_errors(payload)) == []

    non_policy_failure = deepcopy(payload)
    non_policy_failure["failures"][0]["stage"] = "fetch"
    assert list(result_validator.iter_errors(non_policy_failure))

    zero_revision = deepcopy(payload)
    zero_revision["policy_revision"] = 0
    assert list(result_validator.iter_errors(zero_revision))
