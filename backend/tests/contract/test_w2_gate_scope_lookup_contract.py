from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from app.runtime.w2_private_write_authority import (
    GateScopeLookupRequest,
    GateScopeLookupResponse,
)

CONTRACTS = Path(__file__).parents[2] / "contracts" / "w1" / "v1"


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def test_gate_scope_lookup_is_binding_only_and_not_an_authority_token() -> None:
    request = {
        "schema_version": "w1.private.w2-gate-scope-lookup.v1",
        "owner_user_id": "22222222-2222-4222-8222-222222222222",
        "owner_deletion_epoch": 0,
        "command_id": "11111111-1111-4111-8111-111111111111",
        "job_id": "33333333-3333-4333-8333-333333333333",
        "execution_fence": 1,
        "operation_id": "44444444-4444-4444-8444-444444444444",
        "operation_revision": 2,
        "action": "ABORT",
        "phase": "APPLY",
        "result_digest": "sha256:" + "a" * 64,
    }
    response = {**request, "scope": {"type": "ACCOUNT"}}
    request_validator = _validator("w2-gate-scope-lookup.request.schema.json")
    response_validator = _validator("w2-gate-scope-lookup.response.schema.json")
    assert request_validator.is_valid(request)
    assert response_validator.is_valid(response)
    assert (
        GateScopeLookupRequest.model_validate(request).model_dump(mode="json", exclude_none=True)
        == request
    )
    assert (
        GateScopeLookupResponse.model_validate(response).model_dump(mode="json", exclude_none=True)
        == response
    )
    assert not request_validator.is_valid({**request, "scope": {"type": "ACCOUNT"}})
    assert not response_validator.is_valid({**response, "authority_ref": "forbidden"})
    assert not response_validator.is_valid({**response, "scope": {"type": "PROJECT"}})
    with pytest.raises(ValidationError):
        GateScopeLookupResponse.model_validate({**response, "scope": {"type": "PROJECT"}})


def test_gate_scope_lookup_purge_requires_the_original_and_new_epochs() -> None:
    request = {
        "schema_version": "w1.private.w2-gate-scope-lookup.v1",
        "owner_user_id": "22222222-2222-4222-8222-222222222222",
        "owner_deletion_epoch": 0,
        "command_id": "11111111-1111-4111-8111-111111111111",
        "job_id": "33333333-3333-4333-8333-333333333333",
        "execution_fence": 1,
        "operation_id": "44444444-4444-4444-8444-444444444444",
        "operation_revision": 4,
        "action": "PURGE",
        "phase": "ACK_RELAY",
        "result_digest": "sha256:" + "b" * 64,
        "purge_owner_deletion_epoch": 1,
    }
    validator = _validator("w2-gate-scope-lookup.request.schema.json")
    assert validator.is_valid(request)
    assert GateScopeLookupRequest.model_validate(request).model_dump(mode="json") == request
    assert not validator.is_valid(
        {key: value for key, value in request.items() if key != "purge_owner_deletion_epoch"}
    )
    assert not validator.is_valid({**request, "purge_owner_deletion_epoch": 0})
