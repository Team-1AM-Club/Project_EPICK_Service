"""Offline W1 command validation and unadopted W2 wire proposal tests."""

from __future__ import annotations

import importlib
import importlib.util
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from epick_engine.source_collection.contracts import CollectionCommand, CollectionResult
from epick_engine.source_collection.w1_transport import W1WireContractError

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
W1 = FIXTURES / "w1_private_contract"
PROPOSAL = FIXTURES / "w2_commit_gate_proposal"
CONTRACTS = Path(__file__).resolve().parents[3] / "contracts/w2-private"
MESSAGE_ID = UUID("50000000-0000-4000-8000-000000000001")
OCCURRED_AT = datetime(2026, 9, 18, tzinfo=UTC)


def _codec():
    name = "epick_engine.source_collection.commit_gate_contracts"
    assert importlib.util.find_spec(name) is not None, "commit-gate codec is missing"
    return importlib.import_module(name)


def _load(directory: Path, name: str) -> dict:
    return json.loads((directory / name).read_text(encoding="utf-8"))


def _gate(action: str) -> dict:
    return _load(W1, f"private-w2-commit-gate-{action.lower()}.json")


def _validator(directory: Path, name: str) -> Draft202012Validator:
    return Draft202012Validator(_load(directory, name), format_checker=FormatChecker())


@pytest.mark.parametrize("action", ["PREPARE", "FINALIZE", "ABORT", "PURGE"])
def test_pinned_gate_actions_round_trip_without_repair(action: str) -> None:
    raw = _gate(action)
    _validator(W1, "private-w2-commit-gate.schema.json").validate(raw)
    parsed = _codec().parse_commit_gate_command(raw)
    assert parsed.model_dump(mode="json") == raw
    assert parsed.operation_id == UUID("21000000-0000-4000-8000-000000000001")
    assert parsed.execution_fence == 3


@pytest.mark.parametrize("field", ["owner", "command", "fence", "epoch", "digest", "revision"])
def test_pinned_structurally_invalid_gate_fixtures_are_rejected(field: str) -> None:
    raw = _load(W1, f"invalid-private-w2-commit-gate-{field}.json")
    assert list(_validator(W1, "private-w2-commit-gate.schema.json").iter_errors(raw))
    with pytest.raises(W1WireContractError):
        _codec().parse_commit_gate_command(raw)


@pytest.mark.parametrize("value", [None, 0, True, "1", 1.0])
def test_non_purge_rejects_even_explicit_null_purge_epoch(value: object) -> None:
    raw = _gate("PREPARE")
    raw["purge_owner_deletion_epoch"] = value
    with pytest.raises(W1WireContractError):
        _codec().parse_commit_gate_command(raw)


@pytest.mark.parametrize("epoch", [0, 4])
def test_purge_requires_newer_epoch(epoch: int) -> None:
    raw = _gate("PURGE")
    raw["owner_deletion_epoch"] = epoch
    raw["purge_owner_deletion_epoch"] = epoch
    with pytest.raises(W1WireContractError):
        _codec().parse_commit_gate_command(raw)


def test_purge_requires_present_integer_epoch() -> None:
    raw = _gate("PURGE")
    del raw["purge_owner_deletion_epoch"]
    with pytest.raises(W1WireContractError):
        _codec().parse_commit_gate_command(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operation_revision", True),
        ("operation_revision", "1"),
        ("execution_fence", True),
        ("execution_fence", 3.0),
        ("owner_deletion_epoch", False),
        ("owner_deletion_epoch", "0"),
        ("issued_at", "2026-09-17 00:00:00Z"),
        ("issued_at", "2026-09-17T00:00:00"),
        ("message_id", "20000000000040008000000000000001"),
        ("operation_id", "urn:uuid:21000000-0000-4000-8000-000000000001"),
        ("action", "UNKNOWN"),
        ("unexpected", "synthetic-private-marker"),
    ],
)
def test_gate_rejects_coercion_lexical_variants_and_unknown_fields(
    field: str, value: object
) -> None:
    raw = _gate("PREPARE")
    raw[field] = value
    with pytest.raises(W1WireContractError):
        _codec().parse_commit_gate_command(raw)


def test_valid_binding_changes_require_store_comparison_not_codec_constants() -> None:
    raw = _gate("PREPARE")
    raw["authenticated_owner_ref"] = str(MESSAGE_ID)
    raw["operation_revision"] = 100
    assert _codec().parse_commit_gate_command(raw).authenticated_owner_ref == MESSAGE_ID


def _pair() -> tuple[CollectionCommand, CollectionResult]:
    vector = _load(PROPOSAL, "digest-vector.json")
    return (
        CollectionCommand.model_validate(vector["command"]),
        CollectionResult.model_validate(vector["result"]),
    )


