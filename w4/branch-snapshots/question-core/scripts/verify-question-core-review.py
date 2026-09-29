"""Verify W1's pre-adoption baseline without promoting it to a W4 wire contract."""

import argparse
import hashlib
import json
import subprocess
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
BASELINE_SHA = "bb27a692cf002100a3f773df7e434e90fe03f83f"
SNAPSHOT = ROOT / "samples/question-core-review-20260919"
BASELINE_FILES = {
    "backend/contracts/w1/v1/private-message-envelope.schema.json": "8119cb594782187669df608d6f1748c57c45130356998c7cd77867cdd9f80201",
    "backend/contracts/w1/v1/core-source-decision.schema.json": "421cf7808c1e15758b9de1164b606f7007010636e3394befadb1f38ff5997b4e",
    "backend/contracts/fixtures/v1/w1/private-core-source-decision-question.json": "805cdd8a88271ca5ea9f2712a5b6cec3c1bee2d8e72e361b08b892508d99ba7f",
}
CANDIDATES = {
    "specs/004-w1-w4-question-core/w4-adoption.md": "6b4bfe3b0796c376b9b5f2831e66aaf526c3f27f94e80f5112f2df0d559b537f",
    "specs/004-w1-w4-question-core/contracts/w4-question-core-decision.event.schema.json": "1eb0506d9b13e22198ea11fb5fccd628096de2903b0fe5b1da3d383928d8407a",
}


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def pin(path, raw):
    if path.exists() and path.read_bytes() != raw:
        raise ValueError("Refusing to replace a different pinned source: " + path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": sha256(raw)}


def boundary_checks(blobs):
    envelope_schema = json.loads(blobs[next(iter(BASELINE_FILES))])
    storage_schema = json.loads(blobs["backend/contracts/w1/v1/core-source-decision.schema.json"])
    fixture = json.loads(
        blobs["backend/contracts/fixtures/v1/w1/private-core-source-decision-question.json"]
    )
    validators = []
    for schema in (envelope_schema, storage_schema):
        Draft202012Validator.check_schema(schema)
        validators.append(Draft202012Validator(schema, format_checker=FormatChecker()))
    envelope, storage = validators
    flat = json.loads((ROOT / "samples/question-core-local/core-required.json").read_bytes())
    wrong_company = {**fixture["payload"], "company_id": fixture["payload"]["source_id"]}
    wrong_question = {**fixture["payload"], "question_version_id": None}
    wrong_code = {**fixture["payload"], "is_core": True}
    wrong_channel = deepcopy(fixture)
    wrong_channel["channel"] = "w1.private.w3.core-source-decision.v1"
    extra_job = {**fixture["payload"], "job_id": flat["job_id"]}
    checks = {
        "existing_fixture_passes_envelope": envelope.is_valid(fixture),
        "existing_fixture_passes_storage_payload": storage.is_valid(fixture["payload"]),
        "existing_question_pin_company_is_null": fixture["payload"]["company_id"] is None,
        "existing_fixture_has_no_explicit_job_binding": "job_id" not in fixture
        and "job_id" not in fixture["payload"],
        "existing_storage_rejects_non_null_question_company": not storage.is_valid(wrong_company),
        "existing_storage_rejects_missing_question_identity": not storage.is_valid(wrong_question),
        "existing_storage_rejects_code_boolean_mismatch": not storage.is_valid(wrong_code),
        "existing_envelope_rejects_w4_using_w3_channel": not envelope.is_valid(wrong_channel),
        "existing_storage_cannot_gain_job_id_without_revision": not storage.is_valid(extra_job),
        "test_only_flat_event_is_not_existing_envelope": not envelope.is_valid(flat),
        "test_only_flat_event_is_not_existing_storage_payload": not storage.is_valid(flat),
    }
    if not all(checks.values()):
        raise ValueError("Unexpected baseline compatibility result")
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git", default="git")
    parser.add_argument("--service-reference", type=Path, required=True)
    parser.add_argument("--request-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)

    def git(*command):
        return subprocess.check_output(
            [args.git, "-C", str(args.service_reference), *command], stderr=subprocess.PIPE
        )

    resolved = git("rev-parse", BASELINE_SHA + "^{commit}").decode().strip()
    if resolved != BASELINE_SHA:
        raise ValueError("W1 baseline commit mismatch")
    tree = set(git("ls-tree", "-r", "--name-only", BASELINE_SHA).decode().splitlines())
    blobs = {}
    for path, expected in BASELINE_FILES.items():
        raw = git("show", f"{BASELINE_SHA}:{path}")
        if sha256(raw) != expected:
            raise ValueError("W1 request checksum mismatch: " + path)
        blobs[path] = raw
    checks = boundary_checks(blobs)
    pinned = [pin(SNAPSHOT / "upstream" / Path(path).name, raw) for path, raw in blobs.items()]
    pinned.append(
        pin(
            ROOT / "docs/inputs/W1_W4_Required_Contracts_2026-09-19.md",
            args.request_file.read_bytes(),
        )
    )
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "BASELINE_VERIFIED_PRE_ADOPTION",
        "repository": "https://github.com/Team-1AM-Club/Project_EPICK_Service",
        "baseline_full_sha": BASELINE_SHA,
        "baseline_scope": "EXISTING_ENVELOPE_AND_STORAGE_NOT_W4_FLAT_ADOPTION",
        "baseline_checksums_verified": len(blobs),
        "pinned_sources": pinned,
        "candidate_sources": [
            {
                "path": path,
                "review_sha256_from_request": checksum,
                "present_in_baseline_commit": path in tree,
                "original_bytes_verified": False,
                "review_status": "ORIGINAL_NOT_RECEIVED",
            }
            for path, checksum in CANDIDATES.items()
        ],
        "boundary_checks": checks,
        "boundary_checks_passed": len(checks),
        "w1_w4_contract_adopted": False,
        "w1_consumer_contract_suite": "NOT_RUN",
        "joint_ct12": "NOT_RUN",
        "actual_queue_calls": 0,
        "real_model_calls": 0,
        "next_step": "REVIEW_CANDIDATE_BYTES_AND_AGREE_D01_D03_BEFORE_ADOPTED_SHA_EXCHANGE",
    }
    raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    (output / "source-review.json").write_bytes(raw)
    (SNAPSHOT / "source-review.json").write_bytes(raw)
    print(
        json.dumps(
            {
                "status": report["status"],
                "baseline_files_verified": len(blobs),
                "boundary_checks_passed": len(checks),
                "candidate_originals_received": 0,
                "contract_adopted": False,
            }
        )
    )


if __name__ == "__main__":
    main()
