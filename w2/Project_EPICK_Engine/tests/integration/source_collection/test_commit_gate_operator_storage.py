"""Count-only operator inspection over synthetic, owner-isolated private rows."""

import json
from pathlib import Path
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator
from tests.integration.source_collection.test_private_commit_gate import (
    NOW,
    _gate,
    _pair,
    _trusted_scope,
    apply_commit_gate,
    database_engine,
    session_factory,
    stage_private_result,
)

from epick_engine.source_collection.commit_gate_operator import inspect_counts

__all__ = ["database_engine", "session_factory"]
pytestmark = pytest.mark.approved_postgres


def test_inspection_reports_private_visibility_and_purge_without_payloads(session_factory) -> None:
    command, result = _pair()
    operation = uuid4()
    with session_factory.begin() as session:
        stage_private_result(session, command, result, message_id=uuid4(), occurred_at=NOW)
    for action, revision, expected, visible in [
        (None, 0, "STAGED", 0),
        ("PREPARE", 1, "PREPARED", 0),
        ("FINALIZE", 2, "FINALIZED", 1),
        ("PURGE", 3, "PURGED", 0),
    ]:
        with session_factory.begin() as session:
            if action:
                apply_commit_gate(
                    session,
                    _gate(command, result, action, operation_id=operation, revision=revision),
                    ack_message_id=uuid4(),
                    occurred_at=NOW,
                )
            counts = inspect_counts(
                session, owner_ref=command.authenticated_owner_ref, command_id=command.command_id
            )
        assert counts["state_counts"][expected] == 1
        assert sum(counts["state_counts"].values()) == 1
        assert counts["visible_result_count"] == visible
        assert counts["ack_count"] == revision
        serialized = json.dumps(counts)
        schema_path = (
            Path(__file__).resolve().parents[3] / "contracts/w2-private/ct15-inspection.schema.json"
        )
        Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8"))).validate(counts)
        assert str(command.authenticated_owner_ref) not in serialized
        assert str(command.command_id) not in serialized
        assert "result_digest" not in counts
    assert counts["result_payload_count"] == counts["staged_payload_count"] == 0
    with session_factory.begin() as session:
        other = inspect_counts(session, owner_ref=uuid4(), command_id=command.command_id)
    assert all(value == 0 for value in other["state_counts"].values())
    assert other["ack_count"] == other["staged_outbox_count"] == 0


def test_synthetic_stage_accepts_result_larger_than_inbound_gate_limit(session_factory, tmp_path):
    from epick_engine.source_collection.commit_gate_operator import stage_synthetic_input

    command, result = _pair()
    raw = {"command": command.model_dump(mode="json"), "result": result.model_dump(mode="json")}
    raw["result"]["message_ko"] = "synthetic " * 2500
    input_path = tmp_path / "synthetic.json"
    input_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    stage_synthetic_input(
        session_factory,
        input_path,
        authority_provider=lambda command: _trusted_scope(command).decision,
    )
    with session_factory.begin() as session:
        counts = inspect_counts(
            session, owner_ref=command.authenticated_owner_ref, command_id=command.command_id
        )
    assert counts["state_counts"]["STAGED"] == 1


def test_synthetic_stage_rolls_back_when_wrapped_wire_exceeds_outbound_limit(
    session_factory, tmp_path
):
    from epick_engine.source_collection.commit_gate_operator import stage_synthetic_input
    from epick_engine.source_collection.commit_gate_runtime import MAX_OUTBOUND_MESSAGE_BYTES

    command, result = _pair()
    raw = {"command": command.model_dump(mode="json"), "result": result.model_dump(mode="json")}
    raw["result"]["message_ko"] = ""
    compact = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
    raw["result"]["message_ko"] = "x" * (
        MAX_OUTBOUND_MESSAGE_BYTES - 100 - len(compact.encode("utf-8"))
    )
    input_path = tmp_path / "synthetic.json"
    input_path.write_text(
        json.dumps(raw, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        stage_synthetic_input(
            session_factory,
            input_path,
            authority_provider=lambda command: _trusted_scope(command).decision,
        )
    with session_factory.begin() as session:
        counts = inspect_counts(
            session, owner_ref=command.authenticated_owner_ref, command_id=command.command_id
        )
    assert counts["staged_outbox_count"] == 0
    assert sum(counts["state_counts"].values()) == 0
