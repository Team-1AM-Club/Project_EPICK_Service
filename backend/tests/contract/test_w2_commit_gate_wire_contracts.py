from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.runtime.w2_commit_gate_contracts import (
    W2CommitGateContractError,
    parse_w2_commit_gate_ack,
    parse_w2_commit_gate_proposal,
    parse_w2_staged_result,
)

FIXTURE_ROOT = Path(__file__).parents[2] / "contracts" / "fixtures/v1/w2_commit_gate"


def _load(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def _positive_fixture_names() -> tuple[str, ...]:
    manifest = _load("manifest.json")
    return tuple(
        entry["file"]
        for entry in manifest["files"]
        if entry["expectation"] == "schema-valid/model-valid"
        and entry["file"] != "digest-vector.json"
    )


@pytest.mark.parametrize("fixture_name", _positive_fixture_names())
def test_pinned_positive_wire_fixtures_parse(fixture_name: str) -> None:
    parsed = parse_w2_commit_gate_proposal(_load(fixture_name))

    assert parsed.message_id
    assert parsed.producer == "w2"
    assert parsed.visibility_scope == "PRIVATE"


@pytest.mark.parametrize(
    "fixture_name",
    (
        "invalid-staged-message-id.json",
        "invalid-staged-fence.json",
        "invalid-staged-digest.json",
        "invalid-ack-bool-revision.json",
        "invalid-ack-nonpurge-epoch-null.json",
        "invalid-ack-outcome.json",
        "semantic-invalid-staged-binding.json",
        "semantic-invalid-staged-digest.json",
        "semantic-invalid-ack-purge-epoch.json",
    ),
)
def test_pinned_invalid_wire_fixtures_are_rejected(fixture_name: str) -> None:
    with pytest.raises(W2CommitGateContractError):
        parse_w2_commit_gate_proposal(_load(fixture_name))


def test_wire_models_are_frozen_and_ack_semantics_remain_explicit() -> None:
    staged = parse_w2_staged_result(_load("staged-result.json"))
    ack = parse_w2_commit_gate_ack(_load("ack-purge-applied.json"))

    assert ack.action == "PURGE"
    assert ack.outcome == "APPLIED"
    assert ack.purge_owner_deletion_epoch > ack.owner_deletion_epoch
    with pytest.raises(ValidationError):
        staged.producer = "w1"
