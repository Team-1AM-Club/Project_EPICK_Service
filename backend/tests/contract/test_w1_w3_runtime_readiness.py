from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.runtime.w1_w3_runtime_readiness import (
    W1W3ReadinessError,
    assert_gates_verified,
    load_runtime_readiness,
    validate_runtime_readiness,
    validate_runtime_readiness_transition,
)

REPO_ROOT = Path(__file__).parents[3]
FEATURE_ROOT = REPO_ROOT / "specs" / "007-w1-w3-runtime-integration"
READINESS_PATH = FEATURE_ROOT / "evidence" / "runtime-readiness.json"
SCHEMA_PATH = FEATURE_ROOT / "contracts" / "runtime-readiness.schema.json"


def _readiness() -> dict[str, object]:
    return json.loads(READINESS_PATH.read_text(encoding="utf-8"))


def _gate(data: dict[str, object], gate_id: str) -> dict[str, object]:
    gates = data["gates"]
    assert isinstance(gates, list)
    return next(gate for gate in gates if gate["id"] == gate_id)


def test_initial_readiness_is_schema_valid_and_only_m1_is_in_progress() -> None:
    loaded = load_runtime_readiness(READINESS_PATH, schema_path=SCHEMA_PATH)

    assert loaded["status"] == "M1_IN_PROGRESS"
    assert loaded["milestones"]["M1"]["status"] == "IN_PROGRESS"
    assert {loaded["milestones"][name]["status"] for name in ("M2", "M3", "M4", "M5")} == {
        "BLOCKED"
    }


def test_false_ready_is_rejected_until_required_gates_are_verified() -> None:
    data = _readiness()
    data["status"] = "M1_M4_READY_FOR_JOINT_CT12"
    milestones = data["milestones"]
    assert isinstance(milestones, dict)
    for name in ("M1", "M2", "M3", "M4"):
        milestones[name]["status"] = "COMPLETE"

    with pytest.raises(W1W3ReadinessError, match="W3-A"):
        validate_runtime_readiness(data, schema_path=SCHEMA_PATH)


def test_joint_complete_requires_every_milestone_and_gate() -> None:
    data = _readiness()
    data["status"] = "JOINT_CT12_COMPLETE"
    milestones = data["milestones"]
    assert isinstance(milestones, dict)
    for milestone in milestones.values():
        milestone["status"] = "COMPLETE"
    for gate_id in ("W3-A", "W3-B", "W3-C", "W3-D", "W3-E"):
        _gate(data, gate_id)["status"] = "VERIFIED"

    with pytest.raises(W1W3ReadinessError, match="W3-F"):
        validate_runtime_readiness(data, schema_path=SCHEMA_PATH)


def test_gate_assertion_lists_missing_gates_without_values() -> None:
    with pytest.raises(W1W3ReadinessError, match="W3-A, W3-B"):
        assert_gates_verified(_readiness(), "W3-A", "W3-B")


def test_completed_milestone_cannot_move_backwards() -> None:
    before = _readiness()
    before["status"] = "BLOCKED_ON_W3_INPUTS"
    before["milestones"]["M1"]["status"] = "COMPLETE"
    _gate(before, "W3-D")["status"] = "VERIFIED"
    after = deepcopy(before)
    after["milestones"]["M1"]["status"] = "IN_PROGRESS"

    with pytest.raises(W1W3ReadinessError, match="M1.*backwards"):
        validate_runtime_readiness_transition(before, after, schema_path=SCHEMA_PATH)
