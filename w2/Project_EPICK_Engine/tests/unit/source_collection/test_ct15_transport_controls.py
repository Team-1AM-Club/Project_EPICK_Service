"""Bounded synthetic controls never manufacture an authenticated W1 delivery."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from epick_engine.source_collection.commit_gate_contracts import (
    StagedResultProposal,
    build_commit_gate_ack,
    build_staged_result,
    parse_commit_gate_command,
)
from epick_engine.source_collection.commit_gate_runtime import QueueDelivery, _wire_body
from epick_engine.source_collection.contracts import CollectionCommand, CollectionResult
from epick_engine.source_collection.ct15_transport_controls import (
    ControlledQueue,
    Ct15ControlError,
    main,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
NOW = datetime(2026, 9, 20, tzinfo=UTC)
SENDER = "AROASYNTHETICROLE01"
CONFIGURED_SENDER = "AROASYNTHETICW1ROLE12"


def ct15_environment() -> dict[str, str]:
    return {
        "W2_CT15_ENABLED": "true",
        "W2_CT15_GATE_ONLY_QUEUE_APPROVED": "true",
        "W2_CT15_DATABASE_URL": "postgresql+psycopg://synthetic@localhost/epick_ct15",
        "W2_CT15_REGION": "ap-northeast-2",
        "W2_CT15_COMMAND_QUEUE_URL": (
            "https://sqs.ap-northeast-2.amazonaws.com/123456789012/test-ct15-command"
        ),
        "W2_CT15_INBOUND_QUEUE_URL": (
            "https://sqs.ap-northeast-2.amazonaws.com/123456789012/test-ct15-inbound"
        ),
        "W2_CT15_EXPECTED_W1_SENDER_ID": CONFIGURED_SENDER,
        "W2_CT15_RUNTIME_LABEL": "epick-ct15-synthetic",
        "W1_LOOKUP_ENDPOINT": "https://lookup.example.test/internal/v1/job-commands/lookup",
        "W1_LOOKUP_BEARER": "BEARER-CANARY",
        "W1_LOOKUP_CA_FILE": "/run/epick/source-runtime/w1-ca.pem",
    }


@dataclass
class Queue:
    deliveries: list[QueueDelivery] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    def receive(self):
        return self.deliveries

    def send(self, body):
        self.sent.append(body)

    def delete(self, receipt):
        self.deleted.append(receipt)


def pair():
    raw = json.loads(
        (FIXTURES / "w2_commit_gate_proposal/digest-vector.json").read_text(encoding="utf-8")
    )
    return CollectionCommand.model_validate(raw["command"]), CollectionResult.model_validate(
        raw["result"]
    )


def control(mode, *, approved=True, run_id="ct15-control-test"):
    queue = Queue()
    command, result = pair()
    wrapper = ControlledQueue(
        queue,
        command=command,
        run_id=run_id,
        mode=mode,
        approved=approved,
        expected_sender_id=SENDER,
    )
    staged = build_staged_result(command, result, message_id=uuid4(), occurred_at=NOW)
    return wrapper, queue, staged


def gate(action="PREPARE"):
    raw = json.loads(
        (FIXTURES / f"w1_private_contract/private-w2-commit-gate-{action.lower()}.json").read_text(
            encoding="utf-8"
        )
    )
    command, _ = pair()
    raw.update(
        command_id=str(command.command_id),
        job_id=str(command.job_id),
        authenticated_owner_ref=str(command.authenticated_owner_ref),
        execution_fence=int(command.execution_fence),
        owner_deletion_epoch=command.owner_deletion_epoch,
    )
    return parse_commit_gate_command(raw)


@pytest.mark.parametrize("approved", [False, None, "true", 1])
def test_opt_in_must_be_explicit_boolean(approved):
    with pytest.raises(Ct15ControlError):
        control("duplicate", approved=approved)


@pytest.mark.parametrize("run_id", ["production", "ct15-", "CT15-demo", "ct15-a/../../x"])
def test_run_id_is_bounded_synthetic_name(run_id):
    with pytest.raises(Ct15ControlError):
        control("duplicate", run_id=run_id)


def test_duplicate_uses_exact_original_wire_and_safe_evidence():
    wrapper, queue, staged = control("duplicate")
    wire = _wire_body(staged.model_dump(mode="json"))
    wrapper.send(wire)
    assert queue.sent == [wire, wire]
    evidence = wrapper.evidence()
    assert evidence["control_applied"] is True
    assert evidence["send_count"] == 2
    encoded = json.dumps(evidence)
    assert str(staged.message_id) in encoded
    assert "message_ko" not in encoded
    assert "authenticated_owner_ref" not in encoded
    assert "receipt" not in encoded


def test_conflict_preserves_id_but_has_valid_different_digest():
    wrapper, queue, staged = control("conflict")
    original = _wire_body(staged.model_dump(mode="json"))
    wrapper.send(original)
    assert queue.sent[0] == original
    conflict = StagedResultProposal.model_validate_json(queue.sent[1])
    assert conflict.message_id == staged.message_id
    assert conflict.command == staged.command
    assert conflict.result_digest != staged.result_digest
    assert conflict.result.message_ko != staged.result.message_ko


@pytest.mark.parametrize("mode,send_count", [("drop-ack", 0), ("ack-send-uncertain", 1)])
def test_ack_loss_is_failure_not_success(mode, send_count):
    wrapper, queue, _ = control(mode)
    ack = build_commit_gate_ack(gate(), outcome="APPLIED", message_id=uuid4(), occurred_at=NOW)
    wire = _wire_body(ack.model_dump(mode="json"))
    with pytest.raises(Ct15ControlError, match="synthetic transport interruption"):
        wrapper.send(wire)
    assert queue.sent == [wire] * send_count
    assert wrapper.evidence()["control_applied"] is True


@pytest.mark.parametrize("mode", ["retain", "retain-finalize"])
def test_retention_uses_real_received_handle_without_gate_injection(mode):
    wrapper, queue, _ = control(mode)
    command = gate("FINALIZE")
    delivery = QueueDelivery("private-receipt", _wire_body(command.model_dump(mode="json")), SENDER)
    queue.deliveries.append(delivery)
    assert wrapper.receive() == [delivery]
    wrapper.delete("private-receipt")
    assert queue.deleted == []
    assert queue.sent == []
    assert wrapper.evidence()["delete_suppressed_count"] == 1
    assert "private-receipt" not in json.dumps(wrapper.evidence())


def test_retain_finalize_does_not_suppress_prepare_delete():
    wrapper, queue, _ = control("retain-finalize")
    queue.deliveries.append(QueueDelivery("r", _wire_body(gate().model_dump(mode="json")), SENDER))
    wrapper.receive()
    wrapper.delete("r")
    assert queue.deleted == ["r"]
    assert wrapper.evidence()["control_applied"] is False


def test_unauthenticated_delivery_is_not_promoted_or_captured():
    wrapper, queue, _ = control("retain")
    delivery = QueueDelivery("r", "not-json", "UNTRUSTED")
    queue.deliveries.append(delivery)
    assert wrapper.receive() == [delivery]
    assert wrapper.evidence()["received_count"] == 0
    with pytest.raises(Ct15ControlError):
        wrapper.delete("r")


def test_other_command_and_gate_as_outbound_are_rejected():
    wrapper, queue, staged = control("duplicate")
    other = staged.model_dump(mode="json")
    other["command"]["job_id"] = str(uuid4())
    with pytest.raises(Ct15ControlError):
        wrapper.send(_wire_body(other))
    with pytest.raises(Ct15ControlError):
        wrapper.send(_wire_body(gate().model_dump(mode="json")))
    assert queue.sent == []


def test_conflict_cannot_modify_ack():
    wrapper, queue, _ = control("conflict")
    ack = build_commit_gate_ack(gate(), outcome="APPLIED", message_id=uuid4(), occurred_at=NOW)
    with pytest.raises(Ct15ControlError):
        wrapper.send(_wire_body(ack.model_dump(mode="json")))
    assert queue.sent == []


def test_cli_without_control_approval_never_constructs_sdk(monkeypatch, capsys):
    from epick_engine.source_collection import ct15_transport_controls as module

    monkeypatch.delenv("W2_CT15_TRANSPORT_CONTROLS", raising=False)

    def forbidden(*args):
        raise AssertionError("SDK must not be reached")

    monkeypatch.setattr(module, "create_sqs_client", forbidden)
    assert main(["duplicate", "--run-id", "ct15-test", "--command", "private-secret-path"]) == 1
    assert json.loads(capsys.readouterr().out) == {"status": "CT15_CONTROL_FAILED"}


def test_control_is_bounded_to_one_send_and_cannot_receive():
    wrapper, queue, staged = control("duplicate")
    body = _wire_body(staged.model_dump(mode="json"))
    wrapper.send(body)
    with pytest.raises(Ct15ControlError):
        wrapper.send(body)
    with pytest.raises(Ct15ControlError):
        wrapper.receive()
    assert queue.sent == [body, body]


@pytest.mark.parametrize("mode", ["duplicate", "conflict"])
def test_second_send_failure_does_not_claim_control_was_applied(mode, monkeypatch):
    wrapper, queue, staged = control(mode)

    def fail_second(body):
        if queue.sent:
            raise RuntimeError("synthetic unconfirmed second handoff")
        queue.sent.append(body)

    monkeypatch.setattr(queue, "send", fail_second)
    with pytest.raises(RuntimeError):
        wrapper.send(_wire_body(staged.model_dump(mode="json")))
    evidence = wrapper.evidence()
    assert evidence["send_count"] == 1
    assert evidence["control_applied"] is False


def test_scope_mismatch_incoming_keeps_receipt_and_outputs_no_body():
    wrapper, queue, _ = control("retain")
    raw = gate().model_dump(mode="json")
    raw["authenticated_owner_ref"] = str(uuid4())
    queue.deliveries.append(QueueDelivery("private-receipt", _wire_body(raw), SENDER))
    with pytest.raises(Ct15ControlError, match="synthetic gate control input rejected"):
        wrapper.receive()
    assert queue.deleted == []
    assert wrapper.evidence()["messages"] == []


@pytest.mark.parametrize("mode", ["retain", "retain-finalize"])
def test_cli_retain_paths_inject_same_configured_private_authority_client(
    monkeypatch, tmp_path, capsys, mode
):
    from epick_engine.source_collection import ct15_transport_controls as module

    command, _ = pair()
    path = tmp_path / "command.json"
    path.write_text(command.model_dump_json(), encoding="utf-8")
    settings = module.Ct15Settings.from_environment(ct15_environment())
    authority_client = object()
    factory_settings = []
    observed = []
    finalize = gate("FINALIZE")
    sdk = SimpleNamespace(
        receive_message=lambda **_kwargs: {
            "Messages": [
                {
                    "Body": _wire_body(finalize.model_dump(mode="json")),
                    "ReceiptHandle": "private-receipt",
                    "Attributes": {"SenderId": CONFIGURED_SENDER},
                }
            ]
        },
        delete_message=lambda **_kwargs: pytest.fail("retained receipt must not be deleted"),
        send_message=lambda **_kwargs: pytest.fail("retain control must not send"),
    )

    monkeypatch.setenv("W2_CT15_TRANSPORT_CONTROLS", "true")
    monkeypatch.setattr(module.Ct15Settings, "from_environment", lambda _env: settings)
    monkeypatch.setattr(module, "create_sqs_client", lambda _settings: sdk)
    monkeypatch.setattr(
        module, "create_ct15_engine", lambda _settings: SimpleNamespace(dispose=lambda: None)
    )
    monkeypatch.setattr(module, "preflight", lambda *_args: {})
    monkeypatch.setattr(module, "sessionmaker", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        module,
        "create_private_authority_client",
        lambda actual_settings: factory_settings.append(actual_settings) or authority_client,
    )

    def strict_consume_once(_sessions, queue, expected_sender_id, *, private_authority_client):
        observed.append(private_authority_client)
        assert expected_sender_id == CONFIGURED_SENDER
        delivery = queue.receive()[0]
        queue.delete(delivery.receipt_handle)
        return SimpleNamespace(status="APPLIED")

    monkeypatch.setattr(module, "consume_once", strict_consume_once)

    assert main([mode, "--run-id", "ct15-test", "--command", str(path)]) == 0
    assert factory_settings == [settings]
    assert observed == [authority_client]
    output = json.loads(capsys.readouterr().out)
    assert output["control_applied"] is True
    assert output["delete_suppressed_count"] == 1


def test_cli_relay_control_injects_same_configured_private_authority_client(
    monkeypatch, tmp_path, capsys
):
    from epick_engine.source_collection import ct15_transport_controls as module

    command, result = pair()
    path = tmp_path / "command.json"
    path.write_text(command.model_dump_json(), encoding="utf-8")
    settings = module.Ct15Settings.from_environment(ct15_environment())
    authority_client = object()
    factory_settings = []
    observed = []
    sent = []

    def confirm_send(**kwargs):
        body = kwargs["MessageBody"]
        sent.append(body)
        return {
            "MessageId": "synthetic-message-id",
            "MD5OfMessageBody": hashlib.md5(
                body.encode("utf-8"), usedforsecurity=False
            ).hexdigest(),
        }

    sdk = SimpleNamespace(
        send_message=confirm_send,
        receive_message=lambda **_kwargs: pytest.fail("relay control must not receive"),
        delete_message=lambda **_kwargs: pytest.fail("relay control must not delete"),
    )

    monkeypatch.setenv("W2_CT15_TRANSPORT_CONTROLS", "true")
    monkeypatch.setattr(module.Ct15Settings, "from_environment", lambda _env: settings)
    monkeypatch.setattr(module, "create_sqs_client", lambda _settings: sdk)
    monkeypatch.setattr(
        module, "create_ct15_engine", lambda _settings: SimpleNamespace(dispose=lambda: None)
    )
    monkeypatch.setattr(module, "preflight", lambda *_args: {})
    monkeypatch.setattr(module, "sessionmaker", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(
        module,
        "create_private_authority_client",
        lambda actual_settings: factory_settings.append(actual_settings) or authority_client,
    )

    def strict_relay_once(_sessions, queue, *, command_id, authority_client):
        observed.append(authority_client)
        assert command_id == command.command_id
        staged = build_staged_result(command, result, message_id=uuid4(), occurred_at=NOW)
        queue.send(_wire_body(staged.model_dump(mode="json")))
        return SimpleNamespace(status="SENT")

    monkeypatch.setattr(module, "relay_once", strict_relay_once)

    assert main(["duplicate", "--run-id", "ct15-test", "--command", str(path)]) == 0
    assert factory_settings == [settings]
    assert observed == [authority_client]
    assert len(sent) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["control_applied"] is True
    assert output["send_count"] == 2


def test_cli_missing_w1_configuration_fails_before_transport_effects(monkeypatch, tmp_path, capsys):
    from epick_engine.source_collection import ct15_transport_controls as module

    command, _ = pair()
    path = tmp_path / "command.json"
    path.write_text(command.model_dump_json(), encoding="utf-8")
    values = ct15_environment()
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("W1_LOOKUP_BEARER")
    monkeypatch.setenv("W2_CT15_TRANSPORT_CONTROLS", "true")
    effects = []
    monkeypatch.setattr(
        module, "create_sqs_client", lambda _settings: effects.append("transport-created")
    )
    monkeypatch.setattr(
        module,
        "create_private_authority_client",
        lambda _settings: effects.append("authority-created"),
    )

    assert main(["duplicate", "--run-id", "ct15-test", "--command", str(path)]) == 1
    assert effects == []
    assert json.loads(capsys.readouterr().out) == {"status": "CT15_CONTROL_FAILED"}


@pytest.mark.parametrize("status", ["EMPTY", "SENT", "APPLIED"])
def test_cli_untriggered_control_is_not_success(monkeypatch, tmp_path, capsys, status):
    from epick_engine.source_collection import ct15_transport_controls as module

    command, _ = pair()
    path = tmp_path / "command.json"
    path.write_text(command.model_dump_json(), encoding="utf-8")
    monkeypatch.setenv("W2_CT15_TRANSPORT_CONTROLS", "true")
    monkeypatch.setattr(
        module.Ct15Settings,
        "from_environment",
        lambda _env: SimpleNamespace(expected_w1_sender_id=SENDER),
    )
    monkeypatch.setattr(module, "create_sqs_client", lambda _settings: object())
    monkeypatch.setattr(
        module, "create_ct15_engine", lambda _settings: SimpleNamespace(dispose=lambda: None)
    )
    monkeypatch.setattr(module, "preflight", lambda *_args: {})
    monkeypatch.setattr(module, "sessionmaker", lambda *_args, **_kwargs: object())
    expected_authority_client = object()
    monkeypatch.setattr(
        module,
        "create_private_authority_client",
        lambda _settings: expected_authority_client,
    )

    def strict_relay_once(*_args, authority_client: object, **_kwargs):
        assert authority_client is expected_authority_client
        return SimpleNamespace(status=status)

    monkeypatch.setattr(module, "relay_once", strict_relay_once)
    assert main(["drop-ack", "--run-id", "ct15-test", "--command", str(path)]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == status
    assert output["control_applied"] is False
