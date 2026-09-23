from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from app.runtime.w2_private_deletion_command import (
    W2PrivateDeletionCommandError,
    serialize_w2_private_deletion_command,
)

_OWNER = UUID("22222222-2222-4222-8222-222222222222")
_DELETION = UUID("11111111-1111-4111-8111-111111111111")
_ATTEMPT = UUID("33333333-3333-4333-8333-333333333333")
_DEDUP = UUID("44444444-4444-4444-8444-444444444444")


def _command(**overrides: object) -> str:
    arguments: dict[str, object] = {
        "deletion_id": _DELETION,
        "owner_user_id": _OWNER,
        "deletion_epoch": 7,
        "attempt_ids": (_ATTEMPT,),
        "request_deduplication_ids": (_DEDUP,),
        "private_reference_keys": ("owner/2222/private/1",),
    }
    arguments.update(overrides)
    return serialize_w2_private_deletion_command(**arguments)


def test_command_matches_pinned_w2_schema_and_retries_exact_same_payload() -> None:
    first = _command()
    schema_path = (
        Path(__file__).parents[2]
        / "contracts"
        / "w2"
        / "v1"
        / "private-deletion.command.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    assert list(validator.iter_errors(json.loads(first))) == []
    assert json.loads(first) == {
        "schema_version": "w2.private-deletion.v1",
        "deletion_id": str(_DELETION),
        "owner_user_id": str(_OWNER),
        "deletion_epoch": 7,
        "attempt_ids": [str(_ATTEMPT)],
        "request_deduplication_ids": [str(_DEDUP)],
        "private_reference_keys": ["owner/2222/private/1"],
    }
    assert _command() == first


@pytest.mark.parametrize(
    "overrides",
    [
        {"deletion_epoch": 0},
        {"deletion_epoch": True},
        {"deletion_epoch": 2**63},
        {"attempt_ids": (_ATTEMPT, _ATTEMPT)},
        {"private_reference_keys": ("",)},
        {
            "attempt_ids": (),
            "request_deduplication_ids": (),
            "private_reference_keys": (),
        },
    ],
)
def test_command_rejects_incomplete_or_invalid_scope(overrides: dict[str, object]) -> None:
    with pytest.raises(W2PrivateDeletionCommandError):
        _command(**overrides)
