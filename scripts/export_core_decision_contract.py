"""Export reproducible W3 -> W1 candidate schemas and synthetic cases."""

import argparse
import json
from pathlib import Path

from w3_knowledge.core_decision import CoreDecisionEvent, VERSION


def artifacts():
    core = {
        "schema_version": VERSION,
        "message_type": "w3.private.w1.core-decision",
        "message_id": "50000000-0000-4000-8000-000000000001",
        "occurred_at": "2026-09-18T00:00:00Z",
        "visibility_scope": "PRIVATE",
        "producer": "w3",
        "job_id": "10000000-0000-4000-8000-000000000001",
        "company_id": "20000000-0000-4000-8000-000000000001",
        "source_id": "30000000-0000-4000-8000-000000000001",
        "analysis_input_version": "knowledge-input:alpha",
        "decision_scope": "COMPANY_KNOWLEDGE",
        "decision_owner": "W3",
        "question_version_id": None,
        "decision_version": 1,
        "is_core": True,
        "decision_code": "CORE_REQUIRED",
        "reason_code": "REQUIRED_COMPANY_EVIDENCE",
    }
    CoreDecisionEvent.model_validate(core)
    files = {
        "event.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            **CoreDecisionEvent.model_json_schema(),
        },
        "valid-core.json": core,
        "valid-non-core.json": {
            **core,
            "message_id": "50000000-0000-4000-8000-000000000002",
            "decision_version": 2,
            "is_core": False,
            "decision_code": "NON_CORE_OPTIONAL",
            "reason_code": "OPTIONAL_CONTEXT",
        },
    }
    for name, change in {
        "owner": {"decision_owner": "W4"},
        "scope": {"decision_scope": "QUESTION_MATCHING"},
        "producer": {"producer": "w4"},
        "core-pair": {"is_core": False},
        "revision": {"decision_version": 0},
        "company": {"company_id": None},
        "question": {"question_version_id": "40000000-0000-4000-8000-000000000001"},
        "input": {"analysis_input_version": ""},
    }.items():
        files[f"invalid-{name}.json"] = {**core, **change}
    files["consumer-scenarios.json"] = {
        "status": "W1_CT12_ACCEPTANCE_PROPOSAL_NOT_EXECUTED",
        "current": {
            k: core[k] for k in ("job_id", "company_id", "source_id", "analysis_input_version")
        },
        "cases": [
            {"name": "first", "event": core, "expected": "APPLY_ATOMIC_DECISION_AND_PIN"},
            {"name": "duplicate", "event": core, "expected": "SAME_RECEIPT_NO_WRITE"},
            {
                "name": "id-conflict",
                "event": {**core, "reason_code": "DIFFERENT_REASON"},
                "expected": "CONFLICT_NO_WRITE",
            },
            {
                "name": "stale-input",
                "event": {**core, "analysis_input_version": "older"},
                "expected": "STALE_NO_WRITE",
            },
            {
                "name": "wrong-source",
                "event": {**core, "source_id": "30000000-0000-4000-8000-000000000002"},
                "expected": "BINDING_MISMATCH_NO_WRITE",
            },
            {
                "name": "wrong-company",
                "event": {**core, "company_id": "20000000-0000-4000-8000-000000000002"},
                "expected": "BINDING_MISMATCH_NO_WRITE",
            },
            {
                "name": "stale-revision",
                "event": core,
                "stored_revision": 2,
                "expected": "STALE_NO_WRITE",
            },
            {
                "name": "same-revision-new-id",
                "event": {**core, "message_id": "50000000-0000-4000-8000-000000000003"},
                "stored_revision": 1,
                "expected": "CONFLICT_NO_WRITE",
            },
            {
                "name": "forged-principal",
                "event": core,
                "authenticated_principal": "w4",
                "expected": "UNAUTHENTICATED_NO_WRITE",
            },
        ],
        "execution_note": "Cases are independent; duplicate/id-conflict have the first event stored. Stale revision assumes a different ID at stored revision 2 and no stored incoming ID. They require W1 transaction/authentication integration.",
    }
    return files


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir", type=Path, default=Path("contracts/core-decision/v0.1-candidate")
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    drift = []
    for name, value in artifacts().items():
        content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        path = args.output_dir / name
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                drift.append(name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    print(json.dumps({"files": len(artifacts()), "drift": drift}))
    raise SystemExit(bool(drift))


if __name__ == "__main__":
    main()
