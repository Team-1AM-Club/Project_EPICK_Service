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

W4_QUESTION_CORE_SCHEMA_VERSION = "w4.private.question-core-decision/0.1-candidate"
W4_QUESTION_CORE_MESSAGE_TYPE = "w4.private.w1.question-core-decision"
W4_QUESTION_CORE_PRODUCER = "w4"
W4_QUESTION_CORE_CONSUMER = "w1.w4-question-core-decision"
W4_QUESTION_CORE_MAX_BODY_BYTES = 16 * 1024


class W4QuestionCoreContractError(ValueError):
    """The private W4 event is not a valid Question Core Decision."""

    def __init__(self, code: str = "W4_QUESTION_CORE_SCHEMA_INVALID") -> None:
        super().__init__(code)
        self.code = code


class W4QuestionCorePrincipalError(ValueError):
    """The separately authenticated sender does not match W1 configuration."""

    def __init__(self, code: str = "W4_QUESTION_CORE_PRINCIPAL_MISMATCH") -> None:
        super().__init__(code)
        self.code = code


class W4QuestionCoreReceiptOutcome(str, Enum):
    APPLIED = "APPLIED"
    DUPLICATE = "DUPLICATE"
    STALE_DISCARDED = "STALE_DISCARDED"
    REJECTED_SCHEMA = "REJECTED_SCHEMA"
    REJECTED_PRINCIPAL = "REJECTED_PRINCIPAL"
    REJECTED_BINDING = "REJECTED_BINDING"
    REJECTED_CONFLICT = "REJECTED_CONFLICT"
    RETRYABLE_INFRA_FAILURE = "RETRYABLE_INFRA_FAILURE"


@dataclass(frozen=True, slots=True)
class W4QuestionCoreEvent:
    schema_version: str
    message_type: str
    message_id: UUID
    decision_id: UUID
    occurred_at: str
    visibility_scope: str
    producer: str
    job_id: UUID
    company_id: None
    question_version_id: UUID
    source_id: UUID
    analysis_input_version: str
    decision_scope: str
    decision_owner: str
    decision_version: int
    is_core: bool
    decision_code: str
    reason_code: str
    payload_digest: str


@dataclass(frozen=True, slots=True)
class W4QuestionCoreReceipt:
    message_id: UUID
    payload_digest: str
    outcome: W4QuestionCoreReceiptOutcome
    retryable: bool
    error_code: str | None
    decision_id: UUID | None
    received_at: datetime


@lru_cache(maxsize=1)
def _w4_question_core_validator() -> Draft202012Validator:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "contracts"
        / "w4"
        / "v1"
        / "question-core-decision.event.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _reject_nonfinite_constant(_: str) -> object:
    raise W4QuestionCoreContractError("W4_QUESTION_CORE_NONFINITE_NUMBER")


def canonical_w4_question_core_json(payload: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise W4QuestionCoreContractError() from error


def w4_question_core_payload_digest(payload: Mapping[str, object]) -> str:
    return f"sha256:{hashlib.sha256(canonical_w4_question_core_json(payload)).hexdigest()}"


def verify_w4_question_core_principal(
    *, authenticated_principal: str, expected_principal: str
) -> None:
    """Require independently authenticated transport identity, never a body field."""

    if (
        not isinstance(authenticated_principal, str)
        or not authenticated_principal
        or not isinstance(expected_principal, str)
        or not expected_principal
        or authenticated_principal != expected_principal
    ):
        raise W4QuestionCorePrincipalError()


def parse_w4_question_core_event(
    body: bytes | str | Mapping[str, object],
) -> W4QuestionCoreEvent:
    try:
        if isinstance(body, bytes):
            if len(body) > W4_QUESTION_CORE_MAX_BODY_BYTES:
                raise W4QuestionCoreContractError("W4_QUESTION_CORE_BODY_TOO_LARGE")
            payload = json.loads(
                body.decode("utf-8"), parse_constant=_reject_nonfinite_constant
            )
        elif isinstance(body, str):
            if len(body.encode("utf-8")) > W4_QUESTION_CORE_MAX_BODY_BYTES:
                raise W4QuestionCoreContractError("W4_QUESTION_CORE_BODY_TOO_LARGE")
            payload = json.loads(body, parse_constant=_reject_nonfinite_constant)
        elif isinstance(body, Mapping):
            payload = dict(body)
        else:
            raise W4QuestionCoreContractError()
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise W4QuestionCoreContractError() from error

    if not isinstance(payload, dict):
        raise W4QuestionCoreContractError()
    try:
        _w4_question_core_validator().validate(payload)
        if len(canonical_w4_question_core_json(payload)) > W4_QUESTION_CORE_MAX_BODY_BYTES:
            raise W4QuestionCoreContractError("W4_QUESTION_CORE_BODY_TOO_LARGE")
        return W4QuestionCoreEvent(
            schema_version=payload["schema_version"],
            message_type=payload["message_type"],
            message_id=UUID(payload["message_id"]),
            decision_id=UUID(payload["decision_id"]),
            occurred_at=payload["occurred_at"],
            visibility_scope=payload["visibility_scope"],
            producer=payload["producer"],
            job_id=UUID(payload["job_id"]),
            company_id=None,
            question_version_id=UUID(payload["question_version_id"]),
            source_id=UUID(payload["source_id"]),
            analysis_input_version=payload["analysis_input_version"],
            decision_scope=payload["decision_scope"],
            decision_owner=payload["decision_owner"],
            decision_version=payload["decision_version"],
            is_core=payload["is_core"],
            decision_code=payload["decision_code"],
            reason_code=payload["reason_code"],
            payload_digest=w4_question_core_payload_digest(payload),
        )
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        if isinstance(error, W4QuestionCoreContractError):
            raise
        raise W4QuestionCoreContractError() from error
