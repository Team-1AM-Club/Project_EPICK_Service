"""Fail-closed milestone and external-gate validation for W1/W3 integration."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

_GATE_IDS = {"W3-A", "W3-B", "W3-C", "W3-D", "W3-E", "W3-F"}
_MILESTONE_GATES = {
    "M1": ("W3-D",),
    "M2": ("W3-A", "W3-B"),
    "M3": ("W3-C", "W3-E"),
    "M5": ("W3-F",),
}
_MILESTONE_TRANSITIONS = {
    "NOT_STARTED": {"NOT_STARTED", "IN_PROGRESS", "BLOCKED"},
    "BLOCKED": {"BLOCKED", "IN_PROGRESS"},
    "IN_PROGRESS": {"IN_PROGRESS", "BLOCKED", "COMPLETE"},
    "COMPLETE": {"COMPLETE"},
}
_GATE_TRANSITIONS = {
    "OPEN": {"OPEN", "RECEIVED", "VERIFIED", "REJECTED"},
    "RECEIVED": {"RECEIVED", "VERIFIED", "REJECTED"},
    "VERIFIED": {"VERIFIED"},
    "REJECTED": {"REJECTED", "RECEIVED"},
}
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


class W1W3ReadinessError(ValueError):
    """Safe validation error for integration readiness metadata."""


def load_runtime_readiness(
    path: Path, *, schema_path: Path
) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise W1W3ReadinessError("runtime readiness document is unreadable") from error
    if not isinstance(value, dict):
        raise W1W3ReadinessError("runtime readiness document must be an object")
    return validate_runtime_readiness(value, schema_path=schema_path)


def validate_runtime_readiness(
    value: Mapping[str, Any], *, schema_path: Path
) -> dict[str, Any]:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    except (OSError, json.JSONDecodeError, SchemaError, ValidationError) as error:
        raise W1W3ReadinessError("runtime readiness does not match its schema") from error

    data = dict(value)
    milestones = data["milestones"]
    gate_rows = data["gates"]
    gate_by_id = {row["id"]: row for row in gate_rows}
    if set(gate_by_id) != _GATE_IDS or len(gate_rows) != len(_GATE_IDS):
        raise W1W3ReadinessError("runtime readiness must contain each W3-A through W3-F gate once")

    status = data["status"]
    if status == "M1_IN_PROGRESS" and milestones["M1"]["status"] != "IN_PROGRESS":
        raise W1W3ReadinessError("M1_IN_PROGRESS requires M1 to be IN_PROGRESS")
    if status == "BLOCKED_ON_W3_INPUTS" and not any(
        row["status"] == "BLOCKED" for row in milestones.values()
    ):
        raise W1W3ReadinessError("BLOCKED_ON_W3_INPUTS requires a blocked milestone")
    if status == "M1_M4_READY_FOR_JOINT_CT12":
        _assert_milestones_complete(milestones, "M1", "M2", "M3", "M4")
        _assert_gate_map_verified(gate_by_id, "W3-A", "W3-B", "W3-C", "W3-D", "W3-E")
    if status == "JOINT_CT12_COMPLETE":
        _assert_milestones_complete(milestones, "M1", "M2", "M3", "M4", "M5")
        _assert_gate_map_verified(gate_by_id, *_GATE_IDS)

    for milestone, required_gates in _MILESTONE_GATES.items():
        if milestones[milestone]["status"] == "COMPLETE":
            _assert_gate_map_verified(gate_by_id, *required_gates)

    if (
        milestones["M3"]["status"] == "COMPLETE"
        and "evidence/m3-deletion-operations.json"
        not in milestones["M3"]["evidence_refs"]
    ):
        raise W1W3ReadinessError(
            "M3 completion requires actual private-queue E2E evidence"
        )

    if milestones["M4"]["status"] == "COMPLETE":
        _assert_milestones_complete(milestones, "M1", "M2")
    if milestones["M5"]["status"] == "COMPLETE":
        _assert_milestones_complete(milestones, "M1", "M2", "M3", "M4")
    return data


def assert_gates_verified(value: Mapping[str, Any], *gate_ids: str) -> None:
    gate_by_id = {row["id"]: row for row in value.get("gates", [])}
    unknown = set(gate_ids) - _GATE_IDS
    if unknown:
        raise W1W3ReadinessError(f"unknown gates: {', '.join(sorted(unknown))}")
    _assert_gate_map_verified(gate_by_id, *gate_ids)


def validate_runtime_source_pins(
    readiness: Mapping[str, Any], baseline: Mapping[str, Any]
) -> None:
    """Bind deployable readiness to the implementation pin, not the receipt commit."""
    implementation_sha = baseline.get("w3_implementation_sha")
    receipt_head_sha = baseline.get("w3_receipt_head_sha")
    if not isinstance(implementation_sha, str) or not _FULL_SHA.fullmatch(
        implementation_sha
    ):
        raise W1W3ReadinessError("W3 implementation SHA is missing or invalid")
    if not isinstance(receipt_head_sha, str) or not _FULL_SHA.fullmatch(receipt_head_sha):
        raise W1W3ReadinessError("W3 receipt HEAD SHA is missing or invalid")
    if readiness.get("w3_source_sha") != implementation_sha:
        raise W1W3ReadinessError("runtime readiness does not match the W3 implementation SHA")
    if baseline.get("w3_runtime_drift_from_implementation_to_receipt") is not False:
        raise W1W3ReadinessError("W3 implementation-to-receipt runtime drift is not cleared")


def validate_runtime_readiness_transition(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    schema_path: Path,
) -> None:
    valid_before = validate_runtime_readiness(before, schema_path=schema_path)
    valid_after = validate_runtime_readiness(after, schema_path=schema_path)
    for name in ("M1", "M2", "M3", "M4", "M5"):
        old = valid_before["milestones"][name]["status"]
        new = valid_after["milestones"][name]["status"]
        if new not in _MILESTONE_TRANSITIONS[old]:
            raise W1W3ReadinessError(f"{name} readiness cannot move backwards from {old} to {new}")
    before_gates = {row["id"]: row["status"] for row in valid_before["gates"]}
    after_gates = {row["id"]: row["status"] for row in valid_after["gates"]}
    for gate_id, old in before_gates.items():
        new = after_gates[gate_id]
        if new not in _GATE_TRANSITIONS[old]:
            raise W1W3ReadinessError(
                f"{gate_id} readiness cannot move backwards from {old} to {new}"
            )


def _assert_gate_map_verified(gate_by_id: Mapping[str, Any], *gate_ids: str) -> None:
    missing = [
        gate_id
        for gate_id in sorted(gate_ids)
        if gate_id not in gate_by_id or gate_by_id[gate_id].get("status") != "VERIFIED"
    ]
    if missing:
        raise W1W3ReadinessError(f"required gates are not verified: {', '.join(missing)}")


def _assert_milestones_complete(milestones: Mapping[str, Any], *names: str) -> None:
    incomplete = [name for name in names if milestones[name]["status"] != "COMPLETE"]
    if incomplete:
        raise W1W3ReadinessError(
            f"required milestones are incomplete: {', '.join(incomplete)}"
        )