def _proposal_codec():
    codec = _codec()
    assert hasattr(codec, "staged_result_digest"), "digest proposal is missing"
    assert hasattr(codec, "build_staged_result"), "staged-result proposal is missing"
    assert hasattr(codec, "build_commit_gate_ack"), "ACK proposal is missing"
    return codec


def test_digest_matches_independently_fixed_unicode_vector() -> None:
    command, result = _pair()
    assert _proposal_codec().staged_result_digest(command, result) == (
        "sha256:430a2e14098d3dd309f6bfb0a23f75e0e6fb5f6e80cc00ea8ff0dd81ecab0360"
    )


def test_digest_preserves_array_order_and_does_not_normalize_unicode() -> None:
    command, result = _pair()
    codec = _proposal_codec()
    failure = result.failures[0].model_copy(
        update={"missing_sections": list(reversed(result.failures[0].missing_sections))}
    )
    reordered = result.model_copy(update={"failures": [failure]})
    composed = result.model_copy(update={"message_ko": result.message_ko.replace("e\u0301", "é")})
    assert codec.staged_result_digest(command, reordered) != codec.staged_result_digest(
        command, result
    )
    assert codec.staged_result_digest(command, composed) != codec.staged_result_digest(
        command, result
    )


@pytest.mark.parametrize("field", ["command_id", "job_id", "source_id", "input_version"])
def test_digest_rejects_command_result_binding_mismatch(field: str) -> None:
    command, result = _pair()
    changed = command.model_copy(update={field: 2 if field == "input_version" else MESSAGE_ID})
    if field == "input_version":
        changed = changed.model_copy(
            update={
                "core_source_decision": command.core_source_decision.model_copy(
                    update={"analysis_input_version": 2}
                )
            }
        )
    with pytest.raises(W1WireContractError):
        _proposal_codec().staged_result_digest(changed, result)


@pytest.mark.parametrize("fence", ["alpha-domain-fence", "0", "01", "+1", " 1", "١"])
def test_staged_path_rejects_noncanonical_w1_decimal_fence(fence: str) -> None:
    command, result = _pair()
    with pytest.raises(W1WireContractError):
        _proposal_codec().staged_result_digest(
            command.model_copy(update={"execution_fence": fence}), result
        )


@pytest.mark.parametrize("target", ["command", "result"])
@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), "1"])
def test_digest_revalidates_copied_models_without_serializer_coercion(
    target: str, value: object
) -> None:
    command, result = _pair()
    if target == "command":
        command = command.model_copy(update={"input_version": value})
    else:
        result = result.model_copy(update={"result_version": value})
    with pytest.raises(W1WireContractError):
        _proposal_codec().staged_result_digest(command, result)


def test_digest_revalidates_nested_copied_models() -> None:
    command, result = _pair()
    invalid = command.core_source_decision.model_copy(update={"decision_revision": True})
    with pytest.raises(W1WireContractError):
        _proposal_codec().staged_result_digest(
            command.model_copy(update={"core_source_decision": invalid}), result
        )


def test_staged_result_preserves_binding_result_and_caller_delivery_identity() -> None:
    command, result = _pair()
    codec = _proposal_codec()
    first = codec.build_staged_result(
        command, result, message_id=MESSAGE_ID, occurred_at=OCCURRED_AT
    )
    replay = codec.build_staged_result(
        command, result, message_id=MESSAGE_ID, occurred_at=OCCURRED_AT
    )
    assert first.model_dump(mode="json") == replay.model_dump(mode="json")
    assert first.message_id == MESSAGE_ID
    assert first.occurred_at == OCCURRED_AT
    assert first.schema_version == "w2.private.staged-result.proposal.v1"
    assert first.command == command
    assert first.result == result
    assert (
        first.result_digest
        == "sha256:430a2e14098d3dd309f6bfb0a23f75e0e6fb5f6e80cc00ea8ff0dd81ecab0360"
    )
    assert "operation_id" not in first.model_dump(mode="json")
    assert "execution_lease_id" not in first.model_dump(mode="json")


