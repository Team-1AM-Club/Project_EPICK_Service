"""W4 private Core Decision draft codec. No queue, endpoint or automatic policy.

The trusted host supplies owner-scoped current context and an explicit policy
decision. Registered fixtures are synthetic; this module does not send messages.
"""

from copy import deepcopy
from functools import lru_cache
import json
from pathlib import Path
from typing import Annotated, Literal

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import Field, ValidationError

from .c01_contract import UUIDText
from .handoff_contract import Identifier, Record
from .synthetic_policy import content_hash

W1_PIN = "afec08a9602132e5e433b523b0b6804850440524"
STATUS = "W4_UNADOPTED_PROPOSAL_TRANSPORT_DISCONNECTED"
SCHEMAS = Path(__file__).with_name("core_decision_schemas")


class CoreDecisionError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class DecisionContext(Record):
    """Private local context proposal, not additional fields on W1's wire payload."""
    schema_version: Literal["w4-core-decision-context/0.1-proposal"]
    data_kind: Literal["SYNTHETIC", "REAL"]
    owner_id: UUIDText
    project_id: UUIDText
    question_version_id: UUIDText
    source_id: UUIDText
    collection_company_id: UUIDText
    analysis_input_version: Annotated[str, Field(min_length=1, max_length=64, pattern=r"\S")]
    authorization_revision: Identifier
    current_decision_version: Annotated[int, Field(ge=0)]
    processing_allowed: bool
    question_current: bool
    source_active: bool


@lru_cache(maxsize=3)
def _validator(name):
    schema = json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _require(condition, code="CORE_DECISION_CONTRACT_INVALID"):
    if not condition:
        raise CoreDecisionError(code)


def _wire(name, value):
    try:
        _require(len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) <= 64_000)
        _require(_validator(name).is_valid(value))
    except (ValueError, TypeError, RecursionError):
        raise CoreDecisionError("CORE_DECISION_CONTRACT_INVALID") from None
    return deepcopy(value)


def validate_message(value):
    """Validate W1 envelope AND payload AND the W4-specific cross-object scope."""
    value = _wire("envelope", value)
    payload = _wire("payload", value["payload"])
    _require(value["producer"] == "w4" and value["channel"] == "w1.private.w4.core-source-decision.v1"
             and value["message_type"] == "w1.core-source-decision.v1"
             and payload["decision_scope"] == "QUESTION_MATCHING"
             and payload["company_id"] is None, "CORE_DECISION_SCOPE_MISMATCH")
    _require(type(payload["decision_version"]) is int and type(payload["is_core"]) is bool)
    return value


def _context(value, owner):
    try:
        context = DecisionContext.model_validate(value).model_dump()
    except (ValidationError, TypeError, ValueError):
        raise CoreDecisionError("CORE_CONTEXT_INVALID") from None
    _require(context["owner_id"] == owner, "CORE_OWNER_MISMATCH")
    _require(context["data_kind"] == "SYNTHETIC", "CORE_REAL_DATA_NOT_ENABLED")
    _require(context["processing_allowed"] and context["question_current"] and context["source_active"],
             "CORE_CONTEXT_NOT_CURRENT")
    return context


def _context_key(context):
    # The W1 decision cursor may advance after an ACK was lost. Identity/input/
    # authorization must stay fixed; exact retry at that version is permitted.
    return content_hash({k: v for k, v in context.items() if k != "current_decision_version"})


def _bind(message, context):
    payload = message["payload"]
    _require(all(payload[k] == context[k] for k in
                 ("question_version_id", "source_id", "analysis_input_version")), "CORE_INPUT_BINDING_MISMATCH")


def prepare_decision(context, *, authenticated_owner_id, message_id, decision_id, occurred_at,
                     decision_version, decision_code, reason_code):
    """Build from explicit host policy; never derive core-ness from LLM support/rank."""
    context = _context(context, authenticated_owner_id)
    message = validate_message({
        "schema_version": "w1.private.v1", "message_id": message_id,
        "message_type": "w1.core-source-decision.v1", "producer": "w4", "occurred_at": occurred_at,
        "visibility_scope": "PRIVATE", "channel": "w1.private.w4.core-source-decision.v1",
        "payload": {"schema_version": "w1.core-source-decision.v1", "decision_id": decision_id,
            "decision_scope": "QUESTION_MATCHING", "company_id": None,
            "question_version_id": context["question_version_id"], "source_id": context["source_id"],
            "analysis_input_version": context["analysis_input_version"], "decision_version": decision_version,
            "is_core": decision_code == "CORE_REQUIRED", "decision_code": decision_code, "reason_code": reason_code}})
    _require(decision_version > context["current_decision_version"], "CORE_DECISION_VERSION_NOT_NEW")
    return {"schema_version": "w4-prepared-core-decision/0.1-proposal", "status": STATUS, "w1_pin": W1_PIN,
            "context": context, "context_sha256": _context_key(context), "message": message,
            "message_sha256": content_hash(message)}


def check_current(prepared, *, current_context, authenticated_owner_id):
    """Recheck an immutable prepared message. Returning it does not transmit it."""
    _require(isinstance(prepared, dict) and set(prepared) == {"schema_version", "status", "w1_pin", "context",
             "context_sha256", "message", "message_sha256"}, "CORE_PREPARED_INVALID")
    _require(prepared["schema_version"] == "w4-prepared-core-decision/0.1-proposal"
             and prepared["status"] == STATUS and prepared["w1_pin"] == W1_PIN, "CORE_PREPARED_INVALID")
    before = _context(prepared["context"], authenticated_owner_id)
    current = _context(current_context, authenticated_owner_id)
    _require(prepared["context_sha256"] == _context_key(before) == _context_key(current), "CORE_CONTEXT_CHANGED")
    message = validate_message(prepared["message"])
    _require(content_hash(message) == prepared["message_sha256"], "CORE_PREPARED_MESSAGE_CHANGED")
    _bind(message, current)
    _require(current["current_decision_version"] >= before["current_decision_version"],
             "CORE_DECISION_CURSOR_REGRESSED")
    version = message["payload"]["decision_version"]
    _require(version > before["current_decision_version"], "CORE_DECISION_VERSION_NOT_NEW")
    _require(version >= current["current_decision_version"], "CORE_DECISION_STALE")
    return deepcopy(message)


def receipt_outcome(receipt, *, message):
    """W1 inbound delivery receipt, not W2's proposed commit-gate ACK."""
    message = validate_message(message)
    value = _wire("receipt", receipt)
    _require(value["message_id"] == message["message_id"] and value["consumer"] == "w1.core-source-decision",
             "CORE_RECEIPT_BINDING_MISMATCH")
    outcome = value["outcome"]
    return {"outcome": outcome, "retry_same_message": outcome == "RETRYABLE_INFRA_FAILURE",
            "collection_completion": "NOT_ESTABLISHED_BY_RECEIPT",
            "current_result_visibility": "NOT_ESTABLISHED_BY_RECEIPT"}
