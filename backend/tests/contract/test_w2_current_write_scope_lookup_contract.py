from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from app.runtime.w2_private_write_authority import (
    CurrentWriteScopeLookupRequest,
    CurrentWriteScopeLookupResponse,
)

CONTRACTS = Path(__file__).parents[2] / "contracts"


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((CONTRACTS / "w1" / "v1" / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.mark.parametrize("scope_kind", ["account", "project"])
def test_scope_lookup_fixtures_are_binding_only(scope_kind: str) -> None:
    fixture_root = CONTRACTS / "fixtures" / "v1" / "w1"
    request = json.loads(
        (fixture_root / f"w2-current-write-scope-{scope_kind}.request.json").read_text(
            encoding="utf-8"
        )
    )
    response = json.loads(
        (fixture_root / f"w2-current-write-scope-{scope_kind}.response.json").read_text(
            encoding="utf-8"
        )
    )
    request_validator = _validator("w2-current-write-scope-lookup.request.schema.json")
    response_validator = _validator("w2-current-write-scope-lookup.response.schema.json")
    assert request_validator.is_valid(request)
    assert response_validator.is_valid(response)
    assert CurrentWriteScopeLookupRequest.model_validate(request).model_dump(mode="json") == request
    assert (
        CurrentWriteScopeLookupResponse.model_validate(response).model_dump(mode="json") == response
    )
    assert not request_validator.is_valid({**request, "scope": {"type": "ACCOUNT"}})
    assert not request_validator.is_valid({**request, "project_ref": None})
    assert not response_validator.is_valid({**response, "authority_ref": "not-a-grant"})
    assert not response_validator.is_valid({**response, "scope": {"type": "PROJECT"}})
    with pytest.raises(ValidationError):
        CurrentWriteScopeLookupResponse.model_validate(
            {**response, "scope": {"type": "PROJECT", "project_id": "not-a-uuid"}}
        )
