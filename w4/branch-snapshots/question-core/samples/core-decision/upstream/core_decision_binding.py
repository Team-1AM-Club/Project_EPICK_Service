from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_SCOPE_OWNERS = {
    "COMPANY_KNOWLEDGE": "W3",
    "QUESTION_MATCHING": "W4",
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