@pytest.mark.parametrize("action", ["PREPARE", "FINALIZE", "ABORT", "PURGE"])
@pytest.mark.parametrize("outcome", ["APPLIED", "DUPLICATE", "REJECTED"])
def test_ack_preserves_every_gate_binding_and_distinguishes_outcome(
    action: str, outcome: str
) -> None:
    codec = _proposal_codec()
    command = codec.parse_commit_gate_command(_gate(action))
    ack = codec.build_commit_gate_ack(
        command, outcome=outcome, message_id=MESSAGE_ID, occurred_at=OCCURRED_AT
    )
    replay = codec.build_commit_gate_ack(
        command, outcome=outcome, message_id=MESSAGE_ID, occurred_at=OCCURRED_AT
    )
    assert ack.model_dump(mode="json") == replay.model_dump(mode="json")
    assert ack.schema_version == "w2.private.commit-gate-ack.proposal.v1"
    assert ack.outcome == outcome
    raw = ack.model_dump(mode="json")
    for name in [
        "operation_id",
        "operation_revision",
        "action",
        "command_id",
        "job_id",
        "authenticated_owner_ref",
        "execution_fence",
        "owner_deletion_epoch",
        "result_digest",
    ]:
        assert raw[name] == _gate(action)[name]
    assert ("purge_owner_deletion_epoch" in raw) == (action == "PURGE")


@pytest.mark.parametrize("field", ["operation_revision", "execution_fence", "owner_deletion_epoch"])
def test_ack_revalidates_copied_gate_integers(field: str) -> None:
    codec = _proposal_codec()
    command = codec.parse_commit_gate_command(_gate("PREPARE")).model_copy(update={field: True})
    with pytest.raises(W1WireContractError):
        codec.build_commit_gate_ack(
            command, outcome="APPLIED", message_id=MESSAGE_ID, occurred_at=OCCURRED_AT
        )


@pytest.mark.parametrize("value", [None, "SUCCESS", True])
def test_ack_rejects_unknown_outcome(value: object) -> None:
    codec = _proposal_codec()
    command = codec.parse_commit_gate_command(_gate("PREPARE"))
    with pytest.raises(W1WireContractError):
        codec.build_commit_gate_ack(
            command, outcome=value, message_id=MESSAGE_ID, occurred_at=OCCURRED_AT
        )


@pytest.mark.parametrize(
    "message_id,occurred_at",
    [
        (None, OCCURRED_AT),
        (str(MESSAGE_ID), OCCURRED_AT),
        (MESSAGE_ID, None),
        (MESSAGE_ID, datetime(2026, 9, 18)),
    ],
)
def test_proposals_require_caller_persisted_uuid_and_aware_timestamp(
    message_id: object, occurred_at: object
) -> None:
    codec = _proposal_codec()
    command, result = _pair()
    with pytest.raises(W1WireContractError):
        codec.build_staged_result(command, result, message_id=message_id, occurred_at=occurred_at)
    with pytest.raises(W1WireContractError):
        codec.build_commit_gate_ack(
            codec.parse_commit_gate_command(_gate("PREPARE")),
            outcome="APPLIED",
            message_id=message_id,
            occurred_at=occurred_at,
        )


