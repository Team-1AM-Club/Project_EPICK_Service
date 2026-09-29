"""W1-adopted private gate codec; proposal wire names remain compatibility pins."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from epick_engine.source_collection.contracts import (
    CollectionCommand,
    CollectionResult,
    ContractModel,
)
from epick_engine.source_collection.w1_transport import (
    NonNegativeWireInt,
    PositiveWireInt,
    W1WireContractError,
    _parse_wire,
    _revalidate_model,
)

type CommitGateAction = Literal["PREPARE", "FINALIZE", "ABORT", "PURGE"]
type CommitGateOutcome = Literal["APPLIED", "DUPLICATE", "REJECTED"]
type ResultDigest = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]


class _GateBinding(ContractModel):
    operation_id: UUID
    operation_revision: PositiveWireInt
    action: CommitGateAction
    command_id: UUID
    job_id: UUID
    authenticated_owner_ref: UUID
    execution_fence: PositiveWireInt
    owner_deletion_epoch: NonNegativeWireInt
    purge_owner_deletion_epoch: PositiveWireInt | None = None
    result_digest: ResultDigest

    @model_validator(mode="after")
    def validate_newer_purge_epoch(self) -> _GateBinding:
        if self.action != "PURGE" and "purge_owner_deletion_epoch" in self.model_fields_set:
            raise ValueError("only PURGE permits a purge epoch field")
        if self.action == "PURGE" and (
            self.purge_owner_deletion_epoch is None
            or self.purge_owner_deletion_epoch <= self.owner_deletion_epoch
        ):
            raise ValueError("PURGE requires a newer deletion epoch")
        return self

    @model_serializer(mode="wrap")
    def omit_absent_purge_epoch(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        raw: dict[str, object] = handler(self)
        if (
            self.purge_owner_deletion_epoch is None
            and "purge_owner_deletion_epoch" not in self.model_fields_set
        ):
            raw.pop("purge_owner_deletion_epoch", None)
        return raw


class CommitGateCommand(_GateBinding):
    """W1-issued private binding; parsing grants no execution or commit permission."""

    schema_version: Literal["w1.private.w2.commit-gate.v1"]
    message_id: UUID
    message_type: Literal["w1.private.w2.commit-gate.v1"]
    producer: Literal["w1"]
    visibility_scope: Literal["PRIVATE"]
    issued_at: AwareDatetime


def parse_commit_gate_command(payload: object) -> CommitGateCommand:
    """Validate syntax and PURGE semantics; store checks immutable binding/revision."""

    return _parse_wire(payload, CommitGateCommand, label="W1 commit-gate command")


def _validated_pair(
    command: CollectionCommand, result: CollectionResult
) -> tuple[CollectionCommand, CollectionResult]:
    if not isinstance(command, CollectionCommand) or not isinstance(result, CollectionResult):
        raise W1WireContractError("invalid W2 staged-result context")
    command = _revalidate_model(command, CollectionCommand, label="W2 staged command")
    result = _revalidate_model(result, CollectionResult, label="W2 staged result")
    if (
        command.command_id != result.command_id
        or command.job_id != result.job_id
        or command.source_id != result.source_id
        or command.input_version != result.input_version
        or re.fullmatch(r"[1-9][0-9]*", command.execution_fence) is None
    ):
        raise W1WireContractError("W2 staged-result binding mismatch")
    return command, result


def _digest(command: CollectionCommand, result: CollectionResult) -> str:
    raw = {"command": command.model_dump(mode="json"), "result": result.model_dump(mode="json")}
    encoded = json.dumps(
        raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def staged_result_digest(command: CollectionCommand, result: CollectionResult) -> str:
    """Hash only revalidated, bound command/result JSON under the W1-adopted profile.

    Arrays retain order and Unicode is not normalized. Delivery IDs/timestamps,
    operation ID and lease ID are not hash inputs; none is invented here.
    """

    command, result = _validated_pair(command, result)
    try:
        return _digest(command, result)
    except (TypeError, ValueError):
        raise W1WireContractError("invalid W2 staged-result digest input") from None


class StagedResultProposal(ContractModel):
    """Adopted private submission, retaining the original proposal wire version."""

    schema_version: Literal["w2.private.staged-result.proposal.v1"]
    message_id: UUID
    message_type: Literal["w2.private.staged-result.proposal.v1"]
    producer: Literal["w2"]
    occurred_at: AwareDatetime
    visibility_scope: Literal["PRIVATE"]
    command: CollectionCommand
    result: CollectionResult
    result_digest: ResultDigest

    @model_validator(mode="after")
    def validate_result_binding_and_digest(self) -> StagedResultProposal:
        command, result = _validated_pair(self.command, self.result)
        if self.result_digest != _digest(command, result):
            raise ValueError("staged-result digest mismatch")
        return self


class CommitGateAckProposal(_GateBinding):
    """Adopted explicit outcome; never converted to a W1 normalized success ACK."""

    schema_version: Literal["w2.private.commit-gate-ack.proposal.v1"]
    message_id: UUID
    message_type: Literal["w2.private.commit-gate-ack.proposal.v1"]
    producer: Literal["w2"]
    occurred_at: AwareDatetime
    visibility_scope: Literal["PRIVATE"]
    outcome: CommitGateOutcome


def _delivery_identity(message_id: UUID, occurred_at: datetime) -> dict[str, str]:
    if (
        not isinstance(message_id, UUID)
        or not isinstance(occurred_at, datetime)
        or occurred_at.tzinfo is None
        or occurred_at.utcoffset() is None
    ):
        raise W1WireContractError("invalid persisted W2 proposal delivery identity")
    return {"message_id": str(message_id), "occurred_at": occurred_at.isoformat()}


def build_staged_result(
    command: CollectionCommand,
    result: CollectionResult,
    *,
    message_id: UUID,
    occurred_at: datetime,
) -> StagedResultProposal:
    """Build a proposal using caller-persisted identity, without generating a lease."""

    delivery = _delivery_identity(message_id, occurred_at)
    command, result = _validated_pair(command, result)
    return _parse_wire(
        {
            "schema_version": "w2.private.staged-result.proposal.v1",
            "message_type": "w2.private.staged-result.proposal.v1",
            "producer": "w2",
            "visibility_scope": "PRIVATE",
            **delivery,
            "command": command.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
            "result_digest": staged_result_digest(command, result),
        },
        StagedResultProposal,
        label="W2 staged-result proposal",
    )


def build_commit_gate_ack(
    command: CommitGateCommand,
    *,
    outcome: CommitGateOutcome,
    message_id: UUID,
    occurred_at: datetime,
) -> CommitGateAckProposal:
    """Preserve the W1 binding and explicit outcome; caller owns delivery persistence."""

    delivery = _delivery_identity(message_id, occurred_at)
    if not isinstance(command, CommitGateCommand):
        raise W1WireContractError("invalid W1 commit-gate ACK context")
    if command.action != "PURGE" and "purge_owner_deletion_epoch" in command.model_fields_set:
        raise W1WireContractError("invalid W1 commit-gate ACK context")
    command = _revalidate_model(command, CommitGateCommand, label="W1 commit-gate command")
    raw = command.model_dump(mode="json")
    raw.pop("issued_at")
    raw.update(
        schema_version="w2.private.commit-gate-ack.proposal.v1",
        message_type="w2.private.commit-gate-ack.proposal.v1",
        producer="w2",
        outcome=outcome,
        **delivery,
    )
    return _parse_wire(raw, CommitGateAckProposal, label="W2 commit-gate ACK proposal")
