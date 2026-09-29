"""Prepare synthetic fixtures against the exact received W1 candidate bytes."""

# ruff: noqa: E402
# Direct file execution bootstraps the repository path before local imports.

import hashlib
import json
import runpy
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from epick_w4.question_core_contract import canonical_json, digest
from examples.w4_question_core_local import CANDIDATE_FIXTURES, candidate_contract, read

OUT = CANDIDATE_FIXTURES


def write(name, value):
    (OUT / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main():
    contract = candidate_contract()
    schema = json.loads((OUT / "upstream/w4-question-core-decision.event.schema.json").read_bytes())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    constants = {
        key: prop["const"] for key, prop in schema["properties"].items() if "const" in prop
    }

    def project(event):
        return {**event, **constants}

    core = project(read("core-required.json"))
    optional = project(read("non-core-optional.json"))
    for event in (core, optional):
        contract.validate(event)
        validator.validate(event)
    write("core-required.json", core)
    write("non-core-optional.json", optional)
    rows = []
    for original in read("negative-events.json"):
        event = dict(original["event"])
        # Only version/type change; retain intentionally wrong fixed fields.
        for key in ("schema_version", "message_type"):
            event[key] = constants[key]
        rows.append(
            {
                "case_id": original["case_id"],
                "layer": "CODEC" if original["layer"] == "CONTRACT" else "CONTEXT",
                "schema_valid": validator.is_valid(event),
                "event": event,
            }
        )
    for name, reason in (
        ("reason_lowercase", "private"),
        ("reason_with_space", "QUESTION EVIDENCE"),
        ("reason_too_long", "A" * 65),
    ):
        event = {**core, "reason_code": reason}
        rows.append({"case_id": name, "layer": "CODEC", "schema_valid": False, "event": event})
    write("negative-events.json", rows)
    write(
        "policy-boundary.json",
        {
            "status": "SYNTHETIC_ONLY_PRODUCTION_APPROVAL_PENDING",
            "wire_valid_but_unapproved_reason": {**core, "reason_code": "FUTURE_REASON"},
            "expected_schema_valid": True,
            "expected_codec_valid": True,
            "expected_producer_policy_valid": False,
            "approved_synthetic_pairs": {
                "CORE_REQUIRED": "QUESTION_EVIDENCE_REQUIRED",
                "NON_CORE_OPTIONAL": "SUPPLEMENTARY_CONTEXT",
            },
        },
    )
    helper = runpy.run_path(
        str(ROOT / "samples/question-core-local/upstream/w1_core_decision_binding.py")
    )
    vectors = []
    for case, value in (
        ("core", core),
        ("optional", optional),
        ("unicode_digest_only_not_wire", {"z": "한글", "a": [2, 1], "null": None}),
    ):
        computed = digest(value)
        if helper["core_decision_payload_digest"](value) != computed:
            raise ValueError("Existing W1 generic helper digest mismatch")
        vectors.append(
            {
                "case_id": case,
                "value": value,
                "canonical_utf8": canonical_json(value).decode("utf-8"),
                "digest": computed,
            }
        )
    write("digest-vectors.json", vectors)
    write(
        "manifest.json",
        {
            "status": "W1_CANDIDATE_RECEIVED_NOT_ADOPTED",
            "schema_sha256": contract.source.schema_sha256,
            "candidate_commit_sha": None,
            "external_send_allowed": False,
            "canonical_digest_status": "W4_PROPOSAL_EXISTING_W1_HELPER_MATCHED_NOT_004_CONSUMER_APPROVAL",
            "policy_production_approved": False,
            "max_body_bytes_status": "W4_LOCAL_LIMIT_16384_NOT_A_W1_SCHEMA_CONSTRAINT",
            "files": {
                p.relative_to(OUT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(OUT.rglob("*"))
                if p.is_file() and p.name != "manifest.json"
            },
        },
    )
    print(
        json.dumps(
            {
                "candidate_schema_loaded": True,
                "valid_fixtures": 2,
                "negative_fixtures": len(rows),
                "digest_vectors": len(vectors),
                "contract_adopted": False,
            }
        )
    )


if __name__ == "__main__":
    main()
