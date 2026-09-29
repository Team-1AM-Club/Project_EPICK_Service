"""Exercise W4 preparation and W1 runtime boundaries locally; no joint CT-12 claim."""
import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    backend = args.service_root.resolve() / "backend"
    args.output_dir.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(backend))
    # This URI is intentionally unusable; all actual database work uses SQLite.
    os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://synthetic@127.0.0.1:1/unused")
    import pytest
    from jsonschema import Draft202012Validator, FormatChecker
    from app.models.jobs import JobCommand, OutboxMessage
    from app.runtime.outbox_relay import OutboxRelay
    from epick_w4.core_decision import CoreDecisionError, check_current, prepare_decision

    test_path = backend / "tests/runtime/test_question_core_binding.py"
    spec = importlib.util.spec_from_file_location("w1_question_local_harness", test_path)
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    checks = {}
    result = {
        "status": "RUNNING", "scope": "LOCAL_PARTIAL_INTEGRATION",
        "team_policy_adoption": "PENDING", "joint_ct12": "NOT_RUN",
        "real_postgresql": "NOT_RUN", "real_queue": "NOT_RUN",
        "real_model_calls": 0, "external_messages_sent": 0,
        "mocked_boundaries": ["W4 inbound acceptance and durable decision persistence",
                              "new W1 JobCommand/Outbox persistence and queue delivery"],
        "actual_boundaries": ["W4 codec/current-context guard", "W1 pinned JSON schemas",
                              "W1 company resolver SQL on SQLite", "W1 JobWorker command builder",
                              "W1 OutboxRelay validation/serialization", "W1 private lookup ASGI route"],
    }
    with contextmanager(harness.local.__wrapped__)() as local, pytest.MonkeyPatch.context() as patch:
        context = json.loads((ROOT / "samples/core-decision/context.json").read_text(encoding="utf-8"))
        context["current_decision_version"] = 0
        prepared = prepare_decision(
            context, authenticated_owner_id=context["owner_id"],
            message_id="30000000-0000-4000-8000-000000000007",
            decision_id=local.pin["decision_id"], occurred_at="2026-09-18T07:00:00Z",
            decision_version=local.pin["decision_version"], decision_code="CORE_REQUIRED",
            reason_code="QUESTION_EVIDENCE_REQUIRED")
        message = check_current(prepared, current_context=context,
                                authenticated_owner_id=context["owner_id"])
        for name, value in (("private-message-envelope", message),
                            ("core-source-decision", message["payload"])):
            schema = harness.load("w1/v1/" + name + ".schema.json")
            Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
        checks["w4_message_accepted_by_current_w1_schemas"] = True
        # Explicit synthetic acceptance boundary. There is no W4 W1 inbox consumer yet.
        local.pin.clear()
        local.pin.update({k: v for k, v in message["payload"].items() if k != "schema_version"})
        local.pin["origin_message_id"] = message["message_id"]
        ok, port, job, blocked = harness.dispatch_through_worker(local, patch)
        try:
            checks["w1_worker_builds_question_command"] = ok and not blocked
            command = next(row for row in port.added if isinstance(row, JobCommand))
            outbox = next(row for row in port.added if isinstance(row, OutboxMessage))
            OutboxRelay._validate_command_payload(session=port, message=outbox, command=command,
                                                 job=job, outbox_payload=outbox.payload)
            dispatch = json.loads(OutboxRelay._serialize_private_dispatch(
                message=outbox, command=command, job=job,
                issued_at=datetime(2026, 9, 18, 7, 0, 1, tzinfo=timezone.utc)))
            checks["w1_relay_validates_and_serializes"] = True
            checks["question_pin_stays_null"] = dispatch["core_decision_pin"]["company_id"] is None
            checks["collection_company_is_authoritative"] = dispatch["payload"]["company_id"] == context["collection_company_id"]
            # Persist only the projection consumed by the real lookup route, in SQLite.
            with local.engine.begin() as connection:
                connection.execute(local.tables["job_commands"].update().values(
                    id=UUID(dispatch["message_id"]), payload=deepcopy(command.payload)))
            local.dispatch["lookup_request"] = dispatch["lookup_request"]
            available = harness.lookup(local).json()
            checks["w1_lookup_returns_exact_command"] = (
                available["status"] == "AVAILABLE" and available["command"] == dispatch["payload"])
            retry = check_current(prepared, current_context={**context, "current_decision_version": 1},
                                  authenticated_owner_id=context["owner_id"])
            checks["w4_retry_preserves_message"] = retry == message
            revoked = None
            try:
                check_current(prepared, current_context={**context, "processing_allowed": False},
                              authenticated_owner_id=context["owner_id"])
            except CoreDecisionError as error:
                revoked = error.code
            checks["w4_revocation_stops_retry"] = revoked == "CORE_CONTEXT_NOT_CURRENT"
            harness.change(local, "project_questions", "current_version_id", harness.uid(99))
            stale = harness.lookup(local).json()
            checks["w1_changed_question_blocks_lookup"] = (
                stale["command"] is None and stale["reason_code"] == "COMMAND_BINDING_INVALID")
            result.update(w4_message=message, w1_dispatch=dispatch, lookup_before=available,
                          lookup_after=stale, w4_revoked_error=revoked)
        finally:
            port.session.close()
    result["checks"] = checks
    result["status"] = "PASSED_LOCAL_PARTIAL" if all(checks.values()) else "FAILED"
    paths = [ROOT / "epick_w4/core_decision.py", test_path,
             *(backend / "app/runtime" / name for name in (
                 "core_decision_binding.py", "question_core_binding.py", "workers.py",
                 "outbox_relay.py", "lookup_adapter.py"))]
    result["source_sha256"] = {str(path.relative_to(ROOT if path.is_relative_to(ROOT) else backend)):
                               hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    (args.output_dir / "local-integration.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "passed_checks": sum(checks.values()),
                      "joint_ct12": result["joint_ct12"], "mocked_boundaries": result["mocked_boundaries"]}))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
