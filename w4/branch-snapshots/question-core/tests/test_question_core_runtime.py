"""Host plan, local entrypoint, dedicated temporary credentials; no AWS calls."""

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from epick_w4.question_core_contract import QuestionCoreError
from epick_w4.question_core_producer import JobContext
from epick_w4.question_core_runtime import (
    ROOT,
    FileSessionSqsSender,
    PlanPolicies,
    build_producer,
    build_sender,
    main,
    read_plan,
    read_session,
    runtime_contract,
)
from examples.w4_question_core_local import LocalClock, read

KEY = "00000000-0000-4000-8000-000000000001"


def context():
    return JobContext.model_validate_json(
        json.dumps({**read("context.json"), "context_key": KEY, "current_decision_version": 0})
    )


class QuestionCoreRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.plan_path = self.folder / "plan.json"
        self.plan = json.loads(
            (ROOT / "samples/question-core-w1-context-20260919/plan.json").read_bytes()
        )
        self.save_plan()
        self.env = {
            **{
                key: value
                for key, value in os.environ.items()
                if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
            },
            "W4_CT12_PLAN_PATH": str(self.plan_path),
            "W4_OUTBOX_PATH": str(self.folder / "outbox.sqlite3"),
            "W1_W4_CONTEXT_BEARER": "SYNTHETIC-NOT-A-SECRET",
        }

    def save_plan(self):
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")

    def test_plan_template_is_unapproved_and_contains_no_trusted_job_fields(self):
        plan = read_plan(self.plan_path)
        self.assertEqual(plan.purpose, "CT12_SYNTHETIC_ONLY")
        self.assertFalse(plan.entries[0].policy.approved)
        self.assertEqual(
            set(plan.entries[0].model_dump()), {"context_key", "submission_key", "policy"}
        )

    def test_plan_rejects_client_job_ids_wrong_scope_and_duplicate_bindings(self):
        original = deepcopy(self.plan)
        mutations = []
        extra = deepcopy(original)
        extra["entries"][0]["job_id"] = KEY
        mutations.append(extra)
        wrong = deepcopy(original)
        wrong["purpose"] = "REAL"
        mutations.append(wrong)
        duplicate = deepcopy(original)
        duplicate["entries"] *= 2
        mutations.append(duplicate)
        for value in mutations:
            self.plan_path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(QuestionCoreError, "^CORE_POLICY_PLAN_INVALID$"):
                read_plan(self.plan_path)

    def test_policy_is_reloaded_and_operator_can_withdraw_it(self):
        policies = PlanPolicies(self.plan_path)
        self.plan["entries"][0]["policy"]["approved"] = True
        self.save_plan()
        self.assertTrue(policies.load(context()).approved)
        self.plan["entries"][0]["policy"]["approved"] = False
        self.save_plan()
        self.assertFalse(policies.load(context()).approved)
        self.plan_path.unlink()
        with self.assertRaisesRegex(QuestionCoreError, "^CORE_POLICY_PLAN_INVALID$"):
            policies.load(context())

    def test_missing_context_in_plan_is_terminal(self):
        policies = PlanPolicies(self.plan_path)
        other = context().model_copy(update={"context_key": "00000000-0000-4000-8000-000000000002"})
        with self.assertRaisesRegex(QuestionCoreError, "^CORE_POLICY_PLAN_INVALID$"):
            policies.load(other)

    def test_send_is_disabled_without_explicit_configuration_before_any_sdk(self):
        with patch("boto3.client") as client:
            with self.assertRaisesRegex(QuestionCoreError, "^CORE_SEND_DISABLED$"):
                build_sender({}, runtime_contract())
            client.assert_not_called()

    def test_relative_database_path_and_mutable_image_tag_are_rejected(self):
        for change in ({"W4_OUTBOX_PATH": "relative.sqlite3"}, {"W4_IMAGE_REF": "repo:latest"}):
            with self.assertRaisesRegex(QuestionCoreError, "^CORE_RUNTIME_CONFIG_INVALID$"):
                build_producer({**self.env, **change})

    def test_cli_check_has_no_http_sqs_calls_and_hides_bearer(self):
        stream = io.StringIO()
        with (
            patch.dict(os.environ, self.env, clear=True),
            patch("urllib.request.OpenerDirector.open") as http,
            patch("boto3.client") as client,
            redirect_stdout(stream),
        ):
            self.assertEqual(main(["check"]), 0)
        http.assert_not_called()
        client.assert_not_called()
        self.assertEqual(json.loads(stream.getvalue())["status"], "LOCAL_CONFIG_VALID")
        self.assertNotIn(self.env["W1_W4_CONTEXT_BEARER"], stream.getvalue())
        self.assertTrue(Path(self.env["W4_OUTBOX_PATH"]).is_file())

    def test_cli_relay_auth_failure_stops_without_sleep_or_automatic_restart(self):
        stream = io.StringIO()
        with (
            patch.dict(os.environ, self.env, clear=True),
            patch("epick_w4.question_core_runtime.build_sender"),
            patch("epick_w4.question_core_runtime.QuestionCoreRelay") as relay,
            patch("epick_w4.question_core_runtime.time.sleep") as sleep,
            redirect_stdout(stream),
        ):
            relay.return_value.run_once.return_value = {
                "status": "BLOCKED",
                "error_code": "CORE_CONTEXT_AUTH_FAILED",
            }
            self.assertEqual(main(["relay"]), 2)
        sleep.assert_not_called()
        self.assertEqual(relay.return_value.run_once.call_count, 1)

    def test_real_module_entrypoint_can_restart_over_same_local_store(self):
        env = {**os.environ, **self.env}
        for _ in range(2):
            result = subprocess.run(
                [sys.executable, "-m", "epick_w4.question_core_runtime", "check"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            self.assertEqual(json.loads(result.stdout)["status"], "LOCAL_CONFIG_VALID")

    def test_plan_withdrawal_blocks_already_prepared_decision(self):
        from epick_w4.question_core_relay import QuestionCoreRelay
        from examples.w4_question_core_local import LocalAcceptance

        self.plan["entries"][0]["policy"]["approved"] = True
        self.save_plan()
        contexts = MagicMock()
        contexts.load.return_value = context()
        producer, _ = build_producer(self.env, contexts=contexts)
        producer.clock = LocalClock()
        producer.prepare(submission_key="plan-submit", context_key=KEY)
        self.plan_path.unlink()
        sender = LocalAcceptance()
        result = QuestionCoreRelay(
            producer=producer, sender=sender, clock=producer.clock
        ).run_once()
        self.assertEqual(result, {"status": "BLOCKED", "error_code": "CORE_POLICY_PLAN_INVALID"})
        self.assertEqual(sender.bodies, [])


class QuestionCoreCredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "session.json"
        self.credentials = {
            "Version": 1,
            "AccessKeyId": "ASIA" + "A" * 16,
            "SecretAccessKey": "SYNTHETIC-SECRET",
            "SessionToken": "SYNTHETIC-TOKEN",
            "Expiration": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        }
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.credentials), encoding="utf-8")

    def settings(self):
        from epick_w4.question_core_relay import SqsSendSettings

        return SqsSendSettings(
            "https://sqs.ap-northeast-2.amazonaws.com/000000000000/w4-main",
            "arn:aws:sqs:ap-northeast-2:000000000000:w4-main",
            "ap-northeast-2",
            True,
        )

    def test_only_temporary_credentials_are_accepted_and_repr_hides_secrets(self):
        credentials = read_session(self.path)
        for key in ("AccessKeyId", "SecretAccessKey", "SessionToken"):
            self.assertNotIn(self.credentials[key], repr(credentials))
        self.credentials["AccessKeyId"] = "AKIA" + "A" * 16
        self.save()
        with self.assertRaisesRegex(QuestionCoreError, "^CORE_SQS_CREDENTIALS_UNAVAILABLE$"):
            read_session(self.path)

    def test_expired_incomplete_unknown_field_and_missing_file_fail_closed(self):
        original = deepcopy(self.credentials)
        for change in (
            {"Expiration": "2000-01-01T00:00:00Z"},
            {"SessionToken": ""},
            {"owner_role": "forbidden"},
            {"Version": True},
        ):
            self.credentials = {**original, **change}
            self.save()
            with self.assertRaisesRegex(QuestionCoreError, "^CORE_SQS_CREDENTIALS_UNAVAILABLE$"):
                read_session(self.path)
        self.path.unlink()
        with self.assertRaisesRegex(QuestionCoreError, "^CORE_SQS_CREDENTIALS_UNAVAILABLE$"):
            read_session(self.path)

    def test_each_send_reads_new_session_and_never_uses_default_credential_chain(self):
        sender = FileSessionSqsSender(
            contract=runtime_contract(), settings=self.settings(), session_path=self.path
        )
        with (
            patch("boto3.client") as client,
            patch("epick_w4.question_core_runtime.SqsSendOnly") as transport,
        ):
            transport.return_value.send.return_value = "synthetic-broker-id"
            sender.send("synthetic-body")
            self.credentials["SessionToken"] = "SYNTHETIC-ROTATED-TOKEN"
            self.save()
            sender.send("synthetic-body")
        self.assertEqual(client.call_count, 2)
        first, second = (call.kwargs for call in client.call_args_list)
        self.assertEqual(first["aws_session_token"], "SYNTHETIC-TOKEN")
        self.assertEqual(second["aws_session_token"], "SYNTHETIC-ROTATED-TOKEN")
        self.assertEqual(first["aws_access_key_id"], self.credentials["AccessKeyId"])
        self.assertEqual(first["aws_secret_access_key"], self.credentials["SecretAccessKey"])
        self.assertEqual(first["config"].retries["total_max_attempts"], 1)
        self.assertTrue(first["config"].ignore_configured_endpoint_urls)
        self.assertEqual(client.return_value.close.call_count, 2)

    def test_missing_credentials_cannot_fall_back_to_sdk_env_or_imds(self):
        self.path.unlink()
        sender = FileSessionSqsSender(
            contract=runtime_contract(), settings=self.settings(), session_path=self.path
        )
        with patch("boto3.client") as client:
            with self.assertRaisesRegex(QuestionCoreError, "^CORE_SQS_CREDENTIALS_UNAVAILABLE$"):
                sender.send("synthetic-body")
        client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
