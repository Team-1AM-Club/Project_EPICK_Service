import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from w3_knowledge.core_decision import DecisionContext
from w3_knowledge.core_runtime import AnalysisPlan, Authorization, CoreRuntime
from w3_knowledge.lifecycle import (
    LifecycleReceipt,
    OwnerDeletionCommand,
    SourceRetirementCommand,
)
from w3_knowledge.lifecycle_worker import LifecycleSqsWorker
from w3_knowledge.retention import RetentionPolicy


POLICY = RetentionPolicy(
    handoff_body_seconds=14,
    private_body_max_seconds=30,
    terminal_metadata_seconds=90,
    owner_tombstone_seconds=365,
    retired_counter_seconds=90,
    backup_seconds=30,
)


class Authority:
    def __init__(self, *, active=True):
        self.value = Authorization(
            context=DecisionContext(
                job_id=UUID(int=1),
                company_id=UUID(int=2),
                source_id=UUID(int=3),
                analysis_input_version="analysis-1",
            ),
            owner_id=UUID(int=4),
            owner_epoch=1,
            active=active,
        )

    def current(self, job_id, source_id):
        return self.value


def plan(*, request_id=10, issued_at=100):
    context = Authority().value.context
    return AnalysisPlan(
        context=context,
        analysis_request_id=UUID(int=request_id),
        analysis_request_issued_at=issued_at,
        required_sources=[context.source_id],
        optional_sources=[],
    )


def runtime(tmp_path):
    return CoreRuntime(tmp_path / "runtime.db", policy=POLICY, max_attempts=3)


def owner_command(*, command_id=20, epoch=2):
    return OwnerDeletionCommand(
        schema_version="1.0",
        command_id=UUID(int=command_id),
        owner_id=UUID(int=4),
        owner_deletion_epoch=epoch,
        target_type="W3_CORE_RUNTIME",
        target_ref=UUID(int=30),
    )


def source_command(*, command_id=40, retired_at=200):
    return SourceRetirementCommand(
        schema_version="w1.private.w3-source-retirement/1.0",
        command_id=UUID(int=command_id),
        operation="RETIRE_SOURCE",
        company_id=UUID(int=2),
        source_id=UUID(int=3),
        retired_at=datetime.fromtimestamp(retired_at, UTC),
        target_type="W3_CORE_RUNTIME",
        target_ref=UUID(int=50),
    )


def test_owner_command_maps_applied_duplicate_and_stale_without_raw_command_storage(tmp_path):
    r = runtime(tmp_path)
    r.supply(plan(), Authority(), "first", now=100)

    applied = r.apply_lifecycle_command(owner_command(), now=110)
    exact_redelivery = r.apply_lifecycle_command(owner_command(), now=111)
    semantic_duplicate = r.apply_lifecycle_command(owner_command(command_id=21, epoch=2), now=112)
    stale = r.apply_lifecycle_command(owner_command(command_id=22, epoch=1), now=113)

    assert applied.receipt.outcome == "APPLIED"
    assert applied.receipt.affected_count == 1
    assert exact_redelivery.duplicate_delivery is True
    assert exact_redelivery.receipt.model_dump_json() == applied.receipt.model_dump_json()
    assert semantic_duplicate.receipt.outcome == "DUPLICATE"
    assert stale.receipt.outcome == "STALE"
    assert stale.receipt.applied_epoch == 2
    assert r.inspect()[0]["state"] == "DELETED"

    with r.producer._connect() as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(core_lifecycle_commands)")}
        assert "body" not in columns
        assert db.execute("SELECT COUNT(*) FROM core_lifecycle_commands").fetchone()[0] == 3


def test_same_command_id_with_changed_payload_is_terminal_conflict(tmp_path):
    r = runtime(tmp_path)
    r.apply_lifecycle_command(owner_command(), now=100)
    with pytest.raises(ValueError, match="LIFECYCLE_COMMAND_ID_CONFLICT"):
        r.apply_lifecycle_command(owner_command(epoch=3), now=101)


