"""Materialize reviewable synthetic W4 fixtures; does not contact W1 or a model."""
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from epick_w4.core_decision import DecisionContext  # noqa: E402

OUT = ROOT / "samples/core-decision"


def write(name, data):
    (OUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    optional = json.loads((OUT / "upstream/question-decision.json").read_text(encoding="utf-8"))
    required = deepcopy(optional)
    required["message_id"] = "30000000-0000-4000-8000-000000000003"
    required["occurred_at"] = "2026-09-18T03:00:00Z"
    required["payload"].update({
        "decision_id": "31000000-0000-4000-8000-000000000003",
        "decision_version": 3, "is_core": True,
        "decision_code": "CORE_REQUIRED", "reason_code": "QUESTION_EVIDENCE_REQUIRED",
    })
    write("non-core.json", optional)
    write("core-required.json", required)
    write("context.schema.json", DecisionContext.model_json_schema())

    invalid = []

    def negative(name, change, code="CORE_DECISION_CONTRACT_INVALID"):
        message = deepcopy(required)
        change(message)
        invalid.append({"case_id": name, "expected_error": code, "message": message})

    def payload(**values):
        return lambda message: message["payload"].update(values)

    negative("wrong-channel", lambda m: m.update(channel="w1.private.w3.core-source-decision.v1"))
    negative("public", lambda m: m.update(visibility_scope="PUBLIC"))
    negative("numeric-input-version", payload(analysis_input_version=4))
    negative("company-on-question", payload(company_id="10000000-0000-4000-8000-000000000004"))
    negative("missing-question", payload(question_version_id=None))
    negative("bad-source-uuid", payload(source_id="source-1"))
    negative("missing-source", lambda m: m["payload"].pop("source_id"))
    negative("core-code-contradiction", payload(is_core=False))
    negative("unknown-decision-code", payload(decision_code="SUPPORTED"))
    negative("bad-reason", payload(reason_code="a raw private explanation"))
    negative("zero-version", payload(decision_version=0))
    negative("float-version", payload(decision_version=3.0))
    negative("boolean-version", payload(decision_version=True))
    negative("boolean-as-integer", payload(is_core=1))
    negative("extra-owner-on-wire", payload(owner_id="10000000-0000-4000-8000-000000000003"))
    negative("extra-raw-text-on-wire", lambda m: m.update(raw_text="synthetic private text"))
    negative("naive-time", lambda m: m.update(occurred_at="2026-09-18T03:00:00"))
    negative("invalid-calendar-time", lambda m: m.update(occurred_at="2026-02-30T03:00:00Z"))
    negative("wrong-producer", lambda m: m.update(
        producer="w3", channel="w1.private.w3.core-source-decision.v1"), "CORE_DECISION_SCOPE_MISMATCH")
    negative("company-scope-on-w4", payload(
        decision_scope="COMPANY_KNOWLEDGE", question_version_id=None,
        company_id="10000000-0000-4000-8000-000000000004"), "CORE_DECISION_SCOPE_MISMATCH")
    write("negative-messages.json", invalid)
    outcomes = ("APPLIED", "DUPLICATE", "STALE_DISCARDED", "REJECTED_SCHEMA",
                "REJECTED_PRINCIPAL", "RETRYABLE_INFRA_FAILURE")
    write("receipts.json", [{
        "schema_version": "w1.private.receipt.v1", "message_id": required["message_id"],
        "consumer": "w1.core-source-decision", "outcome": outcome,
        "retryable": outcome == "RETRYABLE_INFRA_FAILURE",
        "error_code": None if outcome == "APPLIED" else "SYNTHETIC_" + outcome,
        "received_at": "2026-09-18T03:00:01Z",
    } for outcome in outcomes])
    print(f"Prepared 2 normal messages, {len(invalid)} negative messages and 6 receipts")


if __name__ == "__main__":
    main()
