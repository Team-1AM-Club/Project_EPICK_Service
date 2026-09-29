from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

_SCOPE_OWNERS = {
    "COMPANY_KNOWLEDGE": "W3",
    "QUESTION_MATCHING": "W4",
}

W3_CORE_DECISION_SCHEMA_VERSION = "w3.private.core-decision/0.1-candidate"
W3_CORE_DECISION_MESSAGE_TYPE = "w3.private.w1.core-decision"
W3_CORE_DECISION_PRODUCER = "w3"
W3_CORE_DECISION_CONSUMER = "w1.core-source-decision"
W3_CORE_DECISION_MAX_BODY_BYTES = 16 * 1024


class CoreDecisionContractError(ValueError):
    """The external W3 event is not a valid Core Decision wire object."""

    def __init__(self, code: str = "CORE_DECISION_SCHEMA_INVALID") -> None:
        super().__init__(code)
        self.code = code


class CoreDecisionReceiptOutcome(str, Enum):
    APPLIED = "APPLIED"
    DUPLICATE = "DUPLICATE"
    STALE_DISCARDED = "STALE_DISCARDED"
    REJECTED_SCHEMA = "REJECTED_SCHEMA"
    REJECTED_PRINCIPAL = "REJECTED_PRINCIPAL"
    REJECTED_BINDING = "REJECTED_BINDING"
    REJECTED_CONFLICT = "REJECTED_CONFLICT"
    RETRYABLE_INFRA_FAILURE = "RETRYABLE_INFRA_FAILURE"


@dataclass(frozen=True, slots=True)
class W3CoreDecisionEvent:
    schema_version: str
    message_type: str
    message_id: UUID
    occurred_at: str
    visibility_scope: str
    producer: str
    job_id: UUID
    company_id: UUID
    source_id: UUID
    analysis_input_version: str
    decision_scope: str
    decision_owner: str
    question_version_id: None
    decision_version: int
    is_core: bool
    decision_code: str
    reason_code: str
    payload_digest: str


@dataclass(frozen=True, slots=True)
class CoreDecisionReceipt:
    message_id: UUID
    payload_digest: str
    outcome: CoreDecisionReceiptOutcome
    retryable: bool
    error_code: str | None
    decision_id: UUID | None
    received_at: datetime


@lru_cache(maxsize=1)
def _w3_core_decision_validator() -> Draft202012Validator:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "w3"
        / "v1"
        / "core-decision.event.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _reject_nonfinite_constant(_: str) -> object:
    raise CoreDecisionContractError("CORE_DECISION_NONFINITE_NUMBER")


def canonical_core_decision_json(payload: Mapping[str, object]) -> bytes:
    """Return the cross-runtime canonical JSON bytes used for replay conflict checks."""

    try:
        return json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise CoreDecisionContractError() from error


def core_decision_payload_digest(payload: Mapping[str, object]) -> str:
    canonical = canonical_core_decision_json(payload)
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def parse_w3_core_decision_event(
    body: bytes | str | Mapping[str, object],
) -> W3CoreDecisionEvent:
    try:
        if isinstance(body, bytes):
            if len(body) > W3_CORE_DECISION_MAX_BODY_BYTES:
                raise CoreDecisionContractError("CORE_DECISION_BODY_TOO_LARGE")
            payload = json.loads(
                body.decode("utf-8"),
                parse_constant=_reject_nonfinite_constant,
            )
        elif isinstance(body, str):
            if len(body.encode("utf-8")) > W3_CORE_DECISION_MAX_BODY_BYTES:
                raise CoreDecisionContractError("CORE_DECISION_BODY_TOO_LARGE")
            payload = json.loads(body, parse_constant=_reject_nonfinite_constant)
        elif isinstance(body, Mapping):
            payload = dict(body)
        else:
            raise CoreDecisionContractError()
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CoreDecisionContractError() from error

    if not isinstance(payload, dict):
        raise CoreDecisionContractError()
    try:
        _w3_core_decision_validator().validate(payload)
        if len(canonical_core_decision_json(payload)) > W3_CORE_DECISION_MAX_BODY_BYTES:
            raise CoreDecisionContractError("CORE_DECISION_BODY_TOO_LARGE")
        digest = core_decision_payload_digest(payload)
        return W3CoreDecisionEvent(
            schema_version=payload["schema_version"],
            message_type=payload["message_type"],
            message_id=UUID(payload["message_id"]),
            occurred_at=payload["occurred_at"],
            visibility_scope=payload["visibility_scope"],
            producer=payload["producer"],
            job_id=UUID(payload["job_id"]),
            company_id=UUID(payload["company_id"]),
            source_id=UUID(payload["source_id"]),
            analysis_input_version=payload["analysis_input_version"],
            decision_scope=payload["decision_scope"],
            decision_owner=payload["decision_owner"],
            question_version_id=None,
            decision_version=payload["decision_version"],
            is_core=payload["is_core"],
            decision_code=payload["decision_code"],
            reason_code=payload["reason_code"],
            payload_digest=digest,
        )
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        if isinstance(error, CoreDecisionContractError):
            raise
        raise CoreDecisionContractError() from error


