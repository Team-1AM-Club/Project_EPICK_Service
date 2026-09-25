from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from app.runtime.w2_private_write_authority import (
    TerminalCleanupAuthorityRequest,
    TerminalCleanupAuthorityResponse,
)

CONTRACTS = Path(__file__).parents[2] / "contracts" / "w1" / "v1"
COMMAND_ID = "11111111-1111-4111-8111-111111111111"


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def test_terminal_cleanup_wire_contract_matches_w1_models() -> None:
    request = {
        "schema_version": "w1.private.w2-terminal-cleanup.v1",
        "owner_user_id": "22222222-2222-4222-8222-222222222222",
        "owner_deletion_epoch": 0,
        "command_id": COMMAND_ID,
        "job_id": "33333333-3333-4333-8333-333333333333",
        "execution_fence": 1,
        "scope": {"type": "ACCOUNT"},
        "cleanup_kind": "STAGED_OUTBOX",
    }
    response = {
        **request,
        "authority_ref": f"w1:terminal-cleanup:{COMMAND_ID}:fence:1:epoch:0:kind:STAGED_OUTBOX",
        "allowed_effect": "OWNER_LOCKED_PRIVATE_CLEANUP_ONLY",
    }
    request_validator = _validator("w2-terminal-cleanup-authority.request.schema.json")
    response_validator = _validator("w2-terminal-cleanup-authority.response.schema.json")
    assert request_validator.is_valid(request)
    assert response_validator.is_valid(response)
    assert (
        TerminalCleanupAuthorityRequest.model_validate(request).model_dump(mode="json") == request
    )
    assert TerminalCleanupAuthorityResponse.model_validate(response).model_dump(mode="json") == (
        response
    )
    assert not request_validator.is_valid({**request, "authority_ref": response["authority_ref"]})
    assert not request_validator.is_valid({**request, "cleanup_kind": "SQS_SEND"})
    assert not response_validator.is_valid({**response, "allowed_effect": "SQS_SEND"})
