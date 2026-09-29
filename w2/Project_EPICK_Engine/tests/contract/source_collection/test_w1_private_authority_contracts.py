from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from epick_engine.source_collection.commit_gate_contracts import (
    build_commit_gate_ack,
    parse_commit_gate_command,
)
from epick_engine.source_collection.contracts import (
    CollectionCommand,
    CollectionStage,
    CoreSourceDecision,
)
from epick_engine.source_collection.w1_transport import W1WireContractError

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
PRIVATE_CONTRACTS = FIXTURES / "w1_private_contract"
OWNER_ID = UUID("22222222-2222-4222-8222-222222222222")
COMMAND_ID = UUID("11111111-1111-4111-8111-111111111111")
JOB_ID = UUID("33333333-3333-4333-8333-333333333333")
PROJECT_ID = UUID("88888888-8888-4888-8888-888888888888")


def _codec():
    name = "epick_engine.source_collection.w1_private_authority_contracts"
    assert importlib.util.find_spec(name) is not None, "W1 private authority codec is missing"
    return importlib.import_module(name)


def _binding(*, execution_fence: object = 1) -> dict[str, object]:
    return {
        "owner_user_id": str(OWNER_ID),
        "owner_deletion_epoch": 0,
        "command_id": str(COMMAND_ID),
        "job_id": str(JOB_ID),
        "execution_fence": execution_fence,
    }


def _write_request(*, scope: dict[str, str] | None = None) -> dict[str, object]:
    return {
        "schema_version": "w1.private.w2-write-authority.v1",
        **_binding(),
        "scope": {"type": "ACCOUNT"} if scope is None else scope,
    }


def _gate_request(*, action: str = "PREPARE") -> dict[str, object]:
    raw: dict[str, object] = {
        "schema_version": "w1.private.w2-gate-authority.v1",
        **_binding(),
        "scope": {"type": "ACCOUNT"},
        "operation_id": "21000000-0000-4000-8000-000000000001",
        "operation_revision": 3,
        "action": action,
        "phase": "APPLY",
        "result_digest": "sha256:" + "a" * 64,
    }
    if action == "PURGE":
        raw["purge_owner_deletion_epoch"] = 1
    return raw


def _command(execution_fence: str) -> CollectionCommand:
    return CollectionCommand(
        schema_version="w2.collection.v1",
        command_id=COMMAND_ID,
        job_id=JOB_ID,
        authenticated_owner_ref=OWNER_ID,
        project_ref=None,
        company_id=UUID("40000000-0000-4000-8000-000000000001"),
        source_id=UUID("50000000-0000-4000-8000-000000000001"),
        input_version=1,
        execution_fence=execution_fence,
        purpose_ref=UUID("60000000-0000-4000-8000-000000000001"),
        core_source_decision=CoreSourceDecision(
            is_core=True,
            decided_by="source-selection",
            rationale="Required source.",
            decision_revision=1,
            analysis_input_version=1,
        ),
        resume_stage=CollectionStage.FETCH,
        policy_revision=1,
        owner_deletion_epoch=0,
    )


def test_pinned_private_schema_hashes() -> None:
    expected = {
        "w2-current-write-scope-lookup.request.schema.json": (
            "6e408368995eba4d17144b963cf10c4ce11657e08ad8cacb93c566f1f4dd092f"
        ),
        "w2-current-write-scope-lookup.response.schema.json": (
            "7715bc88b2f293c571d95c60fead9d3bc9f5028e85e29e08003a1d089d693101"
        ),
        "w2-gate-scope-lookup.request.schema.json": (
            "15ab35ae09dd449b4f0a7d0a0ba707508ef4110d0f898c17c84ca6e3185cf832"
        ),
        "w2-gate-scope-lookup.response.schema.json": (
            "13615def394d4e0c48375a83fe1211574bc290bfbd6b4bbe8d8861ba13d2e3c0"
        ),
        "w2-terminal-cleanup-authority.request.schema.json": (
            "e2d1127c07fb88df249b2bc1294cc930b1191560d04408499587fc54a361e913"
        ),
        "w2-terminal-cleanup-authority.response.schema.json": (
            "cfba5f5c3886291430ff096fb61e8f09640ddc02e83de4cab26bf87338bbba88"
        ),
    }
    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in PRIVATE_CONTRACTS.glob("w2-*.schema.json")
    } == expected


