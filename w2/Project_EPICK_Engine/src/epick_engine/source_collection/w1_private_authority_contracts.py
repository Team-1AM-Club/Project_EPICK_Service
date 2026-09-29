"""Strict codecs for W1's revision-pinned private authority decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, Field, field_validator, model_validator

from epick_engine.source_collection.commit_gate_contracts import (
    CommitGateAckProposal,
    CommitGateAction,
    CommitGateCommand,
)
from epick_engine.source_collection.contracts import CollectionCommand, ContractModel
from epick_engine.source_collection.private_scope import SIGNED_64_MAX
from epick_engine.source_collection.w1_transport import (
    W1WireContractError,
    _revalidate_model,
)

_WIRE_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
_RESULT_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def _require_wire_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str) or _WIRE_UUID.fullmatch(value) is None:
        raise ValueError("wire UUID requires hyphenated hexadecimal syntax")
    return UUID(value)


type CanonicalWireUUID = Annotated[UUID, BeforeValidator(_require_wire_uuid)]
type PositiveSigned64Int = Annotated[int, Field(strict=True, ge=1, le=SIGNED_64_MAX)]
type NonNegativeSigned64Int = Annotated[int, Field(strict=True, ge=0, le=SIGNED_64_MAX)]
type PrivateScopeMapping = dict[str, str]
type GatePhase = Literal["APPLY", "ACK_RELAY"]
type CleanupKind = Literal["RESERVATION_RELEASE", "CLAIM_RELEASE", "STAGED_OUTBOX"]


def _canonical_scope(value: PrivateScopeMapping) -> PrivateScopeMapping:
    if value == {"type": "ACCOUNT"}:
        return value
    if value.keys() != {"type", "project_id"} or value.get("type") != "PROJECT":
        raise ValueError("private write scope must be explicit")
    try:
        project_id = UUID(value["project_id"])
    except (KeyError, ValueError):
        raise ValueError("private write project ID must be canonical") from None
    if value["project_id"] != str(project_id):
        raise ValueError("private write project ID must be canonical")
    return value


class _PrivateBindingModel(ContractModel):
    schema_version: str
    owner_user_id: CanonicalWireUUID
    owner_deletion_epoch: NonNegativeSigned64Int
    command_id: CanonicalWireUUID
    job_id: CanonicalWireUUID
    execution_fence: PositiveSigned64Int


class CurrentWriteScopeLookupRequest(_PrivateBindingModel):
    schema_version: Literal["w1.private.w2-current-write-scope-lookup.v1"]


class CurrentWriteScopeLookupResponse(CurrentWriteScopeLookupRequest):
    scope: PrivateScopeMapping

    _require_canonical_scope = field_validator("scope")(_canonical_scope)


class _PrivateScopedModel(_PrivateBindingModel):
    scope: PrivateScopeMapping

    _require_canonical_scope = field_validator("scope")(_canonical_scope)


class PrivateWriteAuthorityRequest(_PrivateScopedModel):
    schema_version: Literal["w1.private.w2-write-authority.v1"]


class PrivateWriteAuthorityResponse(PrivateWriteAuthorityRequest):
    authority_ref: str


class TerminalCleanupAuthorityRequest(_PrivateScopedModel):
    schema_version: Literal["w1.private.w2-terminal-cleanup.v1"]
    cleanup_kind: CleanupKind


class TerminalCleanupAuthorityResponse(TerminalCleanupAuthorityRequest):
    authority_ref: str
    allowed_effect: Literal["OWNER_LOCKED_PRIVATE_CLEANUP_ONLY"]


class GateAuthorityRequest(_PrivateScopedModel):
    schema_version: Literal["w1.private.w2-gate-authority.v1"]
    operation_id: CanonicalWireUUID
    operation_revision: PositiveSigned64Int
    action: CommitGateAction
    phase: GatePhase
    result_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    purge_owner_deletion_epoch: PositiveSigned64Int | None = None

    @model_validator(mode="after")
    def validate_purge_epoch(self) -> Self:
        if self.action == "PURGE":
            if (
                self.purge_owner_deletion_epoch is None
                or self.purge_owner_deletion_epoch <= self.owner_deletion_epoch
            ):
                raise ValueError("PURGE requires a newer owner deletion epoch")
        elif self.purge_owner_deletion_epoch is not None:
            raise ValueError("only PURGE permits a purge epoch")
        return self


class GateAuthorityResponse(GateAuthorityRequest):
    authority_ref: str


class GateScopeLookupRequest(_PrivateBindingModel):
    schema_version: Literal["w1.private.w2-gate-scope-lookup.v1"]
    operation_id: CanonicalWireUUID
    operation_revision: PositiveSigned64Int
    action: CommitGateAction
    phase: GatePhase
    result_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    purge_owner_deletion_epoch: PositiveSigned64Int | None = None

    @model_validator(mode="after")
    def validate_purge_epoch(self) -> Self:
        if self.action == "PURGE":
            if (
                self.purge_owner_deletion_epoch is None
                or self.purge_owner_deletion_epoch <= self.owner_deletion_epoch
            ):
                raise ValueError("PURGE requires a newer owner deletion epoch")
        elif "purge_owner_deletion_epoch" in self.model_fields_set:
            raise ValueError("only PURGE permits a purge epoch field")
        return self


class GateScopeLookupResponse(GateScopeLookupRequest):
    scope: PrivateScopeMapping

    _require_canonical_scope = field_validator("scope")(_canonical_scope)


def validate_private_echo(request: BaseModel, response: BaseModel) -> None:
    """Reject a successful response that changes any request binding field."""

    if not isinstance(request, BaseModel) or not isinstance(response, BaseModel):
        raise W1WireContractError("invalid W1 private authority echo")
    request_fields = type(request).model_fields
    if not set(request_fields).issubset(type(response).model_fields):
        raise W1WireContractError("invalid W1 private authority echo")
    for field_name in request_fields:
        if getattr(request, field_name) != getattr(response, field_name):
            raise W1WireContractError("W1 private authority echo mismatch")


def _require_uuid(value: object, *, label: str) -> None:
    if not isinstance(value, UUID):
        raise W1WireContractError(f"invalid W1 {label} binding")


def _require_integer(
    value: object, *, minimum: int, label: str, maximum: int = SIGNED_64_MAX
) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise W1WireContractError(f"invalid W1 {label} binding")


@dataclass(frozen=True, slots=True)
class W1PrivateBinding:
    owner_user_id: UUID
    owner_deletion_epoch: int
    command_id: UUID
    job_id: UUID
    execution_fence: int

    def __post_init__(self) -> None:
        _require_uuid(self.owner_user_id, label="owner")
        _require_integer(self.owner_deletion_epoch, minimum=0, label="owner epoch")
        _require_uuid(self.command_id, label="command")
        _require_uuid(self.job_id, label="Job")
        _require_integer(self.execution_fence, minimum=1, label="execution fence")

    @classmethod
    def from_collection(cls, command: CollectionCommand) -> Self:
        if not isinstance(command, CollectionCommand):
            raise W1WireContractError("invalid W2 collection binding")
        command = _revalidate_model(command, CollectionCommand, label="W2 collection command")
        fence_text = command.execution_fence
        if (
            len(fence_text) > len(str(SIGNED_64_MAX))
            or re.fullmatch(r"[1-9][0-9]*", fence_text) is None
        ):
            raise W1WireContractError("invalid W2 collection execution fence")
        try:
            execution_fence = int(fence_text)
        except ValueError:
            raise W1WireContractError("invalid W2 collection execution fence") from None
        if execution_fence > SIGNED_64_MAX:
            raise W1WireContractError("invalid W2 collection execution fence")
        return cls(
            owner_user_id=command.authenticated_owner_ref,
            owner_deletion_epoch=command.owner_deletion_epoch,
            command_id=command.command_id,
            job_id=command.job_id,
            execution_fence=execution_fence,
        )

    @classmethod
    def from_gate(cls, gate: CommitGateCommand) -> Self:
        if not isinstance(gate, CommitGateCommand):
            raise W1WireContractError("invalid W1 commit-gate binding")
        gate = _revalidate_model(gate, CommitGateCommand, label="W1 commit-gate command")
        return cls(
            owner_user_id=gate.authenticated_owner_ref,
            owner_deletion_epoch=gate.owner_deletion_epoch,
            command_id=gate.command_id,
            job_id=gate.job_id,
            execution_fence=gate.execution_fence,
        )

    @classmethod
    def from_ack(cls, ack: CommitGateAckProposal) -> Self:
        if not isinstance(ack, CommitGateAckProposal):
            raise W1WireContractError("invalid W2 commit-gate ACK binding")
        ack = _revalidate_model(ack, CommitGateAckProposal, label="W2 commit-gate ACK")
        return cls(
            owner_user_id=ack.authenticated_owner_ref,
            owner_deletion_epoch=ack.owner_deletion_epoch,
            command_id=ack.command_id,
            job_id=ack.job_id,
            execution_fence=ack.execution_fence,
        )


@dataclass(frozen=True, slots=True)
class W1GateBinding:
    private: W1PrivateBinding
    operation_id: UUID
    operation_revision: int
    action: CommitGateAction
    result_digest: str
    purge_owner_deletion_epoch: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.private, W1PrivateBinding):
            raise W1WireContractError("invalid W1 private gate binding")
        _require_uuid(self.operation_id, label="gate operation")
        _require_integer(self.operation_revision, minimum=1, label="gate revision")
        if self.action not in {"PREPARE", "FINALIZE", "ABORT", "PURGE"}:
            raise W1WireContractError("invalid W1 gate action binding")
        if (
            not isinstance(self.result_digest, str)
            or _RESULT_DIGEST.fullmatch(self.result_digest) is None
        ):
            raise W1WireContractError("invalid W1 gate digest binding")
        if self.action == "PURGE":
            _require_integer(self.purge_owner_deletion_epoch, minimum=1, label="purge epoch")
            assert self.purge_owner_deletion_epoch is not None
            if self.purge_owner_deletion_epoch <= self.private.owner_deletion_epoch:
                raise W1WireContractError("invalid W1 purge epoch binding")
        elif self.purge_owner_deletion_epoch is not None:
            raise W1WireContractError("invalid W1 non-PURGE epoch binding")

    @classmethod
    def from_gate(cls, gate: CommitGateCommand) -> Self:
        if not isinstance(gate, CommitGateCommand):
            raise W1WireContractError("invalid W1 commit-gate binding")
        gate = _revalidate_model(gate, CommitGateCommand, label="W1 commit-gate command")
        return cls(
            private=W1PrivateBinding(
                owner_user_id=gate.authenticated_owner_ref,
                owner_deletion_epoch=gate.owner_deletion_epoch,
                command_id=gate.command_id,
                job_id=gate.job_id,
                execution_fence=gate.execution_fence,
            ),
            operation_id=gate.operation_id,
            operation_revision=gate.operation_revision,
            action=gate.action,
            result_digest=gate.result_digest,
            purge_owner_deletion_epoch=gate.purge_owner_deletion_epoch,
        )

    @classmethod
    def from_ack(cls, ack: CommitGateAckProposal) -> Self:
        if not isinstance(ack, CommitGateAckProposal):
            raise W1WireContractError("invalid W2 commit-gate ACK binding")
        ack = _revalidate_model(ack, CommitGateAckProposal, label="W2 commit-gate ACK")
        return cls(
            private=W1PrivateBinding(
                owner_user_id=ack.authenticated_owner_ref,
                owner_deletion_epoch=ack.owner_deletion_epoch,
                command_id=ack.command_id,
                job_id=ack.job_id,
                execution_fence=ack.execution_fence,
            ),
            operation_id=ack.operation_id,
            operation_revision=ack.operation_revision,
            action=ack.action,
            result_digest=ack.result_digest,
            purge_owner_deletion_epoch=ack.purge_owner_deletion_epoch,
        )
