from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator, FormatChecker


def _schema() -> dict[str, Any]:
    path = (
        Path(__file__).parents[2]
        / "contracts"
        / "w3"
        / "v1"
        / "retention-receipt.schema.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _receipt(*, outcome: str = "APPLIED") -> dict[str, object]:
    occurred_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    source_id = str(uuid4())
    return {
        "schema_version": "w3.private.w1-lifecycle-receipt/1.0",
        "message_type": "w3.private.w1.lifecycle-receipt",
        "receipt_id": str(uuid4()),
        "occurred_at": occurred_at,
        "visibility_scope": "PRIVATE",
        "producer": "w3",
        "command_id": str(uuid4()),
        "target_ref": source_id,
        "operation": "RETIRE_SOURCE",
        "outcome": outcome,
        "affected_count": 1 if outcome == "APPLIED" else 0,
        "applied_epoch": None,
        "effective_at": occurred_at,
    }


@pytest.mark.parametrize("outcome", ["APPLIED", "DUPLICATE", "STALE"])
def test_source_retirement_receipt_schema_accepts_fixed_terminal_outcomes(outcome: str) -> None:
    validator = Draft202012Validator(_schema(), format_checker=FormatChecker())
    assert list(validator.iter_errors(_receipt(outcome=outcome))) == []


def test_source_retirement_receipt_requires_source_reference() -> None:
    validator = Draft202012Validator(_schema(), format_checker=FormatChecker())
    assert not validator.is_valid(_receipt() | {"target_ref": None})
