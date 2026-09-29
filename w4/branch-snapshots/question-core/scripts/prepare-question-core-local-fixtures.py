"""Generate explicitly TEST_ONLY fixtures, never a claimed W1 canonical schema."""

import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from epick_w4.question_core_contract import canonical_json, digest

OUT = ROOT / "samples/question-core-local"


def write(name, value):
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    uuid_schema = {"type": "string", "format": "uuid"}
    properties = {
        "schema_version": {"const": "test-only.w4.question-core/0"},
        "message_type": {"const": "test-only.w4.w1.question-core"},
        "visibility_scope": {"const": "PRIVATE"},
        "producer": {"const": "w4"},
        "decision_scope": {"const": "QUESTION_MATCHING"},
        "decision_owner": {"const": "W4"},
        "company_id": {"const": None},
        **{
            key: deepcopy(uuid_schema)
            for key in ("message_id", "decision_id", "job_id", "question_version_id", "source_id")
        },
        "occurred_at": {"type": "string", "format": "date-time"},
        "analysis_input_version": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
            "pattern": "\\S",
        },
        "decision_version": {"type": "integer", "minimum": 1},
        "is_core": {"type": "boolean"},
        "decision_code": {"enum": ["CORE_REQUIRED", "NON_CORE_OPTIONAL"]},
        "reason_code": {"enum": ["QUESTION_EVIDENCE_REQUIRED", "SUPPLEMENTARY_CONTEXT"]},
    }
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:epick:test-only:w4-question-core",
        "$comment": "LOCAL TEST ONLY. W1 004 original schema not received; never deploy this schema.",
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
        "allOf": [
            {
                "if": {"properties": {"decision_code": {"const": "CORE_REQUIRED"}}},
                "then": {"properties": {"is_core": {"const": True}}},
                "else": {"properties": {"is_core": {"const": False}}},
            }
        ],
    }
    write("test-only.schema.json", schema)
    context = {
        "context_key": "synthetic-w1-context-1",
        "job_id": "10000000-0000-4000-8000-000000000001",
        "question_version_id": "20000000-0000-4000-8000-000000000001",
        "source_id": "30000000-0000-4000-8000-000000000001",
        "analysis_input_version": "question-input:v4",
        "authorization_revision": "synthetic-auth-v1",
        "current_decision_version": 2,
        "data_kind": "SYNTHETIC",
        "processing_allowed": True,
        "question_current": True,
        "source_active": True,
        "revoked": False,
        "valid_until": "2030-01-01T00:00:00Z",
    }
    policy = {
        "decision_version": 3,
        "decision_code": "CORE_REQUIRED",
        "reason_code": "QUESTION_EVIDENCE_REQUIRED",
        "policy_revision": "synthetic-policy-v1",
        "approved": True,
    }
    write("context.json", context)
    write("policy.json", policy)
    event = {key: prop["const"] for key, prop in properties.items() if "const" in prop}
    event.update(
        {
            "message_id": "40000000-0000-4000-8000-000000000001",
            "decision_id": "50000000-0000-4000-8000-000000000001",
            "occurred_at": "2026-09-19T00:00:00Z",
            "decision_version": 3,
            "decision_code": "CORE_REQUIRED",
            "reason_code": "QUESTION_EVIDENCE_REQUIRED",
            "is_core": True,
        }
    )
    event.update(
        {
            key: context[key]
            for key in ("job_id", "question_version_id", "source_id", "analysis_input_version")
        }
    )
    write("core-required.json", event)
    optional = {
        **event,
        "message_id": "40000000-0000-4000-8000-000000000002",
        "decision_id": "50000000-0000-4000-8000-000000000002",
        "is_core": False,
        "decision_code": "NON_CORE_OPTIONAL",
        "reason_code": "SUPPLEMENTARY_CONTEXT",
    }
    write("non-core-optional.json", optional)
    rows = []
    changes = {
        "wrong_job_format": {"job_id": "not-a-uuid"},
        "wrong_producer": {"producer": "w3"},
        "wrong_scope": {"decision_scope": "COMPANY_KNOWLEDGE"},
        "wrong_owner": {"decision_owner": "W3"},
        "company_non_null": {"company_id": context["job_id"]},
        "wrong_question_format": {"question_version_id": "question"},
        "wrong_source_format": {"source_id": "source"},
        "empty_input": {"analysis_input_version": " "},
        "zero_revision": {"decision_version": 0},
        "negative_revision": {"decision_version": -1},
        "bool_revision": {"decision_version": True},
        "float_revision": {"decision_version": 3.0},
        "code_core_mismatch": {"is_core": False},
        "unknown_field": {"owner_id": context["job_id"]},
        "oversized_body": {"analysis_input_version": "x" * (16 * 1024)},
        "same_message_and_decision_id": {"decision_id": event["message_id"]},
    }
    for case, change in changes.items():
        rows.append({"case_id": case, "layer": "CONTRACT", "event": {**event, **change}})
    for key in ("job_id", "question_version_id"):
        value = deepcopy(event)
        value.pop(key)
        rows.append({"case_id": "missing_" + key, "layer": "CONTRACT", "event": value})
    for key, value in {
        "job_id": "10000000-0000-4000-8000-000000000009",
        "question_version_id": "20000000-0000-4000-8000-000000000009",
        "source_id": "30000000-0000-4000-8000-000000000009",
        "analysis_input_version": "question-input:v5",
    }.items():
        rows.append(
            {"case_id": "wrong_bound_" + key, "layer": "CONTEXT", "event": {**event, key: value}}
        )
    write("negative-events.json", rows)
    vectors = [
        {
            "case_id": name,
            "value": value,
            "canonical_utf8": canonical_json(value).decode("utf-8"),
            "digest": digest(value),
        }
        for name, value in [
            ("core", event),
            ("optional", optional),
            ("unicode_digest_only_not_wire", {"z": "한글", "a": [2, 1], "null": None}),
        ]
    ]
    write("digest-vectors.json", vectors)
    manifest = {
        "status": "TEST_ONLY_W1_ORIGINAL_MISSING",
        "w1_canonical_schema": None,
        "external_send_allowed": False,
        "files": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(OUT.glob("*.json"))
            if p.name != "manifest.json"
        },
    }
    write("manifest.json", manifest)
    print(
        f"Generated TEST_ONLY schema, {len(rows)} negative events and {len(vectors)} digest vectors"
    )


if __name__ == "__main__":
    main()
