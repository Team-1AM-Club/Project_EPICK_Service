from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from app.runtime.w2_commit_gate_worker import W2CommitGateAck

BACKEND_ROOT = Path(__file__).parents[2]
CONTRACT_ROOT = BACKEND_ROOT / "contracts"
SCHEMA_PATH = CONTRACT_ROOT / "w1/v1/private-w2-commit-gate.schema.json"
FIXTURE_ROOT = CONTRACT_ROOT / "fixtures/v1/w1"
MANIFEST_PATH = CONTRACT_ROOT / "w1/v1/commit-gate-manifest.json"

pytestmark = pytest.mark.w1_isolated_commit_gate


def _load(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as stream:
        value: dict[str, object] = json.load(stream)
    return value


def _canonical_contract_sha256(path: Path) -> str:
    """Hash LF-normalized bytes so Windows worktrees retain the pinned value."""

    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _validator() -> Draft202012Validator:
    schema = _load(SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.mark.parametrize(
    ("fixture_name", "action", "revision"),
    [
        ("private-w2-commit-gate-prepare.json", "PREPARE", 1),
        ("private-w2-commit-gate-finalize.json", "FINALIZE", 4),
        ("private-w2-commit-gate-abort.json", "ABORT", 2),
        ("private-w2-commit-gate-purge.json", "PURGE", 5),
    ],
)
def test_w1_commit_gate_fixtures_match_the_action_contract(
    fixture_name: str, action: str, revision: int
) -> None:
    message = _load(FIXTURE_ROOT / fixture_name)

    assert list(_validator().iter_errors(message)) == []
    assert message["schema_version"] == "w1.private.w2.commit-gate.v1"
    assert message["message_type"] == "w1.private.w2.commit-gate.v1"
    assert message["producer"] == "w1"
    assert message["visibility_scope"] == "PRIVATE"
    assert message["action"] == action
    assert message["operation_revision"] == revision


def test_w1_commit_gate_actions_keep_the_operation_binding_immutable() -> None:
    fixtures = {
        action: _load(FIXTURE_ROOT / f"private-w2-commit-gate-{action.lower()}.json")
        for action in ("prepare", "finalize", "abort", "purge")
    }
    prepare = fixtures["prepare"]

    for action, message in fixtures.items():
        assert message["operation_id"] == prepare["operation_id"], action
        assert message["command_id"] == prepare["command_id"], action
        assert message["job_id"] == prepare["job_id"], action
        assert message["authenticated_owner_ref"] == prepare["authenticated_owner_ref"], action
        assert message["execution_fence"] == prepare["execution_fence"], action
        assert message["owner_deletion_epoch"] == prepare["owner_deletion_epoch"], action
        assert message["result_digest"] == prepare["result_digest"], action

    assert fixtures["prepare"]["operation_revision"] < fixtures["finalize"]["operation_revision"]
    assert fixtures["prepare"]["operation_revision"] < fixtures["abort"]["operation_revision"]
    assert fixtures["finalize"]["operation_revision"] < fixtures["purge"]["operation_revision"]
    assert "purge_owner_deletion_epoch" not in prepare
    assert fixtures["purge"]["purge_owner_deletion_epoch"] > prepare["owner_deletion_epoch"]
    assert len({message["message_id"] for message in fixtures.values()}) == len(fixtures)


def test_w1_commit_gate_manifest_pins_only_w1_owned_artifacts() -> None:
    manifest = _load(MANIFEST_PATH)

    assert manifest["contract_status"] == "W1_STATIC_COMMIT_GATE_CONTRACT_PINNED"
    assert manifest["artifact_authority"] == "W1"
    assert manifest["schema_version"] == "w1.private.w2.commit-gate.v1"
    assert manifest["artifacts"] == {
        "private-w2-commit-gate.schema.json": _canonical_contract_sha256(SCHEMA_PATH)
    }

    expected_fixtures = {
        "private-w2-commit-gate-prepare.json",
        "private-w2-commit-gate-finalize.json",
        "private-w2-commit-gate-abort.json",
        "private-w2-commit-gate-purge.json",
        "invalid-private-w2-commit-gate-owner.json",
        "invalid-private-w2-commit-gate-command.json",
        "invalid-private-w2-commit-gate-fence.json",
        "invalid-private-w2-commit-gate-epoch.json",
        "invalid-private-w2-commit-gate-digest.json",
        "invalid-private-w2-commit-gate-revision.json",
    }
    assert set(manifest["fixtures"]) == expected_fixtures
    for fixture_name, expected_sha256 in manifest["fixtures"].items():
        assert _canonical_contract_sha256(FIXTURE_ROOT / str(fixture_name)) == expected_sha256

    pending_w2_ack = manifest["pending_w2_canonical_ack"]
    assert pending_w2_ack["status"] == "W2_CANONICAL_ACK_NOT_YET_SUPPLIED"
    assert pending_w2_ack["artifact_authority"] == "W2"


@pytest.mark.parametrize(
    "fixture_name",
    ["private-w2-commit-gate-prepare.json", "private-w2-commit-gate-finalize.json"],
)
def test_w1_normalized_ack_binding_matches_the_outbound_commit_gate_command(
    fixture_name: str,
) -> None:
    """Check the W1 adapter boundary without pretending to validate W2 wire JSON.

    W2 owns the canonical ACK schema.  This static check instead fixes the
    exact fields a future W2 parser must hand to the W1 ACK worker, while the
    integration suite verifies that mismatches are rejected before state moves.
    """

    command = _load(FIXTURE_ROOT / fixture_name)
    ack = W2CommitGateAck(
        message_id=uuid4(),
        operation_id=UUID(str(command["operation_id"])),
        operation_revision=int(command["operation_revision"]),
        action=str(command["action"]),
        command_id=UUID(str(command["command_id"])),
        job_id=UUID(str(command["job_id"])),
        execution_fence=int(command["execution_fence"]),
        owner_deletion_epoch=int(command["owner_deletion_epoch"]),
        result_digest=str(command["result_digest"]),
    )

    assert ack.operation_id == UUID(str(command["operation_id"]))
    assert ack.operation_revision == command["operation_revision"]
    assert ack.action == command["action"]
    assert ack.command_id == UUID(str(command["command_id"]))
    assert ack.job_id == UUID(str(command["job_id"]))
    assert ack.execution_fence == command["execution_fence"]
    assert ack.owner_deletion_epoch == command["owner_deletion_epoch"]
    assert ack.result_digest == command["result_digest"]


@pytest.mark.parametrize(
    "fixture_name",
    [
        "invalid-private-w2-commit-gate-owner.json",
        "invalid-private-w2-commit-gate-command.json",
        "invalid-private-w2-commit-gate-fence.json",
        "invalid-private-w2-commit-gate-epoch.json",
        "invalid-private-w2-commit-gate-digest.json",
        "invalid-private-w2-commit-gate-revision.json",
    ],
)
def test_w1_commit_gate_rejects_invalid_identity_or_currentness_fields(fixture_name: str) -> None:
    assert list(_validator().iter_errors(_load(FIXTURE_ROOT / fixture_name)))
