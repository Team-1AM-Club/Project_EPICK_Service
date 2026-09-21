from __future__ import annotations

from pathlib import Path

RUNBOOK = (
    Path(__file__).parents[3]
    / "md"
    / "deploy"
    / "W1_W3_Runtime_Operations_Runbook_2026-09-20.md"
)


def test_w3_runtime_runbook_pins_policy_slo_and_operational_owners() -> None:
    content = " ".join(RUNBOOK.read_text(encoding="utf-8").split())

    for required in (
        "w3.retention/1.1",
        "five minutes",
        "15 minutes",
        "APPLIED",
        "DUPLICATE",
        "STALE",
        "terminal reject without a receipt",
        "pending receipt-outbox rows before receiving new commands",
        "PERMANENTLY_RETIRED",
        "retired_at + 90 days",
        "W1 operations",
        "W3 runtime owner",
        "Product/privacy approver",
        "REDACTED_QUARANTINED_BACKUP",
        "raw SQLite file restore",
    ):
        assert required in content


def test_w3_runtime_runbook_keeps_actual_private_values_out_of_git() -> None:
    content = RUNBOOK.read_text(encoding="utf-8")

    assert "amazonaws.com/" not in content
    assert "arn:aws:" not in content
    assert "AKIA" not in content
    assert "postgresql+psycopg://" not in content
