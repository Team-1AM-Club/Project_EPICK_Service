"""Offline W1 wire checks using pinned synthetic originals and labeled derived variants."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from epick_engine.source_collection.contracts import CollectionCommand, CollectionResult

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "w1_private_contract"
OTHER_ID = "90000000-0000-4000-8000-000000000001"
LOOKUP_STATUSES = (
    "available",
    "not-found",
    "stale-fence",
    "stale-deletion-epoch",
    "deleted",
    "invalidated",
    "expired",
)


def _load(name: str) -> dict[str, Any]:
    with (FIXTURE_DIR / name).open(encoding="utf-8") as stream:
        value: dict[str, Any] = json.load(stream)
    return value


def _codec() -> ModuleType:
    # An assertion (rather than an import-time error) demonstrates the missing feature in RED.
    name = "epick_engine.source_collection.w1_transport"
    assert importlib.util.find_spec(name) is not None, "standalone W1 wire codec is missing"
    return importlib.import_module(name)


def _assert_schema(value: dict[str, Any], name: str) -> None:
    validator = Draft202012Validator(_load(name), format_checker=FormatChecker())
    assert list(validator.iter_errors(value)) == []


def _set(value: dict[str, Any], path: str, replacement: object) -> None:
    parts = path.split(".")
    target: Any = value
    for part in parts[:-1]:
        target = target[int(part) if isinstance(target, list) else part]
    key = int(parts[-1]) if isinstance(target, list) else parts[-1]
    target[key] = replacement


def _uuid_variant(value: dict[str, Any], path: str, form: str) -> None:
    target: Any = value
    for part in path.split("."):
        target = target[int(part) if isinstance(target, list) else part]
    replacement = target.replace("-", "") if form == "compact" else f"urn:uuid:{target}"
    _set(value, path, replacement)


def _assert_raw_schema_rejects(value: dict[str, Any], name: str) -> None:
    validator = Draft202012Validator(_load(name), format_checker=FormatChecker())
    assert list(validator.iter_errors(value))


def _assert_raw_datetime_schema_rejects_when_supported(value: dict[str, Any], name: str) -> None:
    # date-time depends on an optional jsonschema format package absent in the current venv.
    # These tests always assert codec rejection of known-invalid raw literals below; this
    # conditional is supplementary, not evidence of W1 schema date-time verification here.
    checker = FormatChecker()
    if "date-time" in checker.checkers:
        assert list(Draft202012Validator(_load(name), format_checker=checker).iter_errors(value))


def test_offline_snapshots_keep_pinned_revision_and_lf_hashes() -> None:
    manifest = _load("manifest.json")
    assert manifest["revision"] == "afec08a9602132e5e433b523b0b6804850440524"
    assert manifest["repository"] == "Team-1AM-Club/Project_EPICK_Service"
    for entry in manifest["files"]:
        data = (FIXTURE_DIR / entry["file"]).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(data).hexdigest() == entry["sha256"], entry["file"]
        if entry["file"].endswith(".schema.json"):
            Draft202012Validator.check_schema(json.loads(data))


def test_dispatch_preserves_pinned_command_and_core_pin_without_normalizing_owner() -> None:
    original = _load("private-w2-command-dispatch.json")
    dispatch = _codec().parse_w1_dispatch(original)

    assert isinstance(dispatch.payload, CollectionCommand)
    assert dispatch.payload.core_source_decision.decided_by == "W3"
    assert dispatch.core_decision_pin.model_dump(mode="json") == original["core_decision_pin"]
    assert dispatch.lookup_request.model_dump(mode="json") == original["lookup_request"]
    assert dispatch.message_id == dispatch.payload.command_id == dispatch.lookup_request.command_id
    _assert_schema(dispatch.model_dump(mode="json"), "private-w2-command-dispatch.schema.json")
    _assert_schema(
        dispatch.payload.model_dump(mode="json"), "source-collection.command.schema.json"
    )


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        ("message_id", OTHER_ID),
        ("lookup_request.command_id", OTHER_ID),
        ("payload.command_id", OTHER_ID),
        ("lookup_request.execution_fence", 2),
        ("lookup_request.owner_deletion_epoch", 1),
        ("payload.owner_deletion_epoch", 1),
        ("payload.execution_fence", "01"),
        ("payload.execution_fence", "+1"),
        ("payload.execution_fence", "1.0"),
        ("payload.execution_fence", " 1"),
        ("payload.execution_fence", "١"),
        ("payload.execution_fence", 1),
        ("payload.owner_deletion_epoch", True),
        ("payload.owner_deletion_epoch", "0"),
        ("schema_version", "unknown"),
        ("message_type", "w2.collection.result.v1"),
        ("producer", "w2"),
        ("visibility_scope", "PUBLIC"),
        ("payload_schema_version", "unknown"),
        ("occurred_at", "2026-09-16T02:00:00"),
        ("payload.unexpected", "synthetic-extra"),
        ("unexpected", "synthetic-extra"),
        ("core_decision_pin.unexpected", "synthetic-extra"),
        ("core_decision_pin.decision_scope", "STANDALONE"),
        ("core_decision_pin.company_id", None),
        ("core_decision_pin.question_version_id", OTHER_ID),
        ("core_decision_pin.is_core", "true"),
        ("core_decision_pin.is_core", False),
        ("core_decision_pin.decision_version", True),
        ("core_decision_pin.reason_code", "unsafe reason text"),
    ],
)
def test_derived_dispatch_rejects_malformed_wire_or_identity_mismatch(
    path: str, replacement: object
) -> None:
    derived = _load("private-w2-command-dispatch.json")
    _set(derived, path, replacement)
    codec = _codec()
    with pytest.raises(codec.W1WireContractError):
        codec.parse_w1_dispatch(derived)


def test_question_scoped_pin_accepts_uuid_command_company_without_binding_to_null_pin() -> None:
    derived = _load("private-w2-command-dispatch.json")
    derived["core_decision_pin"].update(
        decision_scope="QUESTION_MATCHING", company_id=None, question_version_id=OTHER_ID
    )
    derived["payload"]["core_source_decision"]["decided_by"] = "W4"
    codec = _codec()
    pin = codec._parse_wire(derived["core_decision_pin"], codec.CoreDecisionPin, label="Core pin")
    assert pin.question_version_id == UUID(OTHER_ID)
    assert pin.company_id is None
    _assert_schema(derived, "private-w2-command-dispatch.schema.json")
    _assert_schema(derived["payload"], "source-collection.command.schema.json")
    dispatch = codec.parse_w1_dispatch(derived)
    assert dispatch.payload.company_id == UUID("10000000-0000-4000-8000-000000000004")
    assert dispatch.core_decision_pin == pin


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        ("payload.source_id", OTHER_ID),
        ("payload.core_source_decision.decided_by", "W3"),
        ("payload.core_source_decision.rationale", "DIFFERENT_REASON"),
    ],
)
def test_question_scoped_dispatch_rejects_non_company_binding_mismatch(
    path: str, replacement: object
) -> None:
    derived = _load("private-w2-command-dispatch.json")
    derived["core_decision_pin"].update(
        decision_scope="QUESTION_MATCHING", company_id=None, question_version_id=OTHER_ID
    )
    derived["payload"]["core_source_decision"]["decided_by"] = "W4"
    _set(derived, path, replacement)
    codec = _codec()
    with pytest.raises(codec.W1WireContractError):
        codec.parse_w1_dispatch(derived)


def test_question_scoped_available_lookup_still_requires_same_command_company() -> None:
    derived = _load("private-w2-command-dispatch.json")
    derived["core_decision_pin"].update(
        decision_scope="QUESTION_MATCHING", company_id=None, question_version_id=OTHER_ID
    )
    derived["payload"]["core_source_decision"]["decided_by"] = "W4"
    codec = _codec()
    dispatch = codec.parse_w1_dispatch(derived)
    raw = _load("private-command-lookup-available.json")
    raw.update(
        command_id=str(dispatch.payload.command_id),
        command=dispatch.payload.model_dump(mode="json"),
    )
    raw["command"]["company_id"] = OTHER_ID
    response = codec.decode_lookup_response(raw, http_status=200, request=dispatch.lookup_request)
    with pytest.raises(codec.W1WireContractError):
        codec.validate_dispatch_lookup(dispatch, response)


def test_question_scoped_forged_non_null_pin_company_is_rejected_on_revalidation() -> None:
    derived = _load("private-w2-command-dispatch.json")
    derived["core_decision_pin"].update(
        decision_scope="QUESTION_MATCHING", company_id=None, question_version_id=OTHER_ID
    )
    derived["payload"]["core_source_decision"]["decided_by"] = "W4"
    codec = _codec()
    dispatch = codec.parse_w1_dispatch(derived)
    forged = dispatch.model_copy(
        update={
            "core_decision_pin": dispatch.core_decision_pin.model_copy(
                update={"company_id": dispatch.payload.company_id}
            )
        }
    )
    raw = _load("private-command-lookup-available.json")
    raw.update(
        command_id=str(dispatch.payload.command_id),
        command=dispatch.payload.model_dump(mode="json"),
    )
    response = codec.decode_lookup_response(raw, http_status=200, request=dispatch.lookup_request)
    with pytest.raises(codec.W1WireContractError):
        codec.validate_dispatch_lookup(forged, response)


def test_lookup_request_round_trip_keeps_integer_fence_and_zero_epoch() -> None:
    original = _load("private-command-lookup-request.json")
    request = _codec().parse_lookup_request(original)
    assert type(request.execution_fence) is int
    assert type(request.owner_deletion_epoch) is int
    assert request.model_dump(mode="json") == original
    _assert_schema(request.model_dump(mode="json"), "private-command-lookup-request.schema.json")


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        ("execution_fence", True),
        ("execution_fence", "1"),
        ("execution_fence", 1.0),
        ("execution_fence", 0),
        ("execution_fence", -1),
        ("owner_deletion_epoch", False),
        ("owner_deletion_epoch", "0"),
        ("owner_deletion_epoch", 0.0),
        ("owner_deletion_epoch", -1),
        ("schema_version", "unknown"),
        ("command_id", "not-a-uuid"),
        ("unexpected", "synthetic-extra"),
    ],
)
def test_derived_lookup_request_rejects_coercion_and_unknown_fields(
    path: str, replacement: object
) -> None:
    derived = _load("private-command-lookup-request.json")
    _set(derived, path, replacement)
    codec = _codec()
    with pytest.raises(codec.W1WireContractError):
        codec.parse_lookup_request(derived)


@pytest.mark.parametrize("status", LOOKUP_STATUSES)
def test_lookup_statuses_expose_a_command_only_for_available(status: str) -> None:
    original = _load(f"private-command-lookup-{status}.json")
    codec = _codec()
    request = codec.parse_lookup_request(_load("private-command-lookup-request.json"))
    response = codec.decode_lookup_response(original, http_status=200, request=request)

    assert response.status == original["status"]
    assert response.reason_code == original["reason_code"]
    assert (response.command is not None) == (status == "available")
    _assert_schema(response.model_dump(mode="json"), "private-command-lookup-response.schema.json")
    if response.command is not None:
        assert isinstance(response.command, CollectionCommand)
        _assert_schema(
            response.command.model_dump(mode="json"), "source-collection.command.schema.json"
        )


def test_pinned_dispatch_and_available_lookup_preserve_the_same_complete_command() -> None:
    codec = _codec()
    dispatch = codec.parse_w1_dispatch(_load("private-w2-command-dispatch.json"))
    response = codec.decode_lookup_response(
        _load("private-command-lookup-available.json"),
        http_status=200,
        request=dispatch.lookup_request,
    )
    assert response.command is not None
    assert hasattr(codec, "validate_dispatch_lookup"), "dispatch/lookup binding is missing"
    assert codec.validate_dispatch_lookup(dispatch, response) is None
    assert dispatch.payload.core_source_decision.decided_by == "W3"
    assert response.command.core_source_decision.decided_by == "W3"
    assert dispatch.payload == response.command
    assert dispatch.payload.model_dump(mode="json") == response.command.model_dump(mode="json")


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        ("command_id", OTHER_ID),
        ("command.command_id", OTHER_ID),
        ("command.execution_fence", "2"),
        ("command.execution_fence", "01"),
        ("command.owner_deletion_epoch", 1),
        ("command.owner_deletion_epoch", False),
        ("command.owner_deletion_epoch", "0"),
        ("command.input_version", "1"),
        ("command.core_source_decision.is_core", 1),
        ("command.unexpected", "synthetic-extra"),
        ("command", None),
        ("reason_code", "UNEXPECTED_REASON"),
        ("status", "UNKNOWN"),
        ("schema_version", "unknown"),
        ("unexpected", "synthetic-extra"),
    ],
)
def test_derived_available_reply_rejects_malformed_or_different_command(
    path: str, replacement: object
) -> None:
    derived = _load("private-command-lookup-available.json")
    _set(derived, path, replacement)
    codec = _codec()
    request = codec.parse_lookup_request(_load("private-command-lookup-request.json"))
    with pytest.raises(codec.W1WireContractError):
        codec.decode_lookup_response(derived, http_status=200, request=request)


@pytest.mark.parametrize("reason", [None, "", "X" * 65])
def test_derived_unavailable_reply_requires_nonempty_bounded_reason(reason: object) -> None:
    derived = _load("private-command-lookup-not-found.json")
    derived["reason_code"] = reason
    codec = _codec()
    request = codec.parse_lookup_request(_load("private-command-lookup-request.json"))
    with pytest.raises(codec.W1WireContractError):
        codec.decode_lookup_response(derived, http_status=200, request=request)


def test_derived_unavailable_reply_cannot_smuggle_a_command() -> None:
    derived = _load("private-command-lookup-deleted.json")
    derived["command"] = _load("private-command-lookup-available.json")["command"]
    codec = _codec()
    request = codec.parse_lookup_request(_load("private-command-lookup-request.json"))
    with pytest.raises(codec.W1WireContractError):
        codec.decode_lookup_response(derived, http_status=200, request=request)


@pytest.mark.parametrize(
    ("http_status", "code", "retryable", "fixture"),
    [
        (401, "UNAUTHENTICATED_SERVICE_PRINCIPAL", False, "private-error-unauthenticated.json"),
        (403, "FORBIDDEN_SERVICE_PRINCIPAL", False, "private-error-forbidden.json"),
        (422, "INVALID_LOOKUP_REQUEST", False, None),
        (503, "INTERNAL_RETRYABLE", True, None),
    ],
)
def test_protected_lookup_error_mapping_only_marks_503_retryable(
    http_status: int, code: str, retryable: bool, fixture: str | None
) -> None:
    # 422/503 are derived from the original safe error, not claimed upstream fixtures.
    body = _load(fixture or "private-error-unauthenticated.json")
    body.update(code=code, retryable=retryable)
    _assert_schema(body, "private-error.schema.json")
    codec = _codec()
    request = codec.parse_lookup_request(_load("private-command-lookup-request.json"))
    with pytest.raises(codec.W1LookupError) as caught:
        codec.decode_lookup_response(body, http_status=http_status, request=request)
    assert caught.value.code == code
    assert caught.value.retryable is retryable
    assert caught.value.http_status == http_status
    assert caught.value.correlation_id == UUID(body["correlation_id"])


@pytest.mark.parametrize(
    ("http_status", "path", "replacement"),
    [
        (403, "code", "UNAUTHENTICATED_SERVICE_PRINCIPAL"),
        (401, "retryable", True),
        (401, "retryable", "false"),
        (503, "code", "UNAUTHENTICATED_SERVICE_PRINCIPAL"),
        (401, "code", "UNKNOWN"),
        (401, "schema_version", "unknown"),
        (401, "unexpected", "synthetic-extra"),
    ],
)
def test_derived_protected_error_rejects_wrong_http_code_or_wire_shape(
    http_status: int, path: str, replacement: object
) -> None:
    derived = _load("private-error-unauthenticated.json")
    _set(derived, path, replacement)
    codec = _codec()
    request = codec.parse_lookup_request(_load("private-command-lookup-request.json"))
    with pytest.raises(codec.W1WireContractError):
        codec.decode_lookup_response(derived, http_status=http_status, request=request)


@pytest.mark.parametrize("http_status", [True, "200", 200.0, 201, 404, 500])
def test_lookup_rejects_unknown_or_coerced_http_status(http_status: object) -> None:
    codec = _codec()
    request = codec.parse_lookup_request(_load("private-command-lookup-request.json"))
    with pytest.raises(codec.W1WireContractError):
        codec.decode_lookup_response(
            _load("private-command-lookup-available.json"), http_status=http_status, request=request
        )


@pytest.mark.parametrize("field", ["execution_fence", "owner_deletion_epoch"])
def test_lookup_revalidates_original_request_model_copy_before_serialization(field: str) -> None:
    codec = _codec()
    request = codec.parse_lookup_request(_load("private-command-lookup-request.json"))
    request = request.model_copy(update={field: field == "execution_fence"})
    with pytest.raises(codec.W1WireContractError):
        codec.decode_lookup_response(
            _load("private-command-lookup-available.json"), http_status=200, request=request
        )


def test_pinned_partial_action_matches_existing_runtime_and_schema() -> None:
    original = _load("source-collection-result-partial.json")
    assert original["required_actions"][0]["code"] == "core_failure_decision"
    assert original["required_actions"][0]["context"]["choices"] == [
        "continue_limited",
        "stop",
        "retry",
    ]
    _assert_schema(original, "source-collection.result.schema.json")
    result = CollectionResult.model_validate(original)
    assert result.model_dump(mode="json") == original


@pytest.mark.parametrize("completion", ["complete", "partial", "failure"])
def test_private_result_envelope_uses_persisted_identity_and_validated_nested_result(
    completion: str,
) -> None:
    original = _load("private-w2-collection-result-complete.json")
    if completion != "complete":
        # A derived envelope around an original W2 payload; no upstream publish occurred.
        original["payload"] = _load(f"source-collection-result-{completion}.json")
    result = CollectionResult.model_validate(original["payload"])
    codec = _codec()
    identity = UUID(original["message_id"])
    occurred_at = datetime(2026, 9, 16, 1, tzinfo=UTC)
    first = codec.build_private_result_envelope(
        result, message_id=identity, occurred_at=occurred_at
    )
    replay = codec.build_private_result_envelope(
        result, message_id=identity, occurred_at=occurred_at
    )

    assert first.model_dump(mode="json") == original
    assert replay.model_dump(mode="json") == original
    assert isinstance(first.payload, CollectionResult)
    _assert_schema(first.model_dump(mode="json"), "private-message-envelope.schema.json")
    _assert_schema(first.payload.model_dump(mode="json"), "source-collection.result.schema.json")


@pytest.mark.parametrize("field", ["input_version", "result_version", "policy_revision"])
def test_result_envelope_revalidates_untrusted_model_copies_without_coercing_ints(
    field: str,
) -> None:
    original = _load("private-w2-collection-result-complete.json")
    result = CollectionResult.model_validate(original["payload"]).model_copy(update={field: True})
    codec = _codec()
    with pytest.raises(codec.W1WireContractError):
        codec.build_private_result_envelope(
            result, message_id=UUID(original["message_id"]), occurred_at=datetime.now(UTC)
        )


def test_result_envelope_rejects_nested_semantic_violation_even_in_model_copy() -> None:
    original = _load("private-w2-collection-result-complete.json")
    result = CollectionResult.model_validate(original["payload"]).model_copy(
        update={"successful_source_refs": []}
    )
    codec = _codec()
    with pytest.raises(codec.W1WireContractError):
        codec.build_private_result_envelope(
            result, message_id=UUID(original["message_id"]), occurred_at=datetime.now(UTC)
        )


@pytest.mark.parametrize(
    ("message_id", "occurred_at"),
    [
        (None, datetime(2026, 9, 16, tzinfo=UTC)),
        (OTHER_ID, datetime(2026, 9, 16, tzinfo=UTC)),
        (UUID(OTHER_ID), None),
        (UUID(OTHER_ID), datetime(2026, 9, 16)),
    ],
)
def test_result_envelope_cannot_invent_missing_or_naive_persisted_identity(
    message_id: object, occurred_at: object
) -> None:
    codec = _codec()
    result = CollectionResult.model_validate(
        _load("private-w2-collection-result-complete.json")["payload"]
    )
    with pytest.raises(codec.W1WireContractError):
        codec.build_private_result_envelope(result, message_id=message_id, occurred_at=occurred_at)


def test_wire_diagnostics_do_not_echo_private_payload_or_log_it(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    codec = _codec()
    derived = deepcopy(_load("private-w2-command-dispatch.json"))
    private_marker = "synthetic-private-marker-not-a-real-secret"
    derived["payload"]["unexpected"] = private_marker
    with pytest.raises(codec.W1WireContractError) as caught:
        codec.parse_w1_dispatch(derived)
    assert private_marker not in str(caught.value)
    assert private_marker not in repr(caught.value)
    assert caught.value.__suppress_context__ is True
    assert caplog.records == []
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("form", ["compact", "urn"])
@pytest.mark.parametrize(
    "path",
    [
        "message_id",
        "payload.command_id",
        "payload.job_id",
        "payload.authenticated_owner_ref",
        "payload.company_id",
        "payload.source_id",
        "payload.purpose_ref",
        "lookup_request.command_id",
        "core_decision_pin.origin_message_id",
        "core_decision_pin.decision_id",
        "core_decision_pin.company_id",
        "core_decision_pin.question_version_id",
        "core_decision_pin.source_id",
    ],
)
def test_dispatch_rejects_raw_uuid_lexical_forms_that_pydantic_normalizes(
    path: str, form: str
) -> None:
    derived = _load("private-w2-command-dispatch.json")
    if path == "core_decision_pin.question_version_id":
        derived["core_decision_pin"].update(
            decision_scope="QUESTION_MATCHING", company_id=None, question_version_id=OTHER_ID
        )
    _uuid_variant(derived, path, form)
    if path.startswith("payload."):
        _assert_raw_schema_rejects(derived["payload"], "source-collection.command.schema.json")
    else:
        _assert_raw_schema_rejects(derived, "private-w2-command-dispatch.schema.json")
    codec = _codec()
    with pytest.raises(codec.W1WireContractError):
        if path == "core_decision_pin.question_version_id":
            codec._parse_wire(derived["core_decision_pin"], codec.CoreDecisionPin, label="Core pin")
        else:
            codec.parse_w1_dispatch(derived)


@pytest.mark.parametrize("form", ["compact", "urn"])
def test_lookup_request_rejects_raw_uuid_lexical_forms(form: str) -> None:
    derived = _load("private-command-lookup-request.json")
    _uuid_variant(derived, "command_id", form)
    _assert_raw_schema_rejects(derived, "private-command-lookup-request.schema.json")
    codec = _codec()
    with pytest.raises(codec.W1WireContractError):
        codec.parse_lookup_request(derived)


@pytest.mark.parametrize("form", ["compact", "urn"])
@pytest.mark.parametrize(
    "path",
    [
        "command_id",
        "command.command_id",
        "command.job_id",
        "command.authenticated_owner_ref",
        "command.company_id",
        "command.source_id",
        "command.purpose_ref",
    ],
)
def test_lookup_reply_rejects_raw_uuid_lexical_forms(path: str, form: str) -> None:
    derived = _load("private-command-lookup-available.json")
    _uuid_variant(derived, path, form)
    if path.startswith("command."):
        _assert_raw_schema_rejects(derived["command"], "source-collection.command.schema.json")
    else:
        _assert_raw_schema_rejects(derived, "private-command-lookup-response.schema.json")
    codec = _codec()
    request = codec.parse_lookup_request(_load("private-command-lookup-request.json"))
    with pytest.raises(codec.W1WireContractError):
        codec.decode_lookup_response(derived, http_status=200, request=request)


@pytest.mark.parametrize("form", ["compact", "urn"])
def test_protected_error_rejects_raw_correlation_uuid_lexical_forms(form: str) -> None:
    derived = _load("private-error-unauthenticated.json")
    _uuid_variant(derived, "correlation_id", form)
    _assert_raw_schema_rejects(derived, "private-error.schema.json")
    codec = _codec()
    request = codec.parse_lookup_request(_load("private-command-lookup-request.json"))
    with pytest.raises(codec.W1WireContractError):
        codec.decode_lookup_response(derived, http_status=401, request=request)


@pytest.mark.parametrize("form", ["compact", "urn"])
@pytest.mark.parametrize(
    "path",
    [
        "message_id",
        "payload.command_id",
        "payload.job_id",
        "payload.source_id",
        "payload.successful_source_refs.0.source_id",
        "payload.successful_source_refs.0.source_version_id",
        "payload.successful_source_refs.0.extraction_revision_id",
        "payload.failures.0.source_id",
        "payload.required_actions.0.context.source_id",
    ],
)
def test_result_json_boundary_rejects_raw_nested_uuid_lexical_forms(path: str, form: str) -> None:
    derived = _load("private-w2-collection-result-complete.json")
    if "failures." in path or "required_actions." in path:
        derived["payload"] = _load("source-collection-result-failure.json")
    _uuid_variant(derived, path, form)
    if path.startswith("payload.") and "required_actions." not in path:
        _assert_raw_schema_rejects(derived["payload"], "source-collection.result.schema.json")
    elif path == "message_id":
        _assert_raw_schema_rejects(derived, "private-message-envelope.schema.json")
    codec = _codec()
    # Exercise the production JSON boundary used by the typed result builder, not a new API.
    with pytest.raises(codec.W1WireContractError):
        codec._parse_wire(
            derived, codec.PrivateCollectionResultEnvelope, label="W1 private result envelope"
        )


@pytest.mark.parametrize(
    "timestamp",
    ["1789531200", "2026-09-16 02:00:00Z", "2026-09-16T02:00Z", "2026-09-16T02:00:00+0000"],
)
def test_dispatch_rejects_raw_non_rfc3339_datetime_lexical_forms(timestamp: str) -> None:
    derived = _load("private-w2-command-dispatch.json")
    derived["occurred_at"] = timestamp
    _assert_raw_datetime_schema_rejects_when_supported(
        derived, "private-w2-command-dispatch.schema.json"
    )
    codec = _codec()
    with pytest.raises(codec.W1WireContractError):
        codec.parse_w1_dispatch(derived)


@pytest.mark.parametrize(
    "path",
    [
        "occurred_at",
        "payload.retry_not_before",
        "payload.required_actions.0.context.retry_not_before",
    ],
)
def test_result_json_boundary_rejects_raw_numeric_string_datetime_lexical_forms(path: str) -> None:
    derived = _load("private-w2-collection-result-complete.json")
    derived["payload"] = _load("source-collection-result-failure.json")
    _set(derived, path, "1789531200")
    if path == "occurred_at":
        _assert_raw_datetime_schema_rejects_when_supported(
            derived, "private-message-envelope.schema.json"
        )
    elif path == "payload.retry_not_before":
        _assert_raw_datetime_schema_rejects_when_supported(
            derived["payload"], "source-collection.result.schema.json"
        )
    codec = _codec()
    with pytest.raises(codec.W1WireContractError):
        codec._parse_wire(
            derived, codec.PrivateCollectionResultEnvelope, label="W1 private result envelope"
        )


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-09-16T11:00:00+09:00",
        "2026-09-16T02:00:00.123456Z",
        "2026-09-16T04:30:00.12+02:30",
        "2026-09-16t02:00:00z",
    ],
)
def test_datetime_lexical_guard_accepts_raw_valid_rfc3339_offsets_and_fractions(
    timestamp: str,
) -> None:
    derived = _load("private-w2-command-dispatch.json")
    derived["occurred_at"] = timestamp
    _assert_schema(derived, "private-w2-command-dispatch.schema.json")
    dispatch = _codec().parse_w1_dispatch(derived)
    assert dispatch.occurred_at == datetime.fromisoformat(timestamp.upper())


@pytest.mark.parametrize("nested", [False, True])
def test_result_builder_rejects_non_rfc3339_typed_datetime_seconds_offset(nested: bool) -> None:
    original = _load("private-w2-collection-result-complete.json")
    result = CollectionResult.model_validate(original["payload"])
    occurred_at = datetime(2026, 9, 16, 1, tzinfo=timezone(timedelta(seconds=30)))
    if nested:
        result = result.model_copy(update={"retry_not_before": occurred_at})
        occurred_at = datetime(2026, 9, 16, 1, tzinfo=UTC)
    codec = _codec()
    with pytest.raises(codec.W1WireContractError):
        codec.build_private_result_envelope(
            result, message_id=UUID(original["message_id"]), occurred_at=occurred_at
        )


def test_result_builder_keeps_valid_typed_offset_and_fractional_datetime() -> None:
    original = _load("private-w2-collection-result-complete.json")
    result = CollectionResult.model_validate(original["payload"])
    occurred_at = datetime.fromisoformat("2026-09-16T11:00:00.123456+09:00")
    envelope = _codec().build_private_result_envelope(
        result, message_id=UUID(original["message_id"]), occurred_at=occurred_at
    )
    assert envelope.occurred_at == occurred_at
    _assert_schema(envelope.model_dump(mode="json"), "private-message-envelope.schema.json")


def test_pinned_policy_failure_null_revision_matches_runtime_and_schema() -> None:
    derived = _load("source-collection-result-policy-failure.json")
    result = CollectionResult.model_validate(derived)
    envelope = _codec().build_private_result_envelope(
        result, message_id=UUID(OTHER_ID), occurred_at=datetime(2026, 9, 16, tzinfo=UTC)
    )
    assert envelope.payload.policy_revision is None
    assert envelope.payload.model_dump(mode="json") == derived
    _assert_schema(envelope.model_dump(mode="json"), "private-message-envelope.schema.json")
    _assert_schema(derived, "source-collection.result.schema.json")


def test_direct_registration_dispatch_keeps_w1_decision_and_separate_opaque_input() -> None:
    original = _load("private-w2-direct-source-registration-dispatch.json")
    dispatch = _codec().parse_w1_dispatch(original)
    assert dispatch.payload.core_source_decision.decided_by == "W1"
    assert dispatch.payload.core_source_decision.is_core is False
    assert dispatch.direct_source_registration_pin.question_version_id is None
    assert dispatch.direct_source_registration_pin.registration_input_version == (
        "direct:40000000-0000-4000-8000-000000000005:1"
    )
    assert dispatch.payload.input_version == 1
    assert dispatch.model_dump(mode="json") == original
    _assert_schema(original, "private-w2-direct-source-registration-dispatch.schema.json")
    _assert_schema(original["payload"], "source-collection.command.schema.json")


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        ("core_decision_pin.company_id", OTHER_ID),
        ("core_decision_pin.source_id", OTHER_ID),
        ("core_decision_pin.decision_version", 2),
        ("core_decision_pin.reason_code", "DIFFERENT_REASON"),
        ("payload.company_id", OTHER_ID),
        ("payload.source_id", OTHER_ID),
        ("payload.input_version", 2),
        ("payload.core_source_decision.decided_by", "w3"),
        ("payload.core_source_decision.decided_by", "W4"),
        ("payload.core_source_decision.is_core", False),
        ("payload.core_source_decision.rationale", "DIFFERENT_REASON"),
        ("payload.core_source_decision.decision_revision", 2),
    ],
)
def test_schema_valid_core_binding_mismatch_is_rejected_without_repair(
    path: str, replacement: object
) -> None:
    derived = _load("private-w2-command-dispatch.json")
    _set(derived, path, replacement)
    if path == "payload.input_version":
        derived["payload"]["core_source_decision"]["analysis_input_version"] = replacement
    _assert_schema(derived, "private-w2-command-dispatch.schema.json")
    _assert_schema(derived["payload"], "source-collection.command.schema.json")
    before = deepcopy(derived)
    codec = _codec()
    with pytest.raises(codec.W1WireContractError):
        codec.parse_w1_dispatch(derived)
    assert derived == before


def test_core_wrapper_cannot_accept_consistently_non_core_pin_and_payload() -> None:
    derived = _load("private-w2-command-dispatch.json")
    derived["core_decision_pin"].update(is_core=False, decision_code="NON_CORE_OPTIONAL")
    derived["payload"]["core_source_decision"]["is_core"] = False
    _assert_schema(derived, "private-w2-command-dispatch.schema.json")
    codec = _codec()
    with pytest.raises(codec.W1WireContractError):
        codec.parse_w1_dispatch(derived)


@pytest.mark.parametrize(
    ("fixture", "pin_name", "input_name"),
    [
        ("private-w2-command-dispatch.json", "core_decision_pin", "analysis_input_version"),
        (
            "private-w2-direct-source-registration-dispatch.json",
            "direct_source_registration_pin",
            "registration_input_version",
        ),
    ],
)
@pytest.mark.parametrize("opaque", ["opaque:not-a-number", "0007"])
def test_pin_opaque_input_is_not_coerced_or_compared_to_numeric_payload_version(
    fixture: str, pin_name: str, input_name: str, opaque: str
) -> None:
    derived = _load(fixture)
    derived[pin_name][input_name] = opaque
    derived[pin_name]["decision_version"] = 7
    derived["payload"]["input_version"] = 7
    derived["payload"]["core_source_decision"].update(decision_revision=7, analysis_input_version=7)
    dispatch = _codec().parse_w1_dispatch(derived)
    assert dispatch.model_dump(mode="json") == derived
    assert dispatch.payload.input_version == 7


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        ("message_id", OTHER_ID),
        ("lookup_request.command_id", OTHER_ID),
        ("lookup_request.execution_fence", 2),
        ("lookup_request.execution_fence", True),
        ("lookup_request.owner_deletion_epoch", 1),
        ("payload.execution_fence", "01"),
        ("payload.owner_deletion_epoch", True),
        ("payload.company_id", OTHER_ID),
        ("payload.source_id", OTHER_ID),
        ("payload.input_version", 2),
        ("payload.core_source_decision.decided_by", "w1"),
        ("payload.core_source_decision.decided_by", "W3"),
        ("payload.core_source_decision.is_core", True),
        ("payload.core_source_decision.rationale", "DIFFERENT_REASON"),
        ("payload.core_source_decision.decision_revision", 2),
        ("direct_source_registration_pin.company_id", OTHER_ID),
        ("direct_source_registration_pin.company_id", None),
        ("direct_source_registration_pin.source_id", OTHER_ID),
        ("direct_source_registration_pin.question_version_id", OTHER_ID),
        ("direct_source_registration_pin.decision_scope", "COMPANY_KNOWLEDGE"),
        ("direct_source_registration_pin.decision_owner", "W3"),
        ("direct_source_registration_pin.is_core", True),
        ("direct_source_registration_pin.is_core", 0),
        ("direct_source_registration_pin.decision_code", "CORE_REQUIRED"),
        ("direct_source_registration_pin.purpose", "COMPANY_KNOWLEDGE"),
        ("direct_source_registration_pin.decision_version", 2),
        ("direct_source_registration_pin.decision_version", 0),
        ("direct_source_registration_pin.decision_version", True),
        ("direct_source_registration_pin.decision_version", "1"),
        ("direct_source_registration_pin.registration_input_version", 1),
        ("direct_source_registration_pin.registration_input_version", ""),
        ("direct_source_registration_pin.registration_input_version", "x" * 65),
        ("direct_source_registration_pin.reason_code", "DIFFERENT_REASON"),
        ("direct_source_registration_pin.reason_code", "unsafe reason text"),
        ("direct_source_registration_pin.unexpected", "synthetic-extra"),
        ("payload.unexpected", "synthetic-extra"),
        ("unexpected", "synthetic-extra"),
    ],
)
def test_direct_registration_rejects_malformed_or_mismatched_binding(
    path: str, replacement: object
) -> None:
    derived = _load("private-w2-direct-source-registration-dispatch.json")
    _set(derived, path, replacement)
    if path == "payload.input_version":
        derived["payload"]["core_source_decision"]["analysis_input_version"] = replacement
    before = deepcopy(derived)
    codec = _codec()
    with pytest.raises(codec.W1WireContractError):
        codec.parse_w1_dispatch(derived)
    assert derived == before


def test_direct_registration_pin_requires_explicit_null_question_version_id() -> None:
    derived = _load("private-w2-direct-source-registration-dispatch.json")
    del derived["direct_source_registration_pin"]["question_version_id"]
    codec = _codec()
    with pytest.raises(codec.W1WireContractError):
        codec.parse_w1_dispatch(derived)


@pytest.mark.parametrize("direct", [False, True])
def test_dispatch_wrapper_cannot_mix_core_and_direct_registration_provenance(direct: bool) -> None:
    fixture = (
        "private-w2-direct-source-registration-dispatch.json"
        if direct
        else "private-w2-command-dispatch.json"
    )
    derived = _load(fixture)
    derived["core_decision_pin" if direct else "direct_source_registration_pin"] = _load(
        "private-w2-command-dispatch.json"
        if direct
        else "private-w2-direct-source-registration-dispatch.json"
    )["core_decision_pin" if direct else "direct_source_registration_pin"]
    codec = _codec()
    with pytest.raises(codec.W1WireContractError):
        codec.parse_w1_dispatch(derived)


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        ("job_id", OTHER_ID),
        ("authenticated_owner_ref", OTHER_ID),
        ("project_ref", "different-project"),
        ("company_id", OTHER_ID),
        ("source_id", OTHER_ID),
        ("purpose_ref", OTHER_ID),
        ("input_version", 2),
        ("core_source_decision.decided_by", "different-owner"),
        ("core_source_decision.is_core", "invert"),
        ("core_source_decision.rationale", "DIFFERENT_REASON"),
        ("core_source_decision.decision_revision", 2),
        ("resume_stage", "fetch"),
        ("policy_revision", 1),
    ],
)
def test_available_lookup_cannot_differ_from_any_dispatch_command_field(
    direct: bool, path: str, replacement: object
) -> None:
    codec = _codec()
    assert hasattr(codec, "validate_dispatch_lookup"), "dispatch/lookup binding is missing"
    dispatch = codec.parse_w1_dispatch(
        _load(
            "private-w2-direct-source-registration-dispatch.json"
            if direct
            else "private-w2-command-dispatch.json"
        )
    )
    raw = _load("private-command-lookup-available.json")
    raw["command_id"] = str(dispatch.payload.command_id)
    raw["command"] = dispatch.payload.model_dump(mode="json")
    if replacement == "invert":
        replacement = not raw["command"]["core_source_decision"]["is_core"]
    _set(raw["command"], path, replacement)
    if path == "input_version":
        raw["command"]["core_source_decision"]["analysis_input_version"] = replacement
    if path == "resume_stage":
        raw["command"]["policy_revision"] = 1
    response = codec.decode_lookup_response(raw, http_status=200, request=dispatch.lookup_request)
    with pytest.raises(codec.W1WireContractError):
        codec.validate_dispatch_lookup(dispatch, response)


def test_direct_registration_available_lookup_keeps_the_same_complete_command() -> None:
    codec = _codec()
    assert hasattr(codec, "validate_dispatch_lookup"), "dispatch/lookup binding is missing"
    dispatch = codec.parse_w1_dispatch(_load("private-w2-direct-source-registration-dispatch.json"))
    raw = _load("private-command-lookup-available.json")
    raw.update(
        command_id=str(dispatch.payload.command_id),
        command=dispatch.payload.model_dump(mode="json"),
    )
    response = codec.decode_lookup_response(raw, http_status=200, request=dispatch.lookup_request)
    assert codec.validate_dispatch_lookup(dispatch, response) is None
    assert response.command == dispatch.payload


@pytest.mark.parametrize("status", LOOKUP_STATUSES[1:])
def test_dispatch_lookup_check_does_not_promote_unavailable_response(status: str) -> None:
    codec = _codec()
    assert hasattr(codec, "validate_dispatch_lookup"), "dispatch/lookup binding is missing"
    dispatch = codec.parse_w1_dispatch(_load("private-w2-command-dispatch.json"))
    response = codec.decode_lookup_response(
        _load(f"private-command-lookup-{status}.json"),
        http_status=200,
        request=dispatch.lookup_request,
    )
    assert codec.validate_dispatch_lookup(dispatch, response) is None
    assert response.command is None
    assert response.status != "AVAILABLE"


@pytest.mark.parametrize(
    ("fixture", "path", "replacement"),
    [
        ("source-collection-result-partial.json", "required_actions.0.code", "continue_limited"),
        (
            "source-collection-result-partial.json",
            "required_actions.0.context.choices",
            ["stop", "retry", "continue_limited"],
        ),
        ("source-collection-result-failure.json", "policy_revision", None),
        ("source-collection-result-partial.json", "policy_revision", None),
        ("source-collection-result-policy-failure.json", "failures.0.stage", "fetch"),
        ("source-collection-result-policy-failure.json", "policy_revision", 0),
        ("source-collection-result-policy-failure.json", "policy_revision", -1),
    ],
)
def test_partial_actions_and_nullable_policy_failures_reject_invalid_variants(
    fixture: str, path: str, replacement: object
) -> None:
    derived = _load(fixture)
    _set(derived, path, replacement)
    _assert_raw_schema_rejects(derived, "source-collection.result.schema.json")
    with pytest.raises(ValidationError):
        CollectionResult.model_validate(derived)


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("tampered", ["dispatch", "response", "unavailable-identity"])
def test_dispatch_lookup_revalidates_copied_models_before_comparison(
    direct: bool, tampered: str
) -> None:
    codec = _codec()
    assert hasattr(codec, "validate_dispatch_lookup"), "dispatch/lookup binding is missing"
    dispatch = codec.parse_w1_dispatch(
        _load(
            "private-w2-direct-source-registration-dispatch.json"
            if direct
            else "private-w2-command-dispatch.json"
        )
    )
    raw = _load("private-command-lookup-available.json")
    raw.update(
        command_id=str(dispatch.payload.command_id),
        command=dispatch.payload.model_dump(mode="json"),
    )
    response = codec.decode_lookup_response(raw, http_status=200, request=dispatch.lookup_request)
    if tampered == "dispatch":
        dispatch = dispatch.model_copy(
            update={"payload": dispatch.payload.model_copy(update={"input_version": True})}
        )
    elif tampered == "response":
        response = response.model_copy(
            update={"command": response.command.model_copy(update={"input_version": True})}
        )
    else:
        raw = _load("private-command-lookup-deleted.json")
        raw["command_id"] = str(dispatch.payload.command_id)
        response = codec.decode_lookup_response(
            raw, http_status=200, request=dispatch.lookup_request
        )
        response = response.model_copy(update={"command_id": UUID(OTHER_ID)})
    with pytest.raises(codec.W1WireContractError):
        codec.validate_dispatch_lookup(dispatch, response)


@pytest.mark.parametrize("invalid_dispatch", [False, True])
def test_dispatch_lookup_rejects_untyped_context(invalid_dispatch: bool) -> None:
    codec = _codec()
    assert hasattr(codec, "validate_dispatch_lookup"), "dispatch/lookup binding is missing"
    dispatch = codec.parse_w1_dispatch(_load("private-w2-command-dispatch.json"))
    response = codec.decode_lookup_response(
        _load("private-command-lookup-available.json"),
        http_status=200,
        request=dispatch.lookup_request,
    )
    with pytest.raises(codec.W1WireContractError):
        codec.validate_dispatch_lookup(
            None if invalid_dispatch else dispatch, response if invalid_dispatch else None
        )


@pytest.mark.parametrize("direct", [False, True])
def test_deep_lookup_mismatch_diagnostics_do_not_echo_or_log_private_data(
    direct: bool, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    codec = _codec()
    assert hasattr(codec, "validate_dispatch_lookup"), "dispatch/lookup binding is missing"
    dispatch = codec.parse_w1_dispatch(
        _load(
            "private-w2-direct-source-registration-dispatch.json"
            if direct
            else "private-w2-command-dispatch.json"
        )
    )
    raw = _load("private-command-lookup-available.json")
    raw.update(
        command_id=str(dispatch.payload.command_id),
        command=dispatch.payload.model_dump(mode="json"),
    )
    marker = "synthetic-private-marker-not-a-real-secret"
    raw["command"]["project_ref"] = marker
    response = codec.decode_lookup_response(raw, http_status=200, request=dispatch.lookup_request)
    with pytest.raises(codec.W1WireContractError) as caught:
        codec.validate_dispatch_lookup(dispatch, response)
    assert marker not in str(caught.value)
    assert marker not in repr(caught.value)
    assert caplog.records == []
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("form", ["compact", "urn"])
@pytest.mark.parametrize(
    "path",
    [
        "direct_source_registration_pin.registration_decision_id",
        "direct_source_registration_pin.company_id",
        "direct_source_registration_pin.source_id",
    ],
)
def test_direct_registration_rejects_normalized_pin_uuid_literals(path: str, form: str) -> None:
    derived = _load("private-w2-direct-source-registration-dispatch.json")
    _uuid_variant(derived, path, form)
    _assert_raw_schema_rejects(
        derived, "private-w2-direct-source-registration-dispatch.schema.json"
    )
    codec = _codec()
    with pytest.raises(codec.W1WireContractError):
        codec.parse_w1_dispatch(derived)