def test_source_retirement_uses_authoritative_first_time_and_blocks_supply(tmp_path):
    r = runtime(tmp_path)
    r.supply(plan(), Authority(), "first", now=100)

    applied = r.retire_source(source_command(), now=210)
    duplicate = r.apply_lifecycle_command(source_command(command_id=41), now=220)
    stale = r.apply_lifecycle_command(source_command(command_id=42, retired_at=201), now=230)

    assert applied.receipt.outcome == "APPLIED"
    assert applied.receipt.effective_at == datetime.fromtimestamp(200, UTC)
    assert duplicate.receipt.outcome == "DUPLICATE"
    assert stale.receipt.outcome == "STALE"
    assert stale.receipt.effective_at == datetime.fromtimestamp(200, UTC)
    with pytest.raises(ValueError, match="SOURCE_RETIRED"):
        r.supply(plan(request_id=11, issued_at=230), Authority(), "new", now=230)
    assert r.expire(now=289.999)["counters"] == 0
    assert r.expire(now=290)["counters"] == 1


def test_source_retirement_and_receipt_outbox_roll_back_together(tmp_path):
    r = runtime(tmp_path)
    with r.producer._connect() as db:
        db.execute(
            """CREATE TRIGGER fail_lifecycle_receipt BEFORE INSERT ON core_lifecycle_receipts
            BEGIN SELECT RAISE(ABORT, 'synthetic outbox failure'); END"""
        )

    with pytest.raises(sqlite3.IntegrityError, match="synthetic outbox failure"):
        r.retire_source(source_command(), now=210)

    with r.producer._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM core_retired_sources").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM core_lifecycle_commands").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM core_lifecycle_receipts").fetchone()[0] == 0


class ReceiptTransport:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.bodies = []

    def send(self, body):
        self.bodies.append(body)
        if self.fail:
            raise TimeoutError("synthetic receipt timeout")
        return "receipt-transport-id"


def test_receipt_outbox_retries_same_bytes_across_restart(tmp_path):
    r = runtime(tmp_path)
    command = owner_command()
    applied = r.apply_lifecycle_command(command, now=100)
    failing = ReceiptTransport(fail=True)

    assert r.relay_lifecycle_receipt(command.command_id, failing, now=100) == "RETRY"
    successful = ReceiptTransport()
    assert (
        runtime(tmp_path).relay_lifecycle_receipt(command.command_id, successful, now=200)
        == "TRANSPORT_HANDOFF"
    )
    assert failing.bodies == successful.bodies == [applied.receipt.model_dump_json()]
    assert r.relay_lifecycle_receipt(command.command_id, successful, now=201) == "ALREADY_HANDOFF"


def test_handed_off_lifecycle_metadata_expires_from_both_tables(tmp_path):
    r = runtime(tmp_path)
    command = owner_command()
    r.apply_lifecycle_command(command, now=100)
    assert r.relay_lifecycle_receipt(command.command_id, ReceiptTransport(), now=100) == (
        "TRANSPORT_HANDOFF"
    )

    assert r.expire(now=189.999)["lifecycle_metadata"] == 0
    assert r.expire(now=190)["lifecycle_metadata"] == 1
    with r.producer._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM core_lifecycle_commands").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM core_lifecycle_receipts").fetchone()[0] == 0


class FakeSqsClient:
    def __init__(self, body, *, sender="AROAW1ROLE:session", fail_send=False):
        self.body = body
        self.sender = sender
        self.fail_send = fail_send
        self.calls = []

    def receive_message(self, **kwargs):
        self.calls.append(("receive", kwargs))
        return {
            "Messages": [
                {
                    "MessageId": "broker-command-id",
                    "ReceiptHandle": "receipt-handle",
                    "Body": self.body,
                    "Attributes": {"SenderId": self.sender, "ApproximateReceiveCount": "1"},
                }
            ]
        }

    def send_message(self, **kwargs):
        self.calls.append(("send", kwargs))
        if self.fail_send:
            raise TimeoutError("synthetic send failure")
        return {"MessageId": "broker-receipt-id", "ResponseMetadata": {"HTTPStatusCode": 200}}

    def delete_message(self, **kwargs):
        self.calls.append(("delete", kwargs))


