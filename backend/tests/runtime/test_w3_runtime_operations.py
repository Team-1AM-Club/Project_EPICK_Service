from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from uuid import UUID

import pytest

ROOT = Path(__file__).parents[3]
W3_SRC = ROOT / "w3" / "Project_EPICK_Service" / "src"
sys.path.insert(0, str(W3_SRC))

from w3_knowledge.core_decision import DecisionContext  # noqa: E402
from w3_knowledge.core_runtime import (  # noqa: E402
    AnalysisPlan,
    Authorization,
    CoreRuntime,
)
from w3_knowledge.retention import DEFAULT_RETENTION_POLICY  # noqa: E402

from scripts.inspect_w3_core_runtime import summarize_report  # noqa: E402


class Authority:
    def __init__(self) -> None:
        self.value = Authorization(
            context=DecisionContext(
                job_id=UUID(int=1),
                company_id=UUID(int=2),
                source_id=UUID(int=3),
                analysis_input_version="input-v1",
            ),
            owner_id=UUID(int=4),
            owner_epoch=1,
            active=True,
        )

    def current(self, job_id, source_id):
        return self.value


class FailingTransport:
    def send(self, body: str) -> str:
        assert body
        raise TimeoutError("transport detail must not persist")


def _plan(authority: Authority) -> AnalysisPlan:
    return AnalysisPlan(
        context=authority.value.context,
        analysis_request_id=UUID(int=10),
        analysis_request_issued_at=100,
        required_sources=[authority.value.context.source_id],
        optional_sources=[],
    )


def test_approved_policy_deadlines_are_exact_and_immutable() -> None:
    policy = DEFAULT_RETENTION_POLICY
    assert policy.revision == "w3.retention/1.1"
    assert policy.handoff_body_seconds == 14 * 24 * 60 * 60
    assert policy.private_body_max_seconds == 30 * 24 * 60 * 60
    assert policy.terminal_metadata_seconds == 90 * 24 * 60 * 60
    assert policy.retired_counter_seconds == 90 * 24 * 60 * 60
    assert policy.owner_tombstone_seconds == 365 * 24 * 60 * 60
    assert policy.backup_seconds == 30 * 24 * 60 * 60
    assert policy.body_deadline(100, 100 + 29 * 24 * 60 * 60) == (
        100 + 30 * 24 * 60 * 60
    )


def test_held_is_reported_without_automatic_replay_and_expire_redacts_body(tmp_path: Path) -> None:
    authority = Authority()
    runtime = CoreRuntime(tmp_path / "core.db", max_attempts=3)
    runtime.supply(_plan(authority), authority, "payload", now=100)
    transport = FailingTransport()

    assert [runtime.relay_once(authority, transport, now=value) for value in (100, 200, 400)] == [
        "RETRY",
        "RETRY",
        "HELD",
    ]
    assert runtime.relay_once(authority, transport, now=10_000) == "IDLE"
    report = runtime.inspect_report()
    assert report["policy_revision"] == "w3.retention/1.1"
    assert report["migration_blockers"] == {"owner_tombstone_without_deleted_at": 0}
    assert report["deliveries"][0]["state"] == "HELD"
    assert "transport detail" not in repr(report)

    deadline = 100 + DEFAULT_RETENTION_POLICY.private_body_max_seconds
    assert runtime.expire(now=deadline - 0.001)["bodies"] == 0
    assert runtime.expire(now=deadline)["bodies"] == 1


def test_backup_is_redacted_quarantined_and_raw_restore_cannot_serve(tmp_path: Path) -> None:
    authority = Authority()
    runtime = CoreRuntime(tmp_path / "core.db", max_attempts=3)
    runtime.supply(_plan(authority), authority, "private payload", now=100)
    backup = tmp_path / "backup.db"

    metadata = runtime.backup(backup, now=200)

    assert metadata["expires_at"] <= 200 + DEFAULT_RETENTION_POLICY.backup_seconds
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT COUNT(*) FROM w3_core_decision_outbox").fetchone()[0] == 0
        states = {row[0] for row in connection.execute("SELECT state FROM core_delivery")}
    assert states == {"BACKUP_REDACTED"}
    restored = CoreRuntime(backup, max_attempts=3)
    with pytest.raises(ValueError, match="RESTORE_QUARANTINED"):
        restored.supply(_plan(authority), authority, "new payload", now=201)


def test_w3_operations_share_state_and_w1_uses_dedicated_retention_workers() -> None:
    w3_compose = (ROOT / "backend" / "infra" / "w3-runtime.compose.yml").read_text(
        encoding="utf-8"
    )
    for service in (
        "w3-core-relay",
        "w3-core-expire",
        "w3-core-inspect",
        "w3-core-backup",
    ):
        assert f"  {service}:" in w3_compose
    assert w3_compose.count("source: epick-w3-core-runtime-state") == 1
    # W3-C replaced operator CLI mutation with an authenticated command consumer.
    assert "w3-core-delete-owner:" not in w3_compose
    assert "w3-core-retire-source:" not in w3_compose

    w1_compose = (ROOT / "backend" / "infra" / "w1-runtime.compose.yml").read_text(
        encoding="utf-8"
    )
    assert "w1-w3-retention-command-relay:" in w1_compose
    assert '["python", "scripts/run_outbox_relay.py", "--scope", "w3-retention"]' in w1_compose
    assert "w1-w3-retention-receipt-worker:" in w1_compose
    assert 'command: ["python", "scripts/run_w3_retention_receipt_worker.py"]' in w1_compose
    assert "W1_W3_RETENTION_COMMAND_ENV_FILE" in w1_compose
    assert "W1_W3_RETENTION_RECEIPT_ENV_FILE" in w1_compose


def test_held_inspector_alerts_but_contains_no_automatic_replay_path() -> None:
    script = ROOT / "backend" / "scripts" / "inspect_w3_core_runtime.py"
    assert script.exists()
    source = script.read_text(encoding="utf-8")
    assert "held_count" in source
    assert "migration_blockers" in source
    assert ".replay(" not in source
    assert " replay " not in source.lower()

    summary = summarize_report(
        {
            "policy_revision": "w3.retention/1.1",
            "migration_blockers": {"owner_tombstone_without_deleted_at": 0},
            "deliveries": [
                {"event_id": "private-one", "state": "HELD"},
                {"event_id": "private-two", "state": "RETRY"},
            ],
        }
    )
    assert summary == {
        "status": "OPERATOR_ACTION_REQUIRED",
        "policy_revision": "w3.retention/1.1",
        "delivery_state_counts": {"HELD": 1, "RETRY": 1},
        "held_count": 1,
        "migration_blocker_count": 0,
        "automatic_action_prohibited": True,
    }
    assert "private-one" not in repr(summary)
