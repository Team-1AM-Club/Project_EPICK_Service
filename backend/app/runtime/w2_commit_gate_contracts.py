"""Pinned, payload-only W2 commit-gate proposal codec.

This module is intentionally limited to deterministic parsing and semantic
validation.  It has no SQS client, no database session and no success mapping;
the dedicated inbound worker owns those responsibilities in later phases.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from typing import Annotated, Literal, TypeAlias, TypeVar
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.runtime.workers import RuntimeContractError, validate_w2_commit_gate_wire_schema

_FENCE_PATTERN = re.compile(r"^[1-9][0-9]*$")
_DIGEST_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")
_STAGED_RESULT_MESSAGE_TYPE = "w2.private.staged-result.proposal.v1"
_ACK_MESSAGE_TYPE = "w2.private.commit-gate-ack.proposal.v1"

PositiveWireInt: TypeAlias = Annotated[int, Field(strict=True, ge=1)]
NonNegativeWireInt: TypeAlias = Annotated[int, Field(strict=True, ge=0)]
ResultDigest: TypeAlias = Annotated[str, Field(pattern=_DIGEST_PATTERN.pattern)]
_WireModel = TypeVar("_WireModel", bound="_PrivateProposal")


class W2CommitGateContractError(RuntimeContractError):
    """The pinned W2 proposal is malformed, unpinned or semantically unsafe."""


class _PrivateProposal(BaseModel):
    """Shared immutable private W2 proposal envelope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    message_id: UUID
    message_type: str
    producer: Literal["w2"]
    occurred_at: AwareDatetime
    visibility_scope: Literal["PRIVATE"]


class W2StagedResultProposal(_PrivateProposal):
    """A private result proposal, not a W1-visible result or queue success."""

    schema_version: Literal[_STAGED_RESULT_MESSAGE_TYPE]
    message_type: Literal[_STAGED_RESULT_MESSAGE_TYPE]
    command: dict[str, object]
    result: dict[str, object]
    result_digest: ResultDigest

    @model_validator(mode="after")
    def _validate_binding_and_digest(self) -> W2StagedResultProposal:
        _validate_staged_binding(self.command, self.result)
        if self.result_digest != staged_result_digest(self.command, self.result):
            raise ValueError("W2 staged-result digest mismatch")
        return self


class W2CommitGateAckProposal(_PrivateProposal):
    """A private ACK proposal whose outcome is retained rather than normalized."""

    schema_version: Literal[_ACK_MESSAGE_TYPE]
    message_type: Literal[_ACK_MESSAGE_TYPE]
    operation_id: UUID
    operation_revision: PositiveWireInt
    action: Literal["PREPARE", "FINALIZE", "ABORT", "PURGE"]
    command_id: UUID
    job_id: UUID
    authenticated_owner_ref: UUID
    execution_fence: PositiveWireInt
    owner_deletion_epoch: NonNegativeWireInt
    purge_owner_deletion_epoch: PositiveWireInt | None = None
    result_digest: ResultDigest
    outcome: Literal["APPLIED", "DUPLICATE", "REJECTED"]

    @model_validator(mode="after")
    def _validate_purge_epoch(self) -> W2CommitGateAckProposal:
        if self.action != "PURGE" and "purge_owner_deletion_epoch" in self.model_fields_set:
            raise ValueError("only PURGE permits a purge epoch field")
        if self.action == "PURGE" and (
            self.purge_owner_deletion_epoch is None
            or self.purge_owner_deletion_epoch <= self.owner_deletion_epoch
        ):
            raise ValueError("PURGE requires a newer owner deletion epoch")
        return self


W2CommitGateProposal: TypeAlias = W2StagedResultProposal | W2CommitGateAckProposal


def _reject_json_constant(constant: str) -> None:
    raise W2CommitGateContractError(f"JSON non-finite number is not permitted: {constant}")


def _reject_duplicate_object_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise W2CommitGateContractError("duplicate JSON object key")
        value[key] = item
    return value


