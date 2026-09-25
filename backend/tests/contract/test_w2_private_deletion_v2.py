from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from app.runtime.w2_private_deletion_v2 import (
    W2PrivateDeletionV2Error,
    parse_w2_private_deletion_ack_v2,
    require_w2_ack_completion_v2,
    serialize_w2_private_deletion_command_v2,
)

OWNER = UUID("22222222-2222-4222-8222-222222222222")
DELETION = UUID("11111111-1111-4111-8111-111111111111")
PROJECT = UUID("33333333-3333-4333-8333-333333333333")
CONTRACTS = Path(__file__).parents[2] / "contracts" / "w2" / "v2"


def test_w2_v2_manifest_pins_source_and_migration() -> None:
    manifest = json.loads((CONTRACTS / "private-deletion-manifest.json").read_text())
    assert manifest["w2_source_sha"] == "e2491a4084ed50090d5135ba177a3772e9a44f5a"
    assert manifest["w2_required_migration_head"] == "0010_private_deletion_scope_v2"
    for prefix in ("command", "ack"):
        path = CONTRACTS / manifest[f"{prefix}_schema_path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest[f"{prefix}_schema_sha256"]


@pytest.mark.parametrize(
    ("name", "digest"),
    [
        (
            "private-deletion-command-v2.schema.json",
            "73eb3a51d15923969d483b13fdbf498e0a6cd8cfa18c019a66f5f532cfd64067",
        ),
        (
            "private-deletion-ack-v2.schema.json",
            "22cd9cd44061b5c14c9852634417482993a8ffb8f69dc8e98e3b6cd71b891bc9",
        ),
    ],
)
def test_w2_v2_schema_raw_bytes_match_owner_pin(name: str, digest: str) -> None:
    assert hashlib.sha256((CONTRACTS / name).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("project_id", [None, PROJECT])
def test_v2_command_has_exact_scope_and_stable_retry_body(project_id: UUID | None) -> None:
    body = serialize_w2_private_deletion_command_v2(
        deletion_id=DELETION,
        owner_user_id=OWNER,
        deletion_epoch=7,
        project_id=project_id,
    )
    payload = json.loads(body)
    schema = json.loads((CONTRACTS / "private-deletion-command-v2.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    assert list(validator.iter_errors(payload)) == []
    assert payload["scope"] == (
        {"type": "ACCOUNT"}
        if project_id is None
        else {"type": "PROJECT", "project_id": str(PROJECT)}
    )
    assert body == serialize_w2_private_deletion_command_v2(
        deletion_id=DELETION,
        owner_user_id=OWNER,
        deletion_epoch=7,
        project_id=project_id,
    )
    assert set(payload) == {
        "schema_version",
        "deletion_id",
        "owner_user_id",
        "deletion_epoch",
        "scope",
    }


def test_v2_ack_requires_current_exact_account_or_project_binding() -> None:
    body = json.dumps(
        {
            "schema_version": "w2.private-deletion-ack.v2",
            "deletion_id": str(DELETION),
            "owner_user_id": str(OWNER),
            "deletion_epoch": 7,
            "scope": {"type": "PROJECT", "project_id": str(PROJECT)},
            "outcome": "DUPLICATE",
        }
    )
    ack = parse_w2_private_deletion_ack_v2(body)
    schema = json.loads((CONTRACTS / "private-deletion-ack-v2.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    assert list(validator.iter_errors(json.loads(body))) == []
    assert (
        require_w2_ack_completion_v2(
            ack,
            deletion_id=DELETION,
            owner_user_id=OWNER,
            deletion_epoch=7,
            project_id=PROJECT,
        )
        == DELETION
    )
    with pytest.raises(W2PrivateDeletionV2Error):
        require_w2_ack_completion_v2(
            ack,
            deletion_id=DELETION,
            owner_user_id=OWNER,
            deletion_epoch=7,
            project_id=None,
        )


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "w2.private-deletion-ack.v1"},
        {"outcome": "STALE"},
        {"deletion_epoch": True},
        {"deletion_epoch": 0},
        {"deletion_epoch": 2**63},
        {"scope": {"type": "PROJECT"}},
        {"scope": {"type": "ACCOUNT", "project_id": str(PROJECT)}},
        {"scope": {"type": "UNKNOWN"}},
        {"owner_user_id": "NOT-A-UUID"},
        {"extra": "unsafe"},
    ],
)
def test_v2_ack_rejects_invalid_or_legacy_body(change: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "schema_version": "w2.private-deletion-ack.v2",
        "deletion_id": str(DELETION),
        "owner_user_id": str(OWNER),
        "deletion_epoch": 7,
        "scope": {"type": "ACCOUNT"},
        "outcome": "APPLIED",
    }
    payload.update(change)
    with pytest.raises(W2PrivateDeletionV2Error):
        parse_w2_private_deletion_ack_v2(json.dumps(payload))
