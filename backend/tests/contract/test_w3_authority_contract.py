from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

REQUEST_SCHEMA = "w1/v1/w3-authority.request.schema.json"
RESPONSE_SCHEMA = "w1/v1/w3-authority.response.schema.json"
W3_ROOT = Path(__file__).parents[3] / "w3" / "Project_EPICK_Service"


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value: dict[str, Any] = json.load(stream)
    Draft202012Validator.check_schema(value)
    return value


def _validator(contract_root: Path, relative_path: str) -> Draft202012Validator:
    return Draft202012Validator(
        _load(contract_root / relative_path),
        format_checker=FormatChecker(),
    )


def _annotated_fields(path: Path, class_name: str) -> set[str]:
    module = ast.parse(path.read_text(encoding="utf-8"))
    model = next(
        node for node in module.body if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return {
        node.target.id
        for node in model.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }


def _valid_request() -> dict[str, object]:
    return {
        "schema_version": "w1.private.w3-authority-request.v1",
        "job_id": "11111111-1111-4111-8111-111111111111",
        "source_id": "22222222-2222-4222-8222-222222222222",
    }


def _valid_response() -> dict[str, object]:
    return {
        "schema_version": "w1.private.w3-authority-response.v1",
        "context": {
            "job_id": "11111111-1111-4111-8111-111111111111",
            "company_id": "33333333-3333-4333-8333-333333333333",
            "source_id": "22222222-2222-4222-8222-222222222222",
            "analysis_input_version": "input-v7",
        },
        "owner_id": "44444444-4444-4444-8444-444444444444",
        "owner_epoch": 3,
        "active": True,
    }


def test_authority_schemas_accept_only_the_adopted_minimal_wire(contract_root: Path) -> None:
    request = _validator(contract_root, REQUEST_SCHEMA)
    response = _validator(contract_root, RESPONSE_SCHEMA)

    assert list(request.iter_errors(_valid_request())) == []
    assert list(response.iter_errors(_valid_response())) == []

    unexpected = _valid_request() | {"owner_id": "44444444-4444-4444-8444-444444444444"}
    assert not request.is_valid(unexpected)


@pytest.mark.parametrize(
    ("mutate", "schema"),
    [
        (lambda value: value.update(job_id="not-a-uuid"), REQUEST_SCHEMA),
        (lambda value: value.pop("source_id"), REQUEST_SCHEMA),
        (lambda value: value.update(owner_epoch=-1), RESPONSE_SCHEMA),
        (lambda value: value.update(active=1), RESPONSE_SCHEMA),
        (
            lambda value: value["context"].update(analysis_input_version="   "),
            RESPONSE_SCHEMA,
        ),
    ],
)
def test_authority_schemas_fail_closed_on_invalid_values(
    contract_root: Path,
    mutate,
    schema: str,
) -> None:
    payload = _valid_request() if schema == REQUEST_SCHEMA else _valid_response()
    mutate(payload)
    assert not _validator(contract_root, schema).is_valid(payload)


@pytest.mark.skipif(
    not (W3_ROOT / ".git").exists(),
    reason="independent W3 Git clone is not available in this CI job",
)
def test_authority_response_fields_match_pinned_w3_models(contract_root: Path) -> None:
    schema = _load(contract_root / RESPONSE_SCHEMA)
    context_schema = schema["properties"]["context"]

    assert set(context_schema["properties"]) == _annotated_fields(
        W3_ROOT / "src/w3_knowledge/core_decision.py", "DecisionContext"
    )
    assert set(schema["properties"]) - {"schema_version"} == _annotated_fields(
        W3_ROOT / "src/w3_knowledge/core_runtime.py", "Authorization"
    )
    assert set(context_schema["required"]) == set(context_schema["properties"])
    assert set(schema["required"]) == set(schema["properties"])
