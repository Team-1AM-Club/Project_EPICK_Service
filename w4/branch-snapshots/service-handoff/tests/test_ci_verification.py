"""A partial or skipped suite must not produce a successful handoff report."""

import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.verify_service_handoff import run_suite

ROOT = Path(__file__).resolve().parents[1]


class VerificationTests(unittest.TestCase):
    def check_suite(self, case, minimum=1):
        return run_suite(unittest.TestLoader().loadTestsFromTestCase(case), io.StringIO(), minimum)

    def test_all_executed_tests_pass(self):
        class Passing(unittest.TestCase):
            def test_ok(self):
                pass
        result = self.check_suite(Passing)
        self.assertEqual((result["status"], result["passed"], result["tests_run"]), ("PASSED", 1, 1))

    def test_missing_tests_fail_even_when_discovered_tests_pass(self):
        class Passing(unittest.TestCase):
            def test_ok(self):
                pass
        result = self.check_suite(Passing, minimum=2)
        self.assertEqual(result["status"], "FAILED")
        self.assertIn("INCOMPLETE_TEST_DISCOVERY_OR_EXECUTION", result["reasons"])

    def test_missing_optional_dependency_skip_fails(self):
        @unittest.skip("httpx not installed")
        class Skipped(unittest.TestCase):
            def test_api(self):
                pass
        result = self.check_suite(Skipped)
        self.assertEqual((result["status"], result["skipped"], result["passed"]), ("FAILED", 1, 0))

    def test_failed_module_import_is_recorded_as_failure(self):
        suite = unittest.TestLoader().loadTestsFromName("w4_missing_test_module_for_ci")
        result = run_suite(suite, io.StringIO(), minimum=1)
        self.assertEqual((result["status"], result["errors"], result["passed"]), ("FAILED", 1, 0))

    def test_multiple_subtest_failures_do_not_create_negative_pass_counts(self):
        class Failing(unittest.TestCase):
            def test_values(self):
                for i in range(2):
                    with self.subTest(i=i):
                        self.fail("intentional")
        result = self.check_suite(Failing)
        self.assertEqual((result["status"], result["passed"], result["failures"]), ("FAILED", 0, 2))

    def test_expected_failure_is_not_accepted_as_complete_verification(self):
        class Expected(unittest.TestCase):
            @unittest.expectedFailure
            def test_pending(self):
                self.fail("intentional")
        self.assertEqual(self.check_suite(Expected)["status"], "FAILED")

    def test_existing_success_report_is_preserved_and_reuse_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "summary.json"
            report.write_text('{"status":"PASSED","previous":true}', encoding="utf-8")
            completed = subprocess.run([sys.executable, str(ROOT / "scripts/verify_service_handoff.py"),
                                        "--output-dir", directory], capture_output=True, text=True)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("already exists", completed.stderr)
            self.assertEqual(report.read_text(encoding="utf-8"), '{"status":"PASSED","previous":true}')