def project_core_decision_pin(*, binding: Any, decision: Any) -> dict[str, object]:
    """Project an accepted immutable Core binding into a later execution command."""

    if binding.decision_code != "CORE_REQUIRED" or decision.decision_code != "CORE_REQUIRED":
        raise CoreDecisionBindingError()
    return {
        "origin_message_id": str(binding.origin_message_id),
        "decision_id": str(decision.id),
        "decision_scope": decision.decision_scope,
        "company_id": str(decision.company_id) if decision.company_id is not None else None,
        "question_version_id": (
            str(decision.question_version_id)
            if decision.question_version_id is not None
            else None
        ),
        "source_id": str(binding.source_id),
        "analysis_input_version": binding.analysis_input_version,
        "decision_version": binding.decision_version,
        "is_core": True,
        "decision_code": decision.decision_code,
        "reason_code": decision.reason_code,
    }


class CoreDecisionBindingError(ValueError):
    """A stored Core Decision, its pin, and W2 convenience payload diverged."""

    def __init__(self, code: str = "CORE_DECISION_BINDING_MISMATCH") -> None:
        super().__init__(code)
        self.code = code


def owner_for_core_scope(scope: object) -> str:
    if not isinstance(scope, str) or scope not in _SCOPE_OWNERS:
        raise CoreDecisionBindingError()
    return _SCOPE_OWNERS[scope]


def validate_core_pin_payload_binding(
    *, pin: Mapping[str, object], w2_command: Mapping[str, object]
) -> None:
    """Validate only values retained on the private W2 command and its Core pin.

    This pure validator deliberately does not read the database so the protected lookup adapter can
    fail closed with its column-limited role.  Worker and relay add DB validation below.
    """

    try:
        scope = pin["decision_scope"]
        expected_owner = owner_for_core_scope(scope)
        company_id = pin["company_id"]
        question_version_id = pin["question_version_id"]
        source_id = pin["source_id"]
        decision_version = pin["decision_version"]
        reason_code = pin["reason_code"]
        analysis_input_version = pin["analysis_input_version"]

        if scope == "COMPANY_KNOWLEDGE":
            if not isinstance(company_id, str) or not company_id or question_version_id is not None:
                raise CoreDecisionBindingError()
        elif scope == "QUESTION_MATCHING":
            if (
                company_id is not None
                or not isinstance(question_version_id, str)
                or not question_version_id
            ):
                raise CoreDecisionBindingError()
        if (
            not isinstance(source_id, str)
            or not source_id
            or not isinstance(analysis_input_version, str)
            or not analysis_input_version
            or isinstance(decision_version, bool)
            or not isinstance(decision_version, int)
            or decision_version < 1
            or pin["is_core"] is not True
            or pin["decision_code"] != "CORE_REQUIRED"
            or not isinstance(reason_code, str)
            or not reason_code
        ):
            raise CoreDecisionBindingError()

        core = w2_command["core_source_decision"]
        if not isinstance(core, Mapping):
            raise CoreDecisionBindingError()
        if (
            w2_command["source_id"] != source_id
            or w2_command["company_id"] != company_id
            or w2_command["input_version"] != decision_version
            or core.get("is_core") is not True
            or core.get("decided_by") != expected_owner
            or core.get("rationale") != reason_code
            or core.get("decision_revision") != decision_version
            or core.get("analysis_input_version") != decision_version
        ):
            raise CoreDecisionBindingError()
    except (KeyError, TypeError, CoreDecisionBindingError) as error:
        if isinstance(error, CoreDecisionBindingError):
            raise
        raise CoreDecisionBindingError() from error


def validate_database_core_binding(
    *,
    decision: Any,
    pin: Mapping[str, object],
    w2_command: Mapping[str, object],
    job_analysis_input_version: str | None,
    source_link: Any,
) -> None:
    """Validate the DB source of truth before W1 creates or publishes a W2 command."""

    validate_core_pin_payload_binding(pin=pin, w2_command=w2_command)
    try:
        expected_owner = owner_for_core_scope(pin["decision_scope"])
        if (
            str(decision.id) != pin["decision_id"]
            or decision.decision_scope != pin["decision_scope"]
            or (str(decision.company_id) if decision.company_id is not None else None)
            != pin["company_id"]
            or (
                str(decision.question_version_id)
                if decision.question_version_id is not None
                else None
            )
            != pin["question_version_id"]
            or str(decision.source_id) != pin["source_id"]
            or decision.analysis_input_version != pin["analysis_input_version"]
            or decision.analysis_input_version != job_analysis_input_version
            or decision.decision_version != pin["decision_version"]
            or decision.decision_code != "CORE_REQUIRED"
            or decision.decision_owner != expected_owner
            or decision.reason_code != pin["reason_code"]
            or source_link is None
            or str(source_link.source_id) != pin["source_id"]
        ):
            raise CoreDecisionBindingError()
    except (AttributeError, KeyError, TypeError, CoreDecisionBindingError) as error:
        if isinstance(error, CoreDecisionBindingError):
            raise
        raise CoreDecisionBindingError() from error
