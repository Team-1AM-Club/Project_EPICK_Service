"""Offline Core Decision checks and the pinned W1 company conflict reproduction."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from epick_w4.core_decision import (
    CoreDecisionError, STATUS, W1_PIN, check_current, prepare_decision,
    receipt_outcome, validate_message,
)

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples/core-decision"


def read(name):
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def prepared_fixture(name="core-required.json", context=None):
    context = read("context.json") if context is None else context
    expected = read(name)
    payload = expected["payload"]
    return prepare_decision(
        context, authenticated_owner_id=context["owner_id"],
        message_id=expected["message_id"], decision_id=payload["decision_id"],
        occurred_at=expected["occurred_at"], decision_version=payload["decision_version"],
        decision_code=payload["decision_code"], reason_code=payload["reason_code"],
    )


def verify_upstream_bytes():
    manifest = json.loads((ROOT / "epick_w4/core_decision_schemas/baseline.json").read_text(encoding="utf-8"))
    if manifest["commit"] != W1_PIN or len(manifest["files"]) != 8:
        raise ValueError("UPSTREAM_PIN_MISMATCH")
    for row in manifest["files"]:
        if hashlib.sha256((ROOT / row["local_path"]).read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError("UPSTREAM_BYTES_MISMATCH")
    return len(manifest["files"])


def company_binding_probes():
    # Exact pinned pure helper, inspected before vendoring. No production mutation.
    verify_upstream_bytes()
    path = SAMPLES / "upstream/core_decision_binding.py"
    spec = importlib.util.spec_from_file_location("w1_pinned_core_binding", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dispatch = read("upstream/w2-dispatch.json")
    validator = Draft202012Validator(read("upstream/w2-command.schema.json"), format_checker=FormatChecker())
    rows = []

    def probe(name, pin, command, expected_schema, expected_binding):
        valid_schema = validator.is_valid(command)
        error = None
        try:
            module.validate_core_pin_payload_binding(pin=pin, w2_command=command)
            valid_binding = True
        except module.CoreDecisionBindingError as exc:
            valid_binding = False
            error = exc.code
        rows.append({"case_id": name, "pin": pin, "w2_command": command,
                     "w2_schema_valid": valid_schema, "w1_binding_valid": valid_binding,
                     "binding_error": error,
                     "expected_reproduced": (valid_schema, valid_binding) == (expected_schema, expected_binding)})

    probe("company-scope-control", dispatch["core_decision_pin"], dispatch["payload"], True, True)
    message = read("core-required.json")
    original = {"origin_message_id": message["message_id"], **message["payload"]}
    original.pop("schema_version")
    company = dispatch["payload"]["company_id"]
    for name, pin_company, command_company, schema_ok, binding_ok in (
        ("question-null-command-uuid", None, company, True, False),
        ("question-null-command-null", None, None, False, True),
        ("question-uuid-command-uuid", company, company, True, False),
    ):
        pin = {**original, "company_id": pin_company}
        command = deepcopy(dispatch["payload"])
        command.update(company_id=command_company, source_id=pin["source_id"], input_version=pin["decision_version"])
        command["core_source_decision"].update(
            is_core=True, decided_by="W4", rationale=pin["reason_code"],
            decision_revision=pin["decision_version"], analysis_input_version=pin["decision_version"])
        probe(name, pin, command, schema_ok, binding_ok)
    return rows


def run_demo():
    context = read("context.json")
    prepared = prepared_fixture()
    message = check_current(prepared, current_context=context, authenticated_owner_id=context["owner_id"])
    advanced = {**context, "current_decision_version": message["payload"]["decision_version"]}
    retry = check_current(prepared, current_context=advanced, authenticated_owner_id=context["owner_id"])
    negatives = []
    for row in read("negative-messages.json"):
        actual = None
        try:
            validate_message(row["message"])
        except CoreDecisionError as exc:
            actual = exc.code
        negatives.append({"case_id": row["case_id"], "expected_error": row["expected_error"],
                          "actual_error": actual, "passed": actual == row["expected_error"]})
    context_checks = []
    for name, changes, expected in (
        ("revoked", {"processing_allowed": False}, "CORE_CONTEXT_NOT_CURRENT"),
        ("input-changed", {"analysis_input_version": "question-input:v5"}, "CORE_CONTEXT_CHANGED"),
        ("newer-decision", {"current_decision_version": 4}, "CORE_DECISION_STALE"),
        ("deleted-source", {"source_active": False}, "CORE_CONTEXT_NOT_CURRENT"),
    ):
        actual = None
        try:
            check_current(prepared, current_context={**context, **changes}, authenticated_owner_id=context["owner_id"])
        except CoreDecisionError as exc:
            actual = exc.code
        context_checks.append({"case_id": name, "expected_error": expected,
                               "actual_error": actual, "passed": actual == expected})
    receipts = [receipt_outcome(value, message=message) for value in read("receipts.json")]
    probes = company_binding_probes()
    checks = {
        "upstream_exact_bytes": verify_upstream_bytes() == 8,
        "core_matches_fixture": message == read("core-required.json"),
        "non_core_matches_w1_fixture": prepared_fixture("non-core.json")["message"] == read("upstream/question-decision.json"),
        "retry_identical_message": message == retry,
        "negative_messages": all(row["passed"] for row in negatives),
        "current_context_fences": all(row["passed"] for row in context_checks),
        "company_conflict_reproduced": all(row["expected_reproduced"] for row in probes),
        "receipt_does_not_assert_completion": all(row["collection_completion"] == "NOT_ESTABLISHED_BY_RECEIPT" for row in receipts),
    }
    return {"status": "PASSED" if all(checks.values()) else "FAILED", "proposal_status": STATUS,
            "w1_pin": W1_PIN, "checks": checks, "prepared": prepared,
            "negative_messages": negatives, "context_checks": context_checks,
            "receipts": receipts, "company_binding_probes": probes,
            "real_model_calls": 0, "external_messages_sent": 0,
            "w1_live_queue": "NOT_RUN", "ct12": "NOT_RUN", "ct15": "NOT_RUN",
            "warning": "Reproduced a blocking upstream mismatch; PASSED is a local check result, not integration approval."}
