"""Contract tests for source-scoped W1 failure decisions.

The W1 aggregate is represented by the deterministic, no-I/O ``W1FailureFake``.
These tests deliberately keep its aggregate ``result_revision`` separate from the
per-Source W2 ``result_version`` exposed by collection contracts.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError
from tests.integration.source_collection.w1_failure_fake import W1FailureFake, W1JobStatus

from epick_engine.source_collection.contracts import (
    CollectionCommand,
    CollectionResult,
    CollectionStage,
    CompletionKind,
    CoreSourceDecision,
    Failure,
    SourceReference,
    UserRetryAction,
    UserRetryContext,
)

ENGINE_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = ENGINE_ROOT / "tests" / "fixtures" / "synthetic_sources" / "failure_sequences.json"
CONTRACT_PATH = (
    ENGINE_ROOT.parent
    / "specs"
    / "001-official-source-collection"
    / "contracts"
    / "collection-actions.schema.json"
)
UUID_NAMESPACE = UUID("f53766e6-e1bf-42a6-b513-4280cc047b57")


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value: dict[str, Any] = json.load(stream)
    return value


FAILURE_CASES = {case["name"]: case for case in _load_json(FIXTURE_PATH)["cases"]}
ACTION_SCHEMA = _load_json(CONTRACT_PATH)
DECISION_VALIDATOR = Draft202012Validator(
    ACTION_SCHEMA["$defs"]["DecisionRequest"],
    format_checker=FormatChecker(),
)


def _uuid(name: str) -> UUID:
    return uuid5(UUID_NAMESPACE, name)


def _fake(case_name: str) -> W1FailureFake:
    return W1FailureFake.from_case(deepcopy(FAILURE_CASES[case_name]))


def _source_id(fake: W1FailureFake, name: str) -> str:
    source = next(source for source in fake.snapshot()["sources"] if source["name"] == name)
    return source["source_id"]


def _source_state(fake: W1FailureFake, name: str) -> dict[str, Any]:
    return next(source for source in fake.snapshot()["sources"] if source["name"] == name)


def _decision(
    fake: W1FailureFake,
    *,
    action: str,
    source_id: str | None,
    expected_input_version: int | None = None,
    expected_result_version: int | None = None,
) -> dict[str, Any]:
    snapshot = fake.snapshot()
    return {
        "schema_version": "w1.collection-decision.v2",
        "action": action,
        "source_id": source_id,
        "expected_input_version": (
            snapshot["input_version"] if expected_input_version is None else expected_input_version
        ),
        "expected_result_version": (
            snapshot["result_revision"]
            if expected_result_version is None
            else expected_result_version
        ),
    }


def _submit(
    fake: W1FailureFake,
    *,
    action: str,
    source_id: str | None,
    idempotency_key: str,
    expected_input_version: int | None = None,
    expected_result_version: int | None = None,
) -> dict[str, Any]:
    payload = _decision(
        fake,
        action=action,
        source_id=source_id,
        expected_input_version=expected_input_version,
        expected_result_version=expected_result_version,
    )
    assert list(DECISION_VALIDATOR.iter_errors(payload)) == []
    return fake.submit_decision(payload, idempotency_key=idempotency_key)


def _command(source_id: UUID) -> CollectionCommand:
    return CollectionCommand(
        schema_version="w2.collection.v1",
        command_id=_uuid("command"),
        job_id=_uuid("job"),
        authenticated_owner_ref=_uuid("owner"),
        project_ref="project-ref",
        company_id=_uuid("company"),
        source_id=source_id,
        input_version=1,
        execution_fence="execution-fence",
        purpose_ref=_uuid("purpose"),
        core_source_decision=CoreSourceDecision(
            is_core=True,
            decided_by="source-selection",
            rationale="The source is required for the collection request.",
            decision_revision=7,
            analysis_input_version=1,
        ),
        resume_stage=CollectionStage.FETCH,
        policy_revision=3,
        owner_deletion_epoch=0,
    )


def _partial_result(source_id: UUID) -> CollectionResult:
    return CollectionResult(
        schema_version="w2.collection.v1",
        command_id=_uuid("result-command"),
        job_id=_uuid("result-job"),
        input_version=1,
        result_version=1,
        successful_source_refs=[
            SourceReference(
                source_id=source_id,
                source_version_id=_uuid("source-version"),
                extraction_revision_id=_uuid("extraction-revision"),
            )
        ],
        failures=[
            Failure(
                source_id=source_id,
                stage=CollectionStage.PARSE,
                code="MISSING_REQUIRED_SECTION",
                missing_sections=["job_description"],
                impact="The source requires an explicit retry decision.",
                core_decision_revision=7,
            )
        ],
        completion_kind=CompletionKind.PARTIAL,
        resume_stage=CollectionStage.FETCH,
        checkpoint_ref="fetch:checkpoint-1",
        retry_not_before=None,
        message_ko="일부 항목을 수집했으며 재시도 결정이 필요합니다.",
        required_actions=[
            UserRetryAction(
                code="user_retry",
                label_ko="재시도",
                context=UserRetryContext(
                    source_id=source_id,
                    resume_stage=CollectionStage.FETCH,
                    retry_not_before=None,
                ),
            )
        ],
        source_id=source_id,
        policy_revision=3,
    )


def test_collection_command_carries_an_explicit_core_decision_for_one_source() -> None:
    source_id = _uuid("command-source")

    command = _command(source_id)

    assert command.source_id == source_id
    assert command.core_source_decision.is_core is True
    assert command.core_source_decision.analysis_input_version == command.input_version

    missing_decision = command.model_dump(mode="json")
    del missing_decision["core_source_decision"]
    with pytest.raises(ValidationError, match="core_source_decision"):
        CollectionCommand.model_validate(missing_decision)

    changed_input = command.model_dump(mode="json")
    changed_input["core_source_decision"]["analysis_input_version"] = command.input_version + 1
    with pytest.raises(ValidationError, match="analysis_input_version"):
        CollectionCommand.model_validate(changed_input)


def test_collection_result_limits_success_to_its_source_and_allows_partial_failure() -> None:
    source_id = _uuid("result-source")
    partial = _partial_result(source_id)
    complete = CollectionResult(
        schema_version="w2.collection.v1",
        command_id=_uuid("complete-command"),
        job_id=_uuid("complete-job"),
        input_version=1,
        result_version=1,
        successful_source_refs=partial.successful_source_refs,
        failures=[],
        completion_kind=CompletionKind.COMPLETE,
        resume_stage=None,
        checkpoint_ref=None,
        retry_not_before=None,
        message_ko="수집을 완료했습니다.",
        required_actions=[],
        source_id=source_id,
        policy_revision=3,
    )

    assert partial.completion_kind is CompletionKind.PARTIAL
    assert complete.completion_kind is CompletionKind.COMPLETE

    too_many_successes = partial.model_dump(mode="json")
    too_many_successes["successful_source_refs"].append(
        {
            "source_id": str(source_id),
            "source_version_id": str(_uuid("another-source-version")),
            "extraction_revision_id": str(_uuid("another-extraction-revision")),
        }
    )
    with pytest.raises(ValidationError):
        CollectionResult.model_validate(too_many_successes)

    invalid_partial = partial.model_dump(mode="json")
    invalid_partial["successful_source_refs"] = []
    with pytest.raises(ValidationError, match="partial result requires one success"):
        CollectionResult.model_validate(invalid_partial)

    missing_partial_failure = partial.model_dump(mode="json")
    missing_partial_failure["failures"] = []
    with pytest.raises(ValidationError, match="partial result requires one success and failures"):
        CollectionResult.model_validate(missing_partial_failure)


@pytest.mark.parametrize("field", ["successful_source_refs", "failures", "required_actions"])
def test_collection_result_rejects_cross_source_result_data(field: str) -> None:
    source_id = _uuid("identity-source")
    other_source_id = _uuid("foreign-source")
    payload = _partial_result(source_id).model_dump(mode="json")

    if field == "successful_source_refs":
        payload[field][0]["source_id"] = str(other_source_id)
    elif field == "failures":
        payload[field][0]["source_id"] = str(other_source_id)
    else:
        payload[field][0]["context"]["source_id"] = str(other_source_id)

    with pytest.raises(ValidationError, match="source_id does not match result"):
        CollectionResult.model_validate(payload)


def test_core_and_rate_limit_failures_require_per_source_actions_while_rate_limited() -> None:
    fake = _fake("core_plus_429")
    snapshot = fake.snapshot()

    assert fake.status is W1JobStatus.PAUSED_RATE_LIMIT
    assert snapshot["completion_kind"] == CompletionKind.NONE.value
    assert {failure["source_id"] for failure in snapshot["failures"]} == {
        _source_id(fake, "A"),
        _source_id(fake, "B"),
    }
    assert [action["action"] for action in snapshot["required_actions"]] == [
        "core_failure_decision",
        "core_failure_decision",
    ]
    assert {action["source_id"] for action in snapshot["required_actions"]} == {
        _source_id(fake, "A"),
        _source_id(fake, "B"),
    }
    assert {action["expected_result_version"] for action in snapshot["required_actions"]} == {
        snapshot["result_revision"]
    }
    assert _source_state(fake, "B")["result_version"] != snapshot["result_revision"]


@pytest.mark.parametrize(
    ("action", "source_name"),
    [("retry", "A"), ("continue_limited", "A"), ("stop", None)],
)
def test_decision_schema_accepts_only_public_valid_requests(
    action: str, source_name: str | None
) -> None:
    fake = _fake("core_plus_429")
    payload = _decision(
        fake,
        action=action,
        source_id=_source_id(fake, source_name) if source_name is not None else None,
    )

    assert list(DECISION_VALIDATOR.iter_errors(payload)) == []
    assert set(payload) == {
        "schema_version",
        "action",
        "source_id",
        "expected_input_version",
        "expected_result_version",
    }
    assert "expected_job_revision" not in payload
    assert "idempotency_key" not in payload


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "w1.collection-decision.v1"},
        {"action": "restart"},
        {"source_id": None},
        {"source_id": "not-a-uuid"},
        {"expected_input_version": 0},
        {"expected_result_version": 0},
        {"unexpected": "field"},
    ],
)
def test_decision_schema_rejects_invalid_or_private_request_shapes(change: dict[str, Any]) -> None:
    fake = _fake("core_plus_429")
    payload = _decision(fake, action="retry", source_id=_source_id(fake, "A"))
    payload.update(change)

    assert list(DECISION_VALIDATOR.iter_errors(payload))


def test_decision_schema_rejects_a_non_null_source_for_stop() -> None:
    fake = _fake("core_plus_429")
    payload = _decision(fake, action="stop", source_id=None)
    payload["source_id"] = _source_id(fake, "A")

    assert list(DECISION_VALIDATOR.iter_errors(payload))


def test_accepting_a_retry_does_not_dispatch_while_another_core_source_is_undecided() -> None:
    fake = _fake("core_plus_429")
    a_source_id = _source_id(fake, "A")

    accepted = _submit(
        fake,
        action="retry",
        source_id=a_source_id,
        idempotency_key="retry-a",
    )
    after_acceptance = fake.snapshot()
    replay = fake.submit_decision(
        _decision(fake, action="retry", source_id=a_source_id, expected_result_version=3),
        idempotency_key="retry-a",
    )
    after_replay = fake.snapshot()

    assert accepted["status_code"] == 202
    assert accepted["accepted"] is True
    assert accepted["dispatch_count"] == 0
    assert replay == accepted
    assert after_acceptance["dispatch_count"] == after_acceptance["active_slots"] == 0
    assert after_replay["dispatch_count"] == after_replay["active_slots"] == 0
    assert _source_state(fake, "A")["dispatched"] is False
    assert _source_state(fake, "B")["decision"] is None
    assert not any(event["event"] == "sources_dispatched" for event in after_replay["ledger"])


def test_continue_limited_unblocks_only_the_retry_selected_source() -> None:
    fake = _fake("core_plus_429")
    a_source_id = _source_id(fake, "A")
    b_source_id = _source_id(fake, "B")

    _submit(fake, action="retry", source_id=a_source_id, idempotency_key="retry-a")
    accepted = _submit(
        fake,
        action="continue_limited",
        source_id=b_source_id,
        idempotency_key="continue-b",
    )
    dispatched = fake.dispatch_ready()
    snapshot = fake.snapshot()

    assert accepted["status_code"] == 202
    assert accepted["dispatch_count"] == 0
    assert dispatched == (UUID(a_source_id),)
    assert fake.status is W1JobStatus.RUNNING
    assert snapshot["dispatch_count"] == snapshot["active_slots"] == 1
    assert _source_state(fake, "A")["dispatched"] is True
    assert _source_state(fake, "B")["dispatched"] is False


def test_all_retry_decisions_dispatch_without_a_circular_wait() -> None:
    fake = _fake("core_plus_429")
    a_source_id = _source_id(fake, "A")
    b_source_id = _source_id(fake, "B")

    _submit(fake, action="retry", source_id=a_source_id, idempotency_key="retry-a")
    _submit(fake, action="retry", source_id=b_source_id, idempotency_key="retry-b")
    dispatched = fake.dispatch_ready()

    assert dispatched == (UUID(a_source_id), UUID(b_source_id))
    assert fake.status is W1JobStatus.RUNNING
    assert fake.snapshot()["required_actions"] == []


def test_analysis_starts_once_only_after_the_retry_result_resolves() -> None:
    fake = _fake("core_plus_429")
    a_source_id = _source_id(fake, "A")
    b_source_id = _source_id(fake, "B")

    _submit(fake, action="retry", source_id=a_source_id, idempotency_key="retry-a")
    _submit(fake, action="continue_limited", source_id=b_source_id, idempotency_key="continue-b")
    assert fake.dispatch_ready() == (UUID(a_source_id),)
    assert fake.request_analysis() is False
    assert fake.snapshot()["analysis_start_count"] == 0

    assert fake.record_source_result(a_source_id, outcome="success") is True
    assert fake.status is W1JobStatus.SUCCEEDED
    assert fake.snapshot()["completion_kind"] == CompletionKind.PARTIAL.value
    assert fake.request_analysis() is True
    assert fake.request_analysis() is False
    assert fake.snapshot()["analysis_start_count"] == 1


def test_refailure_requires_a_core_decision_revision_and_creates_a_fresh_selection() -> None:
    fake = _fake("core_plus_429")
    a_source_id = _source_id(fake, "A")
    b_source_id = _source_id(fake, "B")

    with pytest.raises(ValueError, match="core_decision_revision"):
        fake.record_source_result(a_source_id, outcome="failure", core_decision_revision=None)

    _submit(fake, action="retry", source_id=a_source_id, idempotency_key="retry-a")
    _submit(fake, action="continue_limited", source_id=b_source_id, idempotency_key="continue-b")
    assert fake.dispatch_ready() == (UUID(a_source_id),)
    assert (
        fake.record_source_result(
            a_source_id,
            outcome="failure",
            core_decision_revision=205,
        )
        is True
    )
    snapshot = fake.snapshot()
    fresh_failure = snapshot["failures"][-1]

    assert fake.status is W1JobStatus.WAITING_USER
    assert _source_state(fake, "A")["decision"] is None
    assert _source_state(fake, "A")["core_decision_revision"] == 205
    assert fresh_failure["core_decision_revision"] == 205
    assert snapshot["required_actions"] == [
        {
            "action": "core_failure_decision",
            "source_id": a_source_id,
            "choices": ["continue_limited", "stop", "retry"],
            "expected_result_version": snapshot["result_revision"],
        }
    ]
    assert (
        snapshot["ledger"][-1]["source_result_version"]
        == _source_state(fake, "A")["result_version"]
    )
    assert snapshot["ledger"][-1]["result_revision"] == snapshot["result_revision"]


def test_stop_cancels_without_dispatching_or_starting_analysis() -> None:
    fake = _fake("core_plus_429")

    accepted = _submit(fake, action="stop", source_id=None, idempotency_key="stop")
    snapshot = fake.snapshot()

    assert accepted["status_code"] == 202
    assert accepted["status"] == W1JobStatus.CANCELLED.value
    assert fake.dispatch_ready() == ()
    assert fake.request_analysis() is False
    assert snapshot["dispatch_count"] == snapshot["active_slots"] == 0
    assert snapshot["analysis_start_count"] == 0


def test_fake_rejects_missing_and_foreign_sources_without_accepting_a_decision() -> None:
    fake = _fake("core_plus_429")

    missing = fake.submit_decision(
        _decision(fake, action="retry", source_id=None),
        idempotency_key="missing-source",
    )
    foreign = fake.submit_decision(
        _decision(fake, action="retry", source_id=str(_uuid("foreign"))),
        idempotency_key="foreign-source",
    )

    assert missing["status_code"] == foreign["status_code"] == 409
    assert missing["code"] == "SOURCE_REQUIRED"
    assert foreign["code"] == "SOURCE_NOT_IN_JOB"
    assert fake.snapshot()["dispatch_count"] == 0


def test_decisions_use_current_input_and_w1_aggregate_result_revisions() -> None:
    input_changed = _fake("input_changed")
    input_source_id = _source_id(input_changed, "A")
    stale_input = _submit(
        input_changed,
        action="retry",
        source_id=input_source_id,
        expected_input_version=1,
        idempotency_key="stale-input",
    )

    fake = _fake("core_plus_429")
    b_source_id = _source_id(fake, "B")
    b_source_result_version = _source_state(fake, "B")["result_version"]
    aggregate_result_revision = fake.snapshot()["result_revision"]
    accepted = _submit(
        fake,
        action="continue_limited",
        source_id=b_source_id,
        expected_result_version=aggregate_result_revision,
        idempotency_key="continue-b",
    )
    stale_result = _submit(
        fake,
        action="retry",
        source_id=_source_id(fake, "A"),
        expected_result_version=aggregate_result_revision,
        idempotency_key="stale-result",
    )

    assert stale_input["status_code"] == 409
    assert stale_input["code"] == "STALE_INPUT_VERSION"
    assert accepted["status_code"] == 202
    assert b_source_result_version != aggregate_result_revision
    assert stale_result["status_code"] == 409
    assert stale_result["code"] == "STALE_RESULT_VERSION"


def test_same_idempotency_key_replays_only_the_same_public_decision() -> None:
    fake = _fake("core_plus_429")
    a_source_id = _source_id(fake, "A")
    retry_payload = _decision(fake, action="retry", source_id=a_source_id)

    accepted = fake.submit_decision(retry_payload, idempotency_key="same-key")
    replay = fake.submit_decision(retry_payload, idempotency_key="same-key")
    conflict = fake.submit_decision(
        _decision(fake, action="continue_limited", source_id=_source_id(fake, "B")),
        idempotency_key="same-key",
    )

    assert accepted["status_code"] == replay["status_code"] == 202
    assert replay == accepted
    assert conflict["status_code"] == 409
    assert conflict["code"] == "IDEMPOTENCY_CONFLICT"