def test_manifest_resolves_new_schema_revisions_without_relabeling_original_snapshots() -> None:
    manifest = json.loads((PRIVATE_CONTRACTS / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["revision_provenance"] == {
        "default_field": "revision",
        "override_field": "files[].revision",
    }
    default_revision = manifest["revision"]
    assert default_revision == "afec08a9602132e5e433b523b0b6804850440524"
    pinned_revision = "8d80a6f0edddd350a1e0308751fdb19bf7318d76"
    new_schema_files = {
        "w2-current-write-scope-lookup.request.schema.json",
        "w2-current-write-scope-lookup.response.schema.json",
        "w2-gate-scope-lookup.request.schema.json",
        "w2-gate-scope-lookup.response.schema.json",
        "w2-terminal-cleanup-authority.request.schema.json",
        "w2-terminal-cleanup-authority.response.schema.json",
    }

    resolved_revisions = {
        entry["file"]: entry.get("revision", default_revision) for entry in manifest["files"]
    }
    assert {name: resolved_revisions[name] for name in new_schema_files} == {
        name: pinned_revision for name in new_schema_files
    }
    assert {
        revision for name, revision in resolved_revisions.items() if name not in new_schema_files
    } == {default_revision}


def test_current_scope_cannot_carry_candidate_or_authority() -> None:
    codec = _codec()
    request = {
        "schema_version": "w1.private.w2-current-write-scope-lookup.v1",
        **_binding(),
    }
    response = {**request, "scope": {"type": "ACCOUNT"}}

    codec.CurrentWriteScopeLookupRequest.model_validate(request)
    codec.CurrentWriteScopeLookupResponse.model_validate(response)
    with pytest.raises(ValidationError):
        codec.CurrentWriteScopeLookupRequest.model_validate(
            {**request, "scope": {"type": "ACCOUNT"}}
        )
    with pytest.raises(ValidationError):
        codec.CurrentWriteScopeLookupRequest.model_validate({**request, "project_ref": None})
    with pytest.raises(ValidationError):
        codec.CurrentWriteScopeLookupResponse.model_validate(
            {**response, "authority_ref": "not-a-grant"}
        )


def test_private_response_rejects_wrong_echo_and_noncanonical_project() -> None:
    codec = _codec()
    request = codec.PrivateWriteAuthorityRequest.model_validate(
        _write_request(scope={"type": "PROJECT", "project_id": str(PROJECT_ID)})
    )
    response = codec.PrivateWriteAuthorityResponse.model_validate(
        {**request.model_dump(mode="json"), "authority_ref": "w1:decision:1"}
    )
    codec.validate_private_echo(request, response)

    changed = response.model_copy(update={"job_id": UUID("99999999-9999-4999-8999-999999999999")})
    with pytest.raises(W1WireContractError):
        codec.validate_private_echo(request, changed)
    with pytest.raises(ValidationError):
        codec.PrivateWriteAuthorityResponse.model_validate(
            {
                **response.model_dump(mode="json"),
                "scope": {"type": "PROJECT", "project_id": PROJECT_ID.hex},
            }
        )
    with pytest.raises(ValidationError):
        codec.PrivateWriteAuthorityResponse.model_validate(
            {**response.model_dump(mode="json"), "command_id": COMMAND_ID.hex}
        )


def test_w1_authority_model_field_sets_match_the_pinned_source() -> None:
    codec = _codec()
    binding = {
        "schema_version",
        "owner_user_id",
        "owner_deletion_epoch",
        "command_id",
        "job_id",
        "execution_fence",
    }
    scoped = binding | {"scope"}
    gate = scoped | {
        "operation_id",
        "operation_revision",
        "action",
        "phase",
        "result_digest",
        "purge_owner_deletion_epoch",
    }
    gate_lookup = gate - {"scope"}

    assert set(codec.CurrentWriteScopeLookupRequest.model_fields) == binding
    assert set(codec.CurrentWriteScopeLookupResponse.model_fields) == binding | {"scope"}
    assert set(codec.PrivateWriteAuthorityRequest.model_fields) == scoped
    assert set(codec.PrivateWriteAuthorityResponse.model_fields) == scoped | {"authority_ref"}
    assert set(codec.GateScopeLookupRequest.model_fields) == gate_lookup
    assert set(codec.GateScopeLookupResponse.model_fields) == gate_lookup | {"scope"}
    assert set(codec.GateAuthorityRequest.model_fields) == gate
    assert set(codec.GateAuthorityResponse.model_fields) == gate | {"authority_ref"}
    assert set(codec.TerminalCleanupAuthorityRequest.model_fields) == scoped | {"cleanup_kind"}
    assert set(codec.TerminalCleanupAuthorityResponse.model_fields) == scoped | {
        "cleanup_kind",
        "authority_ref",
        "allowed_effect",
    }


@pytest.mark.parametrize(
    ("model_name", "raw", "field", "value"),
    [
        ("CurrentWriteScopeLookupRequest", None, "owner_deletion_epoch", True),
        ("CurrentWriteScopeLookupRequest", None, "execution_fence", "1"),
        ("PrivateWriteAuthorityRequest", _write_request(), "execution_fence", 1.0),
        ("GateAuthorityRequest", _gate_request(), "operation_revision", "3"),
    ],
)
def test_w1_authority_models_reject_integer_coercion(
    model_name: str, raw: dict[str, object] | None, field: str, value: object
) -> None:
    codec = _codec()
    payload = (
        {
            "schema_version": "w1.private.w2-current-write-scope-lookup.v1",
            **_binding(),
        }
        if raw is None
        else raw
    )
    with pytest.raises(ValidationError):
        getattr(codec, model_name).model_validate({**payload, field: value})


def test_gate_models_preserve_non_purge_omission_and_require_newer_purge_epoch() -> None:
    codec = _codec()
    request = codec.GateAuthorityRequest.model_validate(_gate_request())
    response = codec.GateAuthorityResponse.model_validate(
        {**request.model_dump(mode="json"), "authority_ref": "w1:gate:decision"}
    )
    assert "purge_owner_deletion_epoch" not in request.model_fields_set
    assert "purge_owner_deletion_epoch" in response.model_fields_set
    assert "purge_owner_deletion_epoch" not in request.model_dump(exclude_none=True)
    codec.validate_private_echo(request, response)

    lookup_raw = {
        **_gate_request(),
        "schema_version": "w1.private.w2-gate-scope-lookup.v1",
    }
    lookup_raw.pop("scope")
    lookup = codec.GateScopeLookupRequest.model_validate(lookup_raw)
    assert "purge_owner_deletion_epoch" not in lookup.model_dump(exclude_none=True)
    with pytest.raises(ValidationError):
        codec.GateScopeLookupRequest.model_validate(
            {**lookup_raw, "purge_owner_deletion_epoch": None}
        )

    for raw in (
        {**_gate_request(action="PURGE"), "purge_owner_deletion_epoch": 0},
        {
            key: value
            for key, value in _gate_request(action="PURGE").items()
            if key != "purge_owner_deletion_epoch"
        },
        {**_gate_request(), "purge_owner_deletion_epoch": 1},
    ):
        with pytest.raises(ValidationError):
            codec.GateAuthorityRequest.model_validate(raw)


@pytest.mark.parametrize("execution_fence", ["0", "01", "+1", "1.0", str(2**63)])
def test_collection_binding_rejects_noncanonical_or_out_of_range_fence(
    execution_fence: str,
) -> None:
    with pytest.raises(W1WireContractError):
        _codec().W1PrivateBinding.from_collection(_command(execution_fence))


def test_private_and_gate_bindings_decode_collection_gate_and_ack() -> None:
    codec = _codec()
    collection = codec.W1PrivateBinding.from_collection(_command("7"))
    assert collection == codec.W1PrivateBinding(
        owner_user_id=OWNER_ID,
        owner_deletion_epoch=0,
        command_id=COMMAND_ID,
        job_id=JOB_ID,
        execution_fence=7,
    )

    gate = parse_commit_gate_command(
        json.loads(
            (PRIVATE_CONTRACTS / "private-w2-commit-gate-purge.json").read_text(encoding="utf-8")
        )
    )
    gate_binding = codec.W1GateBinding.from_gate(gate)
    ack = build_commit_gate_ack(
        gate,
        outcome="APPLIED",
        message_id=UUID("70000000-0000-4000-8000-000000000001"),
        occurred_at=datetime(2026, 9, 26, tzinfo=UTC),
    )
    assert codec.W1GateBinding.from_ack(ack) == gate_binding
    assert codec.W1PrivateBinding.from_gate(gate) == gate_binding.private
    assert codec.W1PrivateBinding.from_ack(ack) == gate_binding.private
    assert gate_binding.purge_owner_deletion_epoch is not None
