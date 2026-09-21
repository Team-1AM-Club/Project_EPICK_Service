from __future__ import annotations

import json
from pathlib import Path

from app.runtime.w1_w3_evidence import sanitize_public_evidence

EVIDENCE = (
    Path(__file__).parents[3]
    / "specs"
    / "007-w1-w3-runtime-integration"
    / "evidence"
    / "m3-w1-side-result.json"
)
OPERATIONS_EVIDENCE = EVIDENCE.with_name("m3-deletion-operations.json")


def test_m3_w1_side_evidence_is_count_only_and_closes_actual_aws_gate() -> None:
    payload = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    allowed_fields = {
        "schema_version",
        "status",
        "scope",
        "test_runs",
        "name",
        "passed",
        "failed",
        "exit_code",
        "lint",
        "compose",
        "w1_template_parsed",
        "w3_template_parsed",
        "actual_containers_started",
        "verified_counts",
        "receipt_outcomes",
        "retention_operations",
        "dedicated_command_routes",
        "receipt_consumers",
        "external_gates",
        "id",
        "state",
        "reason",
        "references",
    }

    sanitized = sanitize_public_evidence(payload, allowed_fields=allowed_fields)

    assert sanitized == payload
    assert payload["status"] == "W1_W3_ACTUAL_AWS_E2E_VERIFIED"
    assert payload["compose"]["actual_containers_started"] is True
    assert {gate["id"] for gate in payload["external_gates"]} == {
        "ACTUAL_PRIVATE_QUEUE_BINDING"
    }
    assert {gate["state"] for gate in payload["external_gates"]} == {"VERIFIED"}


def test_m3_actual_aws_evidence_is_count_only_and_reconciles_duplicates() -> None:
    payload = json.loads(OPERATIONS_EVIDENCE.read_text(encoding="utf-8"))
    allowed_fields = {
        "schema_version",
        "status",
        "environment",
        "executed_on",
        "policy_revision",
        "images",
        "w1_digest",
        "w3_digest",
        "deployment_inputs",
        "private_queue_urls_injected_outside_git",
        "stable_role_ids_injected_outside_git",
        "credentials_stored_in_git",
        "database",
        "migration_head",
        "deleter_policies",
        "migration_blockers",
        "initial_delivery",
        "synthetic_owners",
        "deletion_targets",
        "commands_pending_before_relay",
        "commands_claimed",
        "commands_published",
        "w3_commands_received",
        "w3_commands_applied",
        "w3_receipts_acknowledged",
        "w1_receipts_received",
        "w1_receipts_applied",
        "requests_completed",
        "w3_targets_acknowledged",
        "restart_duplicate_delivery",
        "expired_relay_leases",
        "commands_republished",
        "w3_duplicate_commands",
        "w3_duplicate_receipts_acknowledged",
        "w1_duplicate_receipts",
        "additional_logical_receipts",
        "additional_runtime_mutations",
        "final_state",
        "logical_receipts",
        "published_commands",
        "relay_attempts_total",
        "w3_transport_handoffs",
        "pending_w3_deliveries",
        "empty_command_poll_retry_scheduled",
        "empty_receipt_poll_retry_scheduled",
        "verified_corrections",
    }

    sanitized = sanitize_public_evidence(payload, allowed_fields=allowed_fields)

    assert sanitized == payload
    assert payload["status"] == "VERIFIED"
    assert payload["initial_delivery"]["commands_published"] == 2
    assert payload["restart_duplicate_delivery"]["w3_duplicate_commands"] == 2
    assert payload["restart_duplicate_delivery"]["additional_runtime_mutations"] == 0
    assert payload["final_state"]["logical_receipts"] == 2
    assert payload["final_state"]["relay_attempts_total"] == 4
    assert payload["final_state"]["pending_w3_deliveries"] == 0