def test_sqs_worker_sends_durable_receipt_before_acknowledging_command(tmp_path):
    command = owner_command()
    client = FakeSqsClient(command.model_dump_json())
    result = LifecycleSqsWorker(
        runtime=runtime(tmp_path),
        client=client,
        command_queue_url="https://sqs.ap-northeast-2.amazonaws.com/1/w3-lifecycle-command",
        receipt_queue_url="https://sqs.ap-northeast-2.amazonaws.com/1/w3-lifecycle-receipt",
        expected_sender_id="AROAW1ROLE",
    ).drain_once(now=100)

    assert result.applied == 1
    assert result.acknowledged == 1
    assert [call[0] for call in client.calls] == ["receive", "send", "delete"]
    receipt = json.loads(client.calls[1][1]["MessageBody"])
    assert receipt["command_id"] == str(command.command_id)
    assert "owner_id" not in receipt


def test_sqs_worker_does_not_ack_when_receipt_handoff_is_uncertain(tmp_path):
    client = FakeSqsClient(owner_command().model_dump_json(), fail_send=True)
    result = LifecycleSqsWorker(
        runtime=runtime(tmp_path),
        client=client,
        command_queue_url="https://sqs.ap-northeast-2.amazonaws.com/1/w3-lifecycle-command",
        receipt_queue_url="https://sqs.ap-northeast-2.amazonaws.com/1/w3-lifecycle-receipt",
        expected_sender_id="AROAW1ROLE",
    ).drain_once(now=100)

    assert result.retry_scheduled == 1
    assert [call[0] for call in client.calls] == ["receive", "send"]


def test_sqs_worker_does_not_ack_when_receipt_database_step_fails(tmp_path, monkeypatch):
    r = runtime(tmp_path)
    client = FakeSqsClient(owner_command().model_dump_json())

    def fail_relay(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic database failure")

    monkeypatch.setattr(r, "relay_lifecycle_receipt", fail_relay)
    result = LifecycleSqsWorker(
        runtime=r,
        client=client,
        command_queue_url="https://sqs.ap-northeast-2.amazonaws.com/1/w3-lifecycle-command",
        receipt_queue_url="https://sqs.ap-northeast-2.amazonaws.com/1/w3-lifecycle-receipt",
        expected_sender_id="AROAW1ROLE",
    ).drain_once(now=100)

    assert result.retry_scheduled == 1
    assert [call[0] for call in client.calls] == ["receive"]


def test_sqs_worker_terminally_rejects_untrusted_sender_without_receipt(tmp_path):
    client = FakeSqsClient(owner_command().model_dump_json(), sender="AROAOTHER:session")
    result = LifecycleSqsWorker(
        runtime=runtime(tmp_path),
        client=client,
        command_queue_url="https://sqs.ap-northeast-2.amazonaws.com/1/w3-lifecycle-command",
        receipt_queue_url="https://sqs.ap-northeast-2.amazonaws.com/1/w3-lifecycle-receipt",
        expected_sender_id="AROAW1ROLE",
    ).drain_once(now=100)

    assert result.terminal_rejected == 1
    assert result.acknowledged == 1
    assert [call[0] for call in client.calls] == ["receive", "delete"]


def test_lifecycle_cli_requires_explicit_consume_flag(tmp_path):
    import subprocess
    import sys

    database = tmp_path / "runtime.db"
    CoreRuntime(database, policy=POLICY)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "w3_knowledge.core_runtime_cli",
            "lifecycle-once",
            "--db",
            str(database),
            "--retention-seconds",
            str(POLICY.handoff_body_seconds),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert json.loads(result.stdout) == {
        "status": "FAILED",
        "code": "CORE_RUNTIME_COMMAND_FAILED",
    }


def test_committed_lifecycle_contract_examples_validate_and_parse():
    root = Path(__file__).parents[2] / "contracts" / "core-runtime"
    cases = [
        (
            "owner-deletion-command.schema.json",
            "examples/owner-deletion-command.json",
            OwnerDeletionCommand,
        ),
        (
            "source-retirement-command.schema.json",
            "examples/source-retirement-command.json",
            SourceRetirementCommand,
        ),
        (
            "lifecycle-receipt.schema.json",
            "examples/lifecycle-receipt.json",
            LifecycleReceipt,
        ),
    ]
    for schema_name, example_name, model in cases:
        schema = json.loads((root / schema_name).read_text(encoding="utf-8"))
        example = json.loads((root / example_name).read_text(encoding="utf-8"))
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(example)
        model.model_validate(example)