def _json_object(payload: object) -> dict[str, object]:
    if isinstance(payload, (str, bytes, bytearray)):
        try:
            decoded = json.loads(
                payload,
                object_pairs_hook=_reject_duplicate_object_keys,
                parse_constant=_reject_json_constant,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            if isinstance(error, W2CommitGateContractError):
                raise
            raise W2CommitGateContractError("invalid W2 commit-gate JSON") from error
    elif isinstance(payload, Mapping):
        decoded = dict(payload)
    else:
        raise W2CommitGateContractError("W2 commit-gate proposal must be a JSON object")
    if not isinstance(decoded, dict):
        raise W2CommitGateContractError("W2 commit-gate proposal must be a JSON object")
    _require_finite_json_value(decoded)
    return decoded


def _require_finite_json_value(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise W2CommitGateContractError("non-finite JSON number is not permitted")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise W2CommitGateContractError("JSON object keys must be strings")
            _require_finite_json_value(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _require_finite_json_value(item)


def canonical_json_digest(value: object) -> str:
    """Return the exact W2 SHA-256 canonical JSON digest.

    Key order is sorted, arrays retain order, Unicode is emitted as UTF-8 without
    normalization, and NaN/Infinity are rejected rather than serialized.
    """

    _require_finite_json_value(value)
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise W2CommitGateContractError("invalid W2 canonical JSON digest input") from error
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def staged_result_digest(command: object, result: object) -> str:
    """Hash the revalidated W2 command/result pair and no delivery metadata."""

    _validate_staged_binding(command, result)
    return canonical_json_digest({"command": command, "result": result})


def parse_w2_staged_result(payload: object) -> W2StagedResultProposal:
    return _parse_proposal(
        _json_object(payload),
        expected_message_type=_STAGED_RESULT_MESSAGE_TYPE,
        model_type=W2StagedResultProposal,
    )


def parse_w2_commit_gate_ack(payload: object) -> W2CommitGateAckProposal:
    return _parse_proposal(
        _json_object(payload),
        expected_message_type=_ACK_MESSAGE_TYPE,
        model_type=W2CommitGateAckProposal,
    )


def parse_w2_commit_gate_proposal(payload: object) -> W2CommitGateProposal:
    """Dispatch only the two pinned W2 proposal message types."""

    value = _json_object(payload)
    message_type = value.get("message_type")
    if message_type == _STAGED_RESULT_MESSAGE_TYPE:
        return _parse_proposal(
            value,
            expected_message_type=_STAGED_RESULT_MESSAGE_TYPE,
            model_type=W2StagedResultProposal,
        )
    if message_type == _ACK_MESSAGE_TYPE:
        return _parse_proposal(
            value,
            expected_message_type=_ACK_MESSAGE_TYPE,
            model_type=W2CommitGateAckProposal,
        )
    raise W2CommitGateContractError("W2 commit-gate message type is unsupported")


def _parse_proposal(
    value: dict[str, object], *, expected_message_type: str, model_type: type[_WireModel]
) -> _WireModel:
    if value.get("message_type") != expected_message_type:
        raise W2CommitGateContractError("W2 commit-gate message type does not match parser")
    try:
        validate_w2_commit_gate_wire_schema(value, message_type=expected_message_type)
        return model_type.model_validate(value)
    except (RuntimeContractError, ValidationError, ValueError) as error:
        if isinstance(error, W2CommitGateContractError):
            raise
        raise W2CommitGateContractError("W2 commit-gate proposal is invalid") from error


def _validate_staged_binding(command: object, result: object) -> None:
    if not isinstance(command, Mapping) or not isinstance(result, Mapping):
        raise W2CommitGateContractError("W2 staged-result command and result must be objects")
    if any(
        command.get(field) != result.get(field)
        for field in ("command_id", "job_id", "source_id", "input_version")
    ):
        raise W2CommitGateContractError("W2 staged-result immutable binding mismatch")
    execution_fence = command.get("execution_fence")
    if not isinstance(execution_fence, str) or _FENCE_PATTERN.fullmatch(execution_fence) is None:
        raise W2CommitGateContractError("W2 staged-result execution fence is invalid")
    _require_finite_json_value(command)
    _require_finite_json_value(result)