def test_gate_diagnostics_never_echo_or_log_payload(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = deepcopy(_gate("PREPARE"))
    marker = "synthetic-private-marker-not-a-secret"
    raw["unexpected"] = marker
    with pytest.raises(W1WireContractError) as caught:
        _codec().parse_commit_gate_command(raw)
    assert marker not in str(caught.value)
    assert caught.value.__suppress_context__ is True
    assert caplog.records == []
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize(
    "name",
    ["staged-result.json"]
    + [
        f"ack-{action}-{outcome}.json"
        for action in ["prepare", "finalize", "abort", "purge"]
        for outcome in ["applied", "duplicate", "rejected"]
    ],
)
def test_proposal_fixtures_validate_in_schema_and_model_and_rebuild_identically(name: str) -> None:
    codec = _proposal_codec()
    raw = _load(PROPOSAL, name)
    if name.startswith("ack-"):
        schema = "source-collection.commit-gate-ack.schema.json"
        model = codec.CommitGateAckProposal
        rebuilt = codec.build_commit_gate_ack(
            codec.parse_commit_gate_command(_gate(raw["action"])),
            outcome=raw["outcome"],
            message_id=UUID(raw["message_id"]),
            occurred_at=datetime.fromisoformat(raw["occurred_at"]),
        )
    else:
        schema = "source-collection.staged-result.schema.json"
        model = codec.StagedResultProposal
        rebuilt = codec.build_staged_result(
            CollectionCommand.model_validate(raw["command"]),
            CollectionResult.model_validate(raw["result"]),
            message_id=UUID(raw["message_id"]),
            occurred_at=datetime.fromisoformat(raw["occurred_at"]),
        )
    _validator(CONTRACTS, schema).validate(raw)
    parsed = codec._parse_wire(raw, model, label="W2 proposal fixture")
    assert parsed.model_dump(mode="json") == raw
    assert rebuilt.model_dump(mode="json") == raw


@pytest.mark.parametrize(
    "name",
    [
        "invalid-staged-fence.json",
        "invalid-staged-message-id.json",
        "invalid-staged-digest.json",
        "invalid-ack-outcome.json",
        "invalid-ack-nonpurge-epoch-null.json",
        "invalid-ack-bool-revision.json",
    ],
)
def test_structural_proposal_rejections_are_shared_by_schema_and_model(name: str) -> None:
    codec = _proposal_codec()
    raw = _load(PROPOSAL, name)
    staged = "staged" in name
    schema = (
        "source-collection.staged-result.schema.json"
        if staged
        else "source-collection.commit-gate-ack.schema.json"
    )
    model = codec.StagedResultProposal if staged else codec.CommitGateAckProposal
    assert list(_validator(CONTRACTS, schema).iter_errors(raw))
    with pytest.raises(W1WireContractError):
        codec._parse_wire(raw, model, label="W2 proposal fixture")


@pytest.mark.parametrize(
    "name",
    [
        "semantic-invalid-staged-binding.json",
        "semantic-invalid-staged-digest.json",
        "semantic-invalid-ack-purge-epoch.json",
    ],
)
def test_schema_cannot_replace_model_cross_field_and_digest_checks(name: str) -> None:
    codec = _proposal_codec()
    raw = _load(PROPOSAL, name)
    staged = "staged" in name
    schema = (
        "source-collection.staged-result.schema.json"
        if staged
        else "source-collection.commit-gate-ack.schema.json"
    )
    model = codec.StagedResultProposal if staged else codec.CommitGateAckProposal
    _validator(CONTRACTS, schema).validate(raw)
    with pytest.raises(W1WireContractError):
        codec._parse_wire(raw, model, label="W2 proposal fixture")


def test_snapshot_and_proposal_checksums_pin_exact_lf_bytes() -> None:
    import hashlib

    manifest = _load(W1, "manifest.json")
    assert {entry["file"] for entry in manifest["files"]} == {
        path.name for path in W1.glob("*.json") if path.name != "manifest.json"
    }
    for entry in manifest["files"]:
        content = (W1 / entry["file"]).read_bytes()
        assert b"\r" not in content
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]
        assert (
            hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
            == entry["git_blob_sha"]
        )
    proposal = _load(PROPOSAL, "manifest.json")
    assert proposal["contract_status"] == "W2_UNADOPTED_PROPOSAL_QUEUE_DISCONNECTED"
    assert {entry["file"] for entry in proposal["files"]} == {
        path.name for path in PROPOSAL.glob("*.json") if path.name != "manifest.json"
    }
    for entry in proposal["files"]:
        content = (PROPOSAL / entry["file"]).read_bytes()
        assert b"\r" not in content
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]
    for entry in proposal["schemas"]:
        assert (
            hashlib.sha256((CONTRACTS / entry["file"]).read_bytes()).hexdigest() == entry["sha256"]
        )


@pytest.mark.parametrize("field", ["message_id", "occurred_at"])
def test_delivery_identity_is_not_part_of_staged_digest(field: str) -> None:
    command, result = _pair()
    codec = _proposal_codec()
    identity = UUID("50000000-0000-4000-8000-000000000099") if field == "message_id" else MESSAGE_ID
    instant = datetime(2026, 9, 19, tzinfo=UTC) if field == "occurred_at" else OCCURRED_AT
    first = codec.build_staged_result(
        command, result, message_id=MESSAGE_ID, occurred_at=OCCURRED_AT
    )
    changed = codec.build_staged_result(command, result, message_id=identity, occurred_at=instant)
    assert changed.result_digest == first.result_digest
    assert getattr(changed, field) != getattr(first, field)


def test_ack_rejects_model_copy_with_explicit_nonpurge_null() -> None:
    codec = _proposal_codec()
    command = codec.parse_commit_gate_command(_gate("PREPARE")).model_copy(
        update={"purge_owner_deletion_epoch": None}
    )
    with pytest.raises(W1WireContractError):
        codec.build_commit_gate_ack(
            command, outcome="APPLIED", message_id=MESSAGE_ID, occurred_at=OCCURRED_AT
        )


@pytest.mark.parametrize("action", ["PREPARE", "FINALIZE", "ABORT"])
@pytest.mark.parametrize("kind", ["gate", "ack"])
def test_shared_revalidation_rejects_copied_explicit_nonpurge_null(action: str, kind: str) -> None:
    codec = _proposal_codec()
    value = codec.parse_commit_gate_command(_gate(action))
    if kind == "ack":
        value = codec.build_commit_gate_ack(
            value, outcome="APPLIED", message_id=MESSAGE_ID, occurred_at=OCCURRED_AT
        )
    copied = value.model_copy(update={"purge_owner_deletion_epoch": None})
    with pytest.raises(W1WireContractError):
        codec._revalidate_model(copied, type(copied), label="W2 stored gate binding")
