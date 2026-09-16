from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from app.main import create_app


def _load(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value: dict[str, Any] = json.load(stream)
    return value


def _validator(contract_root: Path, relative_schema_path: str) -> Draft202012Validator:
    schema = _load(contract_root / relative_schema_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _private_message_errors(contract_root: Path, message: dict[str, Any]) -> list[str]:
    envelope_validator = _validator(contract_root, "w1/v1/private-message-envelope.schema.json")
    errors = [error.message for error in envelope_validator.iter_errors(message)]
    if errors:
        return errors

    message_type = message["message_type"]
    producer = message["producer"]
    payload = message["payload"]
    if message_type == "w2.collection.result.v1":
        if producer != "w2":
            errors.append("w2.collection.result.v1 must be produced by w2")
        payload_validator = _validator(contract_root, "w2/v1/source-collection.result.schema.json")
        errors.extend(error.message for error in payload_validator.iter_errors(payload))
        return errors

    payload_validator = _validator(contract_root, "w1/v1/core-source-decision.schema.json")
    errors.extend(error.message for error in payload_validator.iter_errors(payload))
    if not errors:
        expected_producer = {
            "COMPANY_KNOWLEDGE": "w3",
            "QUESTION_MATCHING": "w4",
        }[payload["decision_scope"]]
        if producer != expected_producer:
            errors.append(
                f"{payload['decision_scope']} must be produced by {expected_producer}"
            )
    return errors


@pytest.mark.parametrize(
    ("schema_path", "fixture_path"),
    [
        (
            "common/v1/event-envelope.schema.json",
            "fixtures/v1/common/source-restriction-changed.json",
        ),
        (
            "common/v1/event-envelope.schema.json",
            "fixtures/v1/common/source-restriction-replay.json",
        ),
        (
            "common/v1/event-envelope.schema.json",
            "fixtures/v1/common/source-revision-gap.json",
        ),
        ("w1/v1/job.schema.json", "fixtures/v1/w1/accepted-job.json"),
        ("w1/v1/job.schema.json", "fixtures/v1/w1/cancel-requested-job.json"),
        ("w1/v1/job-command.schema.json", "fixtures/v1/w1/job-command.json"),
        ("w1/v1/checkpoint.schema.json", "fixtures/v1/w1/checkpoint.json"),
        ("w1/v1/deletion-command.schema.json", "fixtures/v1/w1/deletion-command.json"),
        (
            "w1/v1/private-command-lookup-request.schema.json",
            "fixtures/v1/w1/private-command-lookup-request.json",
        ),
        (
            "w1/v1/private-command-lookup-response.schema.json",
            "fixtures/v1/w1/private-command-lookup-available.json",
        ),
        (
            "w1/v1/private-command-lookup-response.schema.json",
            "fixtures/v1/w1/private-command-lookup-not-found.json",
        ),
        (
            "w1/v1/private-command-lookup-response.schema.json",
            "fixtures/v1/w1/private-command-lookup-stale-fence.json",
        ),
        (
            "w1/v1/private-command-lookup-response.schema.json",
            "fixtures/v1/w1/private-command-lookup-stale-deletion-epoch.json",
        ),
        (
            "w1/v1/private-command-lookup-response.schema.json",
            "fixtures/v1/w1/private-command-lookup-deleted.json",
        ),
        (
            "w1/v1/private-command-lookup-response.schema.json",
            "fixtures/v1/w1/private-command-lookup-invalidated.json",
        ),
        (
            "w1/v1/private-command-lookup-response.schema.json",
            "fixtures/v1/w1/private-command-lookup-expired.json",
        ),
        (
            "w1/v1/private-w2-command-dispatch.schema.json",
            "fixtures/v1/w1/private-w2-command-dispatch.json",
        ),
        (
            "w1/v1/private-error.schema.json",
            "fixtures/v1/w1/private-error-unauthenticated.json",
        ),
        (
            "w1/v1/private-error.schema.json",
            "fixtures/v1/w1/private-error-forbidden.json",
        ),
        (
            "w1/v1/private-delivery-receipt.schema.json",
            "fixtures/v1/w1/private-delivery-receipt-applied.json",
        ),
        (
            "w1/v1/private-delivery-receipt.schema.json",
            "fixtures/v1/w1/private-delivery-receipt-duplicate.json",
        ),
        (
            "w1/v1/private-delivery-receipt.schema.json",
            "fixtures/v1/w1/private-delivery-receipt-stale-discarded.json",
        ),
        (
            "w1/v1/private-delivery-receipt.schema.json",
            "fixtures/v1/w1/private-delivery-receipt-rejected-schema.json",
        ),
        (
            "w1/v1/private-delivery-receipt.schema.json",
            "fixtures/v1/w1/private-delivery-receipt-rejected-principal.json",
        ),
        (
            "w1/v1/private-delivery-receipt.schema.json",
            "fixtures/v1/w1/private-delivery-receipt-retryable-infra.json",
        ),
        (
            "w1/v1/public-job-action-request.schema.json",
            "fixtures/v1/w1/public-job-action-retry-request.json",
        ),
        (
            "w1/v1/public-job-retry-request.schema.json",
            "fixtures/v1/w1/public-job-retry-request.json",
        ),
        (
            "w1/v1/public-job-action-accepted-http-response.schema.json",
            "fixtures/v1/w1/public-job-action-retry-accepted-http-response.json",
        ),
        (
            "w1/v1/public-api-error.schema.json",
            "fixtures/v1/w1/public-job-error-resource-not-found.json",
        ),
        (
            "w1/v1/public-api-error.schema.json",
            "fixtures/v1/w1/public-job-error-stale-input.json",
        ),
        (
            "w1/v1/public-api-error.schema.json",
            "fixtures/v1/w1/public-job-error-action-not-allowed.json",
        ),
        (
            "w1/v1/public-api-error.schema.json",
            "fixtures/v1/w1/public-job-error-idempotency-conflict.json",
        ),
        (
            "w2/v1/source-collection.command.schema.json",
            "fixtures/v1/w2/source-collection-command.json",
        ),
        (
            "w2/v1/source-collection.result.schema.json",
            "fixtures/v1/w2/source-collection-result-complete.json",
        ),
        (
            "w2/v1/source-collection.result.schema.json",
            "fixtures/v1/w2/source-collection-result-partial.json",
        ),
        (
            "w2/v1/source-collection.result.schema.json",
            "fixtures/v1/w2/source-collection-result-failure.json",
        ),
    ],
)
def test_versioned_contract_fixtures_match_their_schema(
    contract_root: Path, schema_path: str, fixture_path: str
) -> None:
    validator = _validator(contract_root, schema_path)

    assert list(validator.iter_errors(_load(contract_root / fixture_path))) == []


@pytest.mark.parametrize(
    "fixture_path",
    [
        "fixtures/v1/w2/source-event-version-available.json",
        "fixtures/v1/w2/source-event-version-partial.json",
        "fixtures/v1/w2/source-event-observation-changed.json",
        "fixtures/v1/w2/source-event-restriction-changed.json",
    ],
)
def test_w2_public_source_events_match_the_common_envelope_and_w2_payload(
    contract_root: Path, fixture_path: str
) -> None:
    event = _load(contract_root / fixture_path)
    envelope_validator = _validator(contract_root, "common/v1/event-envelope.schema.json")
    payload_validator = _validator(contract_root, "w2/v1/source-event-payload.schema.json")

    assert list(envelope_validator.iter_errors(event)) == []
    assert list(payload_validator.iter_errors(event["payload"])) == []


@pytest.mark.parametrize(
    "fixture_path",
    [
        "fixtures/v1/w1/private-w2-collection-result-complete.json",
        "fixtures/v1/w1/private-core-source-decision-company.json",
        "fixtures/v1/w1/private-core-source-decision-question.json",
    ],
)
def test_w1_private_message_fixtures_match_their_bound_contracts(
    contract_root: Path, fixture_path: str
) -> None:
    assert _private_message_errors(contract_root, _load(contract_root / fixture_path)) == []


def test_private_command_lookup_available_response_contains_a_w2_command(
    contract_root: Path,
) -> None:
    response = _load(contract_root / "fixtures/v1/w1/private-command-lookup-available.json")
    command_validator = _validator(contract_root, "w2/v1/source-collection.command.schema.json")

    assert list(command_validator.iter_errors(response["command"])) == []


def test_w1_dispatch_binds_the_w2_payload_to_the_exact_lookup_request(
    contract_root: Path,
) -> None:
    dispatch = _load(contract_root / "fixtures/v1/w1/private-w2-command-dispatch.json")
    payload = dispatch["payload"]
    lookup_request = dispatch["lookup_request"]

    payload_validator = _validator(contract_root, "w2/v1/source-collection.command.schema.json")
    lookup_validator = _validator(contract_root, "w1/v1/private-command-lookup-request.schema.json")
    assert list(payload_validator.iter_errors(payload)) == []
    assert list(lookup_validator.iter_errors(lookup_request)) == []

    assert dispatch["message_id"] == payload["command_id"] == lookup_request["command_id"]
    # W2 owns the historical string field; W1 lookup owns the canonical integer.
    # The dispatch adapter allows only canonical base-10 rendering, never e.g. "01".
    assert payload["execution_fence"] == str(lookup_request["execution_fence"])
    assert payload["owner_deletion_epoch"] == lookup_request["owner_deletion_epoch"]

    core_decision = _load(
        contract_root / "fixtures/v1/w1/private-core-source-decision-company.json"
    )
    pin = dispatch["core_decision_pin"]
    assert pin["origin_message_id"] == core_decision["message_id"]
    for field in (
        "decision_id",
        "decision_scope",
        "company_id",
        "question_version_id",
        "source_id",
        "analysis_input_version",
        "decision_version",
        "is_core",
        "decision_code",
        "reason_code",
    ):
        assert pin[field] == core_decision["payload"][field]


def test_w1_private_result_deduplication_and_core_decision_scope_are_explicit(
    contract_root: Path,
) -> None:
    w2_result = _load(contract_root / "fixtures/v1/w1/private-w2-collection-result-complete.json")
    company_decision = _load(
        contract_root / "fixtures/v1/w1/private-core-source-decision-company.json"
    )
    question_decision = _load(
        contract_root / "fixtures/v1/w1/private-core-source-decision-question.json"
    )

    assert w2_result["channel"] == "w1.private.w2.collection-result.v1"
    assert w2_result["message_id"]
    assert company_decision["channel"] == "w1.private.w3.core-source-decision.v1"
    assert question_decision["channel"] == "w1.private.w4.core-source-decision.v1"
    for decision_message in (company_decision, question_decision):
        decision = decision_message["payload"]
        assert decision["decision_id"]
        assert decision["analysis_input_version"]
        assert decision["decision_version"] >= 1
        assert decision["reason_code"]


def test_public_job_action_contract_matches_the_live_openapi_surface(contract_root: Path) -> None:
    accepted = _load(
        contract_root / "fixtures/v1/w1/public-job-action-retry-accepted-http-response.json"
    )
    job_response_validator = _validator(contract_root, "w1/v1/public-job-response.schema.json")
    assert list(job_response_validator.iter_errors(accepted["body"])) == []
    assert accepted["headers"]["Location"].endswith(accepted["body"]["id"])

    schema = create_app().openapi()
    paths = schema["paths"]
    action = paths["/api/v1/jobs/{job_id}/actions"]["post"]
    retry = paths["/api/v1/jobs/{job_id}/retry"]["post"]
    assert "202" in action["responses"]
    assert "202" in retry["responses"]
    for operation in (action, retry):
        assert {"404", "409"} <= set(operation["responses"])


def test_public_job_error_status_mapping_is_stable(contract_root: Path) -> None:
    expected_statuses = {
        "public-job-error-resource-not-found.json": (404, "RESOURCE_NOT_FOUND"),
        "public-job-error-stale-input.json": (409, "STALE_INPUT"),
        "public-job-error-action-not-allowed.json": (409, "ACTION_NOT_ALLOWED"),
        "public-job-error-idempotency-conflict.json": (409, "IDEMPOTENCY_CONFLICT"),
    }
    for fixture_name, (_, code) in expected_statuses.items():
        payload = _load(contract_root / "fixtures/v1/w1" / fixture_name)
        assert payload["error"]["code"] == code
    assert {status for status, _ in expected_statuses.values()} == {404, 409}


@pytest.mark.parametrize(
    ("schema_path", "fixture_path"),
    [
        (
            "common/v1/event-envelope.schema.json",
            "fixtures/v1/common/invalid-public-private-field.json",
        ),
        ("w1/v1/job.schema.json", "fixtures/v1/w1/invalid-job-status.json"),
        (
            "w1/v1/private-message-envelope.schema.json",
            "fixtures/v1/w1/invalid-private-envelope-public-scope.json",
        ),
        (
            "w1/v1/core-source-decision.schema.json",
            "fixtures/v1/w1/invalid-core-source-decision-code.json",
        ),
    ],
)
def test_prohibited_or_unknown_contract_values_are_rejected(
    contract_root: Path, schema_path: str, fixture_path: str
) -> None:
    validator = _validator(contract_root, schema_path)

    assert list(validator.iter_errors(_load(contract_root / fixture_path)))


@pytest.mark.parametrize(
    "fixture_path",
    [
        "fixtures/v1/w2/invalid-source-event-private-field.json",
        "fixtures/v1/w2/invalid-source-event-transport-fields.json",
    ],
)
def test_w2_public_source_events_reject_private_or_transport_fields(
    contract_root: Path, fixture_path: str
) -> None:
    event = _load(contract_root / fixture_path)
    envelope_validator = _validator(contract_root, "common/v1/event-envelope.schema.json")
    payload_validator = _validator(contract_root, "w2/v1/source-event-payload.schema.json")

    assert list(envelope_validator.iter_errors(event)) or list(
        payload_validator.iter_errors(event["payload"])
    )


def test_w1_private_core_decision_rejects_wrong_producer_for_scope(contract_root: Path) -> None:
    fixture = _load(
        contract_root / "fixtures/v1/w1/invalid-private-core-decision-producer-scope.json"
    )

    assert _private_message_errors(contract_root, fixture)


def test_w2_import_is_pinned_to_the_observed_runtime_contract(contract_root: Path) -> None:
    manifest = _load(contract_root / "w2/v1/import-manifest.json")

    assert manifest["contract_status"] == (
        "PRIVATE_CONTRACT_ADOPTED_PUBLIC_SOURCE_EVENT_COMPATIBILITY_PENDING"
    )
    assert manifest["declared_runtime_schema_versions"] == ["w2.collection.v1", "w2.source.v1"]
    assert manifest["artifact_authority"] == "W2"
    assert manifest["source_commit"] is None
    assert manifest["source_commit_status"] == "NOT_PROVIDED_WITH_W2_ARTIFACT_PACKAGE"
    assert set(manifest["artifacts"]) == {
        "source-collection.command.schema.json",
        "source-collection.result.schema.json",
        "source-event-payload.schema.json",
    }
    for name, artifact in manifest["artifacts"].items():
        actual_sha256 = hashlib.sha256((contract_root / "w2/v1" / name).read_bytes()).hexdigest()
        assert actual_sha256 == artifact["sha256"]

    fixture_root = contract_root / "fixtures/v1/w2"
    for name, expected_sha256 in manifest["fixtures"].items():
        actual_sha256 = hashlib.sha256((fixture_root / name).read_bytes()).hexdigest()
        assert actual_sha256 == expected_sha256
