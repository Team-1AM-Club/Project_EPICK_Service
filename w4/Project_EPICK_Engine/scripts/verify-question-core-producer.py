"""Record local tests and process-restart evidence; never contact W1/AWS/LLMs."""

# ruff: noqa: E402
# Direct file execution bootstraps the repository path before local imports.

import argparse
import json
import platform
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from epick_w4.question_core_outbox import QuestionCoreOutbox
from scripts.verify_service_handoff import run_suite, source_hashes, write_json


def restart_evidence(contract_mode="w1-candidate"):
    with TemporaryDirectory(prefix="w4-core-restart-") as temporary:
        folder = Path(temporary)
        db_path, accepted = folder / "producer.sqlite3", folder / "accepted.jsonl"
        invocations = []

        def child(action, *, expected=0, extra=()):
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "examples/w4_question_core_local.py"),
                    action,
                    "--db",
                    str(db_path),
                    "--acceptance-log",
                    str(accepted),
                    "--contract",
                    contract_mode,
                    *extra,
                ],
                cwd=ROOT,
                capture_output=True,
                timeout=45,
                check=False,
            )
            invocations.append(
                {
                    "action": action,
                    "exit_code": result.returncode,
                    "expected_exit_code": expected,
                    "extra_arguments": list(extra),
                }
            )
            if result.returncode != expected:
                raise RuntimeError("LOCAL_RESTART_PROCESS_FAILED")

        child("prepare-crash", expected=17)
        store = QuestionCoreOutbox(db_path)
        prepared = store.get_submission("synthetic-submit-1")
        before_send = store.inspect(prepared.message_id)
        child("accept-crash", expected=23)
        after_lost_response = store.inspect(prepared.message_id)
        child("relay", extra=("--seconds", "61"))
        after_restart = store.inspect(prepared.message_id)
        bodies = accepted.read_text(encoding="utf-8").splitlines()
        passed = (
            before_send["state"] == "prepared"
            and before_send["attempt_count"] == 0
            and after_lost_response["state"] == "prepared"
            and after_restart["state"] == "sent"
            and after_restart["attempt_count"] == 2
            and bodies == [prepared.body, prepared.body]
        )
        evidence = {
            "status": "PASSED" if passed else "FAILED",
            "boundary": "REAL_LOCAL_SQLITE_FAKE_QUEUE",
            "contract_mode": contract_mode,
            "invocations": invocations,
            "before_send": before_send,
            "after_lost_response": after_lost_response,
            "after_restart": after_restart,
            "identical_bodies": bodies == [prepared.body, prepared.body],
            "synthetic_event": json.loads(prepared.body),
            "fake_acceptance_count": len(bodies),
            "actual_sqs_requests": 0,
            "w1_logical_once": "NOT_RUN",
        }
    evidence["temporary_database_teardown"] = "REMOVED" if not folder.exists() else "FAILED"
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--contract", choices=("test-only", "w1-candidate", "w1-adopted"), default="w1-candidate"
    )
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "status": "RUNNING",
        "python": platform.python_version(),
        "mode": "SYNTHETIC_LOCAL_ONLY",
        "w1_original_schema": (
            "W1_WIRE_ADOPTED_PINNED_RUNTIME_NOT_ENABLED"
            if args.contract == "w1-adopted"
            else "CANDIDATE_RECEIVED_HASH_VERIFIED_NOT_ADOPTED"
        ),
        "restart_contract": args.contract,
        "adoption": "W4_REPLY_DRAFT_ONLY",
        "joint_ct12": "NOT_RUN",
        "w4_accessible_producer_sha": None,
        "actual_sqs_requests": 0,
        "real_model_calls": 0,
    }
    write_json(output / "summary.json", report)
    write_json(output / "source-sha256.json", source_hashes())
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests"), pattern="test_question_core*.py"
    )
    with (output / "tests.log").open("w", encoding="utf-8") as stream:
        report["tests"] = run_suite(suite, stream, minimum=73)
    if report["tests"]["status"] == "PASSED":
        report["restart_demo"] = restart_evidence(args.contract)
    else:
        report["restart_demo"] = {"status": "NOT_RUN"}
    report["status"] = (
        "PASSED_LOCAL_ONLY"
        if report["tests"]["status"] == "PASSED" and report["restart_demo"]["status"] == "PASSED"
        else "FAILED"
    )
    write_json(output / "summary.json", report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "tests": report["tests"]["tests_run"],
                "joint_ct12": "NOT_RUN",
                "w1_original_schema": report["w1_original_schema"],
            }
        )
    )
    return 0 if report["status"] == "PASSED_LOCAL_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
