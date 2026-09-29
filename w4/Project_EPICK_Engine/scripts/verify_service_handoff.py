"""Record full unittest and synthetic HTTP demo results without a model server."""

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
from importlib import import_module, metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import traceback
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MINIMUM_TESTS = 249  # Previous accepted handoff; additions must not hide missing tests.


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.passed_ids = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed_ids.append(test.id())


def run_suite(suite, stream, minimum=MINIMUM_TESTS):
    discovered = suite.countTestCases()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=RecordingResult).run(suite)
    reasons = []
    if discovered < minimum or result.testsRun != discovered:
        reasons.append("INCOMPLETE_TEST_DISCOVERY_OR_EXECUTION")
    if not result.wasSuccessful() or result.expectedFailures:
        reasons.append("TEST_FAILURE_OR_ERROR")
    if result.skipped:
        reasons.append("SKIPPED_TESTS")
    return {
        "status": "FAILED" if reasons else "PASSED",
        "minimum_tests": minimum,
        "tests_discovered": discovered,
        "tests_run": result.testsRun,
        "passed": len(result.passed_ids),
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "expected_failures": len(result.expectedFailures),
        "unexpected_successes": len(result.unexpectedSuccesses),
        "reasons": reasons,
        "passed_ids": result.passed_ids,
        "failed_ids": [test.id() for test, _ in result.failures + result.errors],
        "skipped_tests": [{"id": test.id(), "reason": why} for test, why in result.skipped],
    }


def source_hashes():
    files = [ROOT / "pyproject.toml", ROOT / "uv.lock"]
    for folder in ("epick_w4", "examples", "scripts", "tests", "samples", "schemas", ".github", "deploy"):
        files.extend(p for p in (ROOT / folder).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts)
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(files)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output/ci/verification")
    args = parser.parse_args()
    out = args.output_dir.resolve()
    # An old successful report must never be mistaken for this run's result.
    try:
        out.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        parser.error("output directory already exists; choose a new directory")
    github = os.environ.get("GITHUB_ACTIONS") == "true"
    summary = {
        "status": "RUNNING",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "execution_environment": "GITHUB_ACTIONS" if github else "LOCAL",
        "github_sha": os.environ.get("GITHUB_SHA") if github else None,
        "github_run_id": os.environ.get("GITHUB_RUN_ID") if github else None,
        "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT") if github else None,
        "tests": {"status": "NOT_RUN"},
        "demos": {"status": "NOT_RUN"},
        "live_team_backend_integration": "NOT_RUN",
        "real_model_quality": "NOT_MEASURED",
        "w3_contract_status": "LOCAL_PROJECTION_DRAFT_PENDING_TEAM_REVIEW",
    }
    write_json(out / "summary.json", summary)
    try:
        write_json(out / "source-sha256.json", source_hashes())
        packages = {}
        for name in ("pydantic", "fastapi", "httpx"):
            try:
                import_module(name)
                packages[name] = metadata.version(name)
            except ImportError:
                packages[name] = "MISSING_OR_IMPORT_ERROR"
        write_json(out / "environment.json", {
            "python": platform.python_version(), "platform": platform.platform(),
            "packages": packages,
            "required_install": "uv sync --locked --extra api --extra test",
        })
        with (out / "tests.log").open("w", encoding="utf-8") as log:
            with redirect_stdout(log), redirect_stderr(log):
                suite = unittest.TestLoader().discover(str(ROOT / "tests"))
                summary["tests"] = run_suite(suite, log)
        write_json(out / "tests.json", summary["tests"])
        print("Tests:", summary["tests"]["status"], summary["tests"]["passed"], "passed")
        # Run demos even when a test fails, to retain independent diagnostic output.
        with (out / "demos.log").open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts/demo-service-handoff.py"),
                 "--output-dir", str(out / "demos")],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=120, check=False,
            )
        demo_report = out / "demos/demo-summary.json"
        if completed.returncode == 0 and demo_report.is_file():
            summary["demos"] = json.loads(demo_report.read_text(encoding="utf-8"))
        else:
            summary["demos"] = {"status": "FAILED", "exit_code": completed.returncode}
        summary["status"] = ("PASSED" if all(summary[key]["status"] == "PASSED"
                                             for key in ("tests", "demos")) else "FAILED")
    except Exception as exc:
        (out / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        summary["status"] = "FAILED"
        summary["error_type"] = type(exc).__name__
    finally:
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json(out / "summary.json", summary)
    print("Verification:", summary["status"], str(out))
    if summary["status"] != "PASSED":
        for name in ("tests.log", "demos.log", "error.log"):
            path = out / name
            if path.is_file():
                print(path.read_text(encoding="utf-8"))
    return 0 if summary["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
