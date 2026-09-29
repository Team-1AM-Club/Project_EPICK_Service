"""Standalone codecs for the revision-pinned W1 private wire contract.

These codecs perform no HTTP, queue, execution, persistence, or deduplication work.
Lookup currentness is not a worker permit, a slot grant, or commit authorization.
Core and direct-registration pins are preserved and cross-bound to their commands.
Wire binding does not replace W1's database, Job type, or execution authority checks.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from epick_engine.source_collection.contracts import (
    CollectionCommand,
    CollectionResult,
    ContractModel,
)

type PositiveWireInt = Annotated[int, Field(strict=True, ge=1)]
type NonNegativeWireInt = Annotated[int, Field(strict=True, ge=0)]


class W1WireContractError(ValueError):
    """Malformed or mismatched wire data; diagnostics never include the payload."""


class W1LookupError(RuntimeError):
    """Validated protected lookup failure, without credentials or private payload."""

    def __init__(
        self,
        *,
        http_status: int,
        code: str,
        retryable: bool,
        correlation_id: UUID,
    ) -> None:
        super().__init__(f"W1 protected lookup failed: {code}")
        self.http_status = http_status
        self.code = code
        self.retryable = retryable
        self.correlation_id = correlation_id


class LookupRequest(ContractModel):
    """Read-only lookup identity; its fence is a positive JSON integer."""

    schema_version: Literal["w1.private.command-lookup.v1"]
    command_id: UUID
    execution_fence: PositiveWireInt
    owner_deletion_epoch: NonNegativeWireInt


class CoreDecisionPin(ContractModel):
    """Immutable W1 wire pin, not a W2 decision or execution authorization."""

    origin_message_id: UUID
    decision_id: UUID
    decision_scope: Literal["COMPANY_KNOWLEDGE", "QUESTION_MATCHING"]
    company_id: UUID | None
    question_version_id: UUID | None
    source_id: UUID
    analysis_input_version: Annotated[str, Field(min_length=1, max_length=64)]
    decision_version: PositiveWireInt
    is_core: bool
    decision_code: Literal["CORE_REQUIRED", "NON_CORE_OPTIONAL"]
    reason_code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")]

    @model_validator(mode="after")
    def validate_pin_shape(self) -> CoreDecisionPin:
        if self.decision_scope == "COMPANY_KNOWLEDGE":
            if self.company_id is None or self.question_version_id is not None:
                raise ValueError("company-scoped pin requires only company_id")
        elif self.company_id is not None or self.question_version_id is None:
            raise ValueError("question-scoped pin requires only question_version_id")
        if self.is_core != (self.decision_code == "CORE_REQUIRED"):
            raise ValueError("pin core value must match its decision code")
        return self


class DirectSourceRegistrationPin(ContractModel):
    """W1-only non-core provenance; the opaque input is not a numeric revision."""

    registration_decision_id: UUID
    decision_scope: Literal["DIRECT_SOURCE_REGISTRATION"]
    company_id: UUID
    question_version_id: None
    source_id: UUID
    registration_input_version: Annotated[str, Field(min_length=1, max_length=64)]
    decision_version: PositiveWireInt
    is_core: bool
    decision_code: Literal["NON_CORE_OPTIONAL"]
    decision_owner: Literal["W1"]
    reason_code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")]
    purpose: Literal["DIRECT_SOURCE_REGISTRATION"]

    @model_validator(mode="after")
    def validate_non_core_registration(self) -> DirectSourceRegistrationPin:
        if self.is_core:
            raise ValueError("direct registration pin must be non-core")
        return self


def _require_matching_identity(command: CollectionCommand, request: LookupRequest) -> None:
    if (
        command.command_id != request.command_id
        or command.execution_fence != str(request.execution_fence)
        or command.owner_deletion_epoch != request.owner_deletion_epoch
    ):
        raise ValueError("command does not match lookup identity")


def _require_matching_decision(
    command: CollectionCommand,
    *,
    decision_scope: Literal["COMPANY_KNOWLEDGE", "QUESTION_MATCHING", "DIRECT_SOURCE_REGISTRATION"],
    company_id: UUID | None,
    source_id: UUID,
    decision_version: int,
    decision_owner: str,
    is_core: bool,
    reason_code: str,
) -> None:
    decision = command.core_source_decision
    if (
        (decision_scope != "QUESTION_MATCHING" and command.company_id != company_id)
        or command.source_id != source_id
        or command.input_version != decision_version
        or decision.decided_by != decision_owner
        or decision.is_core != is_core
        or decision.rationale != reason_code
        or decision.decision_revision != decision_version
        or decision.analysis_input_version != decision_version
    ):
        raise ValueError("command does not match decision pin")


class W1CommandDispatch(ContractModel):
    """W1-issued dispatch data only; does not implement W1WorkerControl."""

    schema_version: Literal["w1.private.w2-command-dispatch.v1"]
    message_id: UUID
    message_type: Literal["w1.private.w2.collection-command.v1"]
    producer: Literal["w1"]
    occurred_at: AwareDatetime
    visibility_scope: Literal["PRIVATE"]
    payload_schema_version: Literal["w2.collection.v1"]
    payload: CollectionCommand
    lookup_request: LookupRequest
    core_decision_pin: CoreDecisionPin

    @model_validator(mode="after")
    def validate_dispatch_identity(self) -> W1CommandDispatch:
        if self.message_id != self.payload.command_id:
            raise ValueError("dispatch message_id must match command_id")
        _require_matching_identity(self.payload, self.lookup_request)
        pin = self.core_decision_pin
        if not pin.is_core or pin.decision_code != "CORE_REQUIRED":
            raise ValueError("Core dispatch requires a Core decision")
        _require_matching_decision(
            self.payload,
            decision_scope=pin.decision_scope,
            company_id=pin.company_id,
            source_id=pin.source_id,
            decision_version=pin.decision_version,
            decision_owner="W3" if pin.decision_scope == "COMPANY_KNOWLEDGE" else "W4",
            is_core=True,
            reason_code=pin.reason_code,
        )
        return self


class W1DirectSourceRegistrationDispatch(ContractModel):
    """Dedicated W1 registration wrapper; never reinterpreted as a Core dispatch."""

    schema_version: Literal["w1.private.w2-direct-source-registration-dispatch.v1"]
    message_id: UUID
    message_type: Literal["w1.private.w2.direct-source-registration.v1"]
    producer: Literal["w1"]
    occurred_at: AwareDatetime
    visibility_scope: Literal["PRIVATE"]
    payload_schema_version: Literal["w2.collection.v1"]
    payload: CollectionCommand
    lookup_request: LookupRequest
    direct_source_registration_pin: DirectSourceRegistrationPin

    @model_validator(mode="after")
    def validate_dispatch_identity(self) -> W1DirectSourceRegistrationDispatch:
        if self.message_id != self.payload.command_id:
            raise ValueError("dispatch message_id must match command_id")
        _require_matching_identity(self.payload, self.lookup_request)
        pin = self.direct_source_registration_pin
        _require_matching_decision(
            self.payload,
            decision_scope=pin.decision_scope,
            company_id=pin.company_id,
            source_id=pin.source_id,
            decision_version=pin.decision_version,
            decision_owner=pin.decision_owner,
            is_core=False,
            reason_code=pin.reason_code,
        )
        return self


type W1Dispatch = W1CommandDispatch | W1DirectSourceRegistrationDispatch


class LookupResponse(ContractModel):
    """A non-AVAILABLE response never exposes a command to execute or resume."""

    schema_version: Literal["w1.private.command-lookup.v1"]
    command_id: UUID
    status: Literal[
        "AVAILABLE",
        "NOT_FOUND",
        "STALE_FENCE",
        "STALE_DELETION_EPOCH",
        "DELETED",
        "INVALIDATED",
        "EXPIRED",
    ]
    reason_code: Annotated[str, Field(min_length=1, max_length=64)] | None
    command: CollectionCommand | None

    @model_validator(mode="after")
    def validate_status_shape(self) -> LookupResponse:
        if self.status == "AVAILABLE":
            if self.command is None or self.reason_code is not None:
                raise ValueError("AVAILABLE requires a command and no reason")
        elif self.command is not None or self.reason_code is None:
            raise ValueError("unavailable lookup requires a reason and no command")
        return self


class _ProtectedLookupError(ContractModel):
    schema_version: Literal["w1.private.error.v1"]
    code: Literal[
        "UNAUTHENTICATED_SERVICE_PRINCIPAL",
        "FORBIDDEN_SERVICE_PRINCIPAL",
        "INVALID_LOOKUP_REQUEST",
        "INTERNAL_RETRYABLE",
    ]
    retryable: bool
    correlation_id: UUID


class PrivateCollectionResultEnvelope(ContractModel):
    """Private result record built with caller-persisted delivery identity."""

    schema_version: Literal["w1.private.v1"]
    message_id: UUID
    message_type: Literal["w2.collection.result.v1"]
    producer: Literal["w2"]
    occurred_at: AwareDatetime
    visibility_scope: Literal["PRIVATE"]
    channel: Literal["w1.private.w2.collection-result.v1"]
    payload: CollectionResult


_WIRE_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
_WIRE_DATETIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:[Zz]|[+-][0-9]{2}:[0-9]{2})"
)


def _require_wire_formats(raw: object, parsed: object) -> None:
    """Check lexical formats before normalized UUIDs or datetimes can hide invalid wire text.

    Pydantic has already checked semantic validity. Typed Python UUID/datetime values
    remain supported; their datetime ISO rendering must also be RFC3339-compatible.
    """

    if isinstance(parsed, UUID):
        if not isinstance(raw, UUID) and (
            not isinstance(raw, str) or _WIRE_UUID.fullmatch(raw) is None
        ):
            raise ValueError("wire UUID requires hyphenated hexadecimal syntax")
    elif isinstance(parsed, datetime):
        text = raw.isoformat() if isinstance(raw, datetime) else raw
        if not isinstance(text, str) or _WIRE_DATETIME.fullmatch(text) is None:
            raise ValueError("wire datetime requires RFC3339 syntax")
    elif isinstance(parsed, BaseModel) and isinstance(raw, dict):
        for name in type(parsed).model_fields:
            if name in raw:
                _require_wire_formats(raw[name], getattr(parsed, name))
    elif isinstance(parsed, list | tuple) and isinstance(raw, list | tuple):
        for raw_item, parsed_item in zip(raw, parsed, strict=True):
            _require_wire_formats(raw_item, parsed_item)


def _parse_wire[M: BaseModel](payload: object, model: type[M], *, label: str) -> M:
    """Validate JSON strictly while retaining JSON UUID, enum, and datetime syntax."""

    if not isinstance(payload, dict):
        raise W1WireContractError(f"invalid {label}")
    try:
        parsed = model.model_validate_json(json.dumps(payload, allow_nan=False), strict=True)
        _require_wire_formats(payload, parsed)
        return parsed
    except (TypeError, ValueError):
        raise W1WireContractError(f"invalid {label}") from None


def _revalidate_model[M: BaseModel](value: M, model: type[M], *, label: str) -> M:
    """Validate Python values before JSON serializers can turn a copied bool into an int."""

    try:
        raw = value.model_dump(mode="python", warnings="error")
        parsed = model.model_validate(raw, strict=True)
        _require_wire_formats(raw, parsed)
        return parsed
    except (TypeError, ValueError):
        raise W1WireContractError(f"invalid {label}") from None


def parse_lookup_request(payload: object) -> LookupRequest:
    """Decode the embedded lookup request without coercing its fence or epoch."""

    return _parse_wire(payload, LookupRequest, label="W1 lookup request")


def parse_w1_dispatch(payload: object) -> W1Dispatch:
    """Decode the explicit Core/direct wrapper without coercing or repairing either pin."""

    if (
        isinstance(payload, dict)
        and payload.get("message_type") == "w1.private.w2.direct-source-registration.v1"
    ):
        return _parse_wire(
            payload, W1DirectSourceRegistrationDispatch, label="W1 direct registration dispatch"
        )
    return _parse_wire(payload, W1CommandDispatch, label="W1 command dispatch")


def validate_dispatch_lookup(dispatch: W1Dispatch, response: LookupResponse) -> None:
    """Require the complete AVAILABLE command to equal the dispatch command.

    A matching unavailable response remains unavailable. This check grants no slot,
    lease, commit permission, or database-backed decision/Job authorization.
    """

    if not isinstance(dispatch, W1CommandDispatch | W1DirectSourceRegistrationDispatch) or not (
        isinstance(response, LookupResponse)
    ):
        raise W1WireContractError("invalid W1 dispatch lookup context")
    if isinstance(dispatch, W1CommandDispatch):
        dispatch = _revalidate_model(dispatch, W1CommandDispatch, label="W1 command dispatch")
    else:
        dispatch = _revalidate_model(
            dispatch, W1DirectSourceRegistrationDispatch, label="W1 direct registration dispatch"
        )
    response = _revalidate_model(response, LookupResponse, label="W1 lookup response")
    if response.command_id != dispatch.payload.command_id or (
        response.status == "AVAILABLE" and response.command != dispatch.payload
    ):
        raise W1WireContractError("W1 dispatch lookup command mismatch")


_LOOKUP_HTTP_ERRORS = {
    401: "UNAUTHENTICATED_SERVICE_PRINCIPAL",
    403: "FORBIDDEN_SERVICE_PRINCIPAL",
    422: "INVALID_LOOKUP_REQUEST",
    503: "INTERNAL_RETRYABLE",
}


def decode_lookup_response(
    payload: object,
    *,
    http_status: int,
    request: LookupRequest,
) -> LookupResponse:
    """Bind a decoded HTTP body to its original request; never retry or authorize work.

    Only a schema-valid HTTP 503/INTERNAL_RETRYABLE error is marked retryable.
    A semantic non-AVAILABLE HTTP 200 remains a response with no command.
    """

    if type(http_status) is not int or not isinstance(request, LookupRequest):
        raise W1WireContractError("invalid W1 lookup response context")
    request = _revalidate_model(request, LookupRequest, label="W1 lookup request")
    if http_status == 200:
        response = _parse_wire(payload, LookupResponse, label="W1 lookup response")
        if response.command_id != request.command_id:
            raise W1WireContractError("W1 lookup response identity mismatch")
        if response.command is not None:
            try:
                _require_matching_identity(response.command, request)
            except ValueError:
                raise W1WireContractError("W1 lookup response identity mismatch") from None
        return response

    expected_code = _LOOKUP_HTTP_ERRORS.get(http_status)
    if expected_code is None:
        raise W1WireContractError("unsupported W1 lookup HTTP status")
    error = _parse_wire(payload, _ProtectedLookupError, label="W1 protected lookup error")
    if error.code != expected_code or error.retryable != (http_status == 503):
        raise W1WireContractError("W1 protected lookup error status mismatch")
    raise W1LookupError(
        http_status=http_status,
        code=error.code,
        retryable=error.retryable,
        correlation_id=error.correlation_id,
    )


def build_private_result_envelope(
    result: CollectionResult,
    *,
    message_id: UUID,
    occurred_at: datetime,
) -> PrivateCollectionResultEnvelope:
    """Encode a validated private result using persisted identity, including on replay.

    The caller owns persistence of message_id/occurred_at and reuse on retries.
    This function does not generate IDs, timestamps, deduplication state, or publish data.
    """

    if (
        not isinstance(result, CollectionResult)
        or not isinstance(message_id, UUID)
        or not isinstance(occurred_at, datetime)
        or occurred_at.tzinfo is None
        or occurred_at.utcoffset() is None
    ):
        raise W1WireContractError("invalid persisted W1 result envelope context")
    result = _revalidate_model(result, CollectionResult, label="W2 collection result")
    payload = result.model_dump(mode="json", warnings="error")
    return _parse_wire(
        {
            "schema_version": "w1.private.v1",
            "message_id": str(message_id),
            "message_type": "w2.collection.result.v1",
            "producer": "w2",
            "occurred_at": occurred_at.isoformat(),
            "visibility_scope": "PRIVATE",
            "channel": "w1.private.w2.collection-result.v1",
            "payload": payload,
        },
        PrivateCollectionResultEnvelope,
        label="W1 private collection result envelope",
    )
