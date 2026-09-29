"""Test-only US4 journey for independent partial Source results.

The deterministic W1 fake validates the decision/dispatch boundary only.  It
does not stand in for W1 authentication, its queue, or a Korean UI consumer.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID

from tests.integration.source_collection.w1_failure_fake import W1FailureFake, W1JobStatus

from epick_engine.source_collection.contracts import CompletionKind

ENGINE_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_PATH = ENGINE_ROOT / "tests" / "fixtures" / "synthetic_sources" / "failure_sequences.json"


def _fake() -> W1FailureFake:
    with FIXTURE_PATH.open(encoding="utf-8") as stream:
        cases = json.load(stream)["cases"]
    case = next(case for case in cases if case["name"] == "core_plus_429")
    return W1FailureFake.from_case(deepcopy(case))


def _source_state(fake: W1FailureFake, name: str) -> dict[str, Any]:
    return next(source for source in fake.snapshot()["sources"] if source["name"] == name)


def _source_id(fake: W1FailureFake, name: str) -> UUID:
    return UUID(_source_state(fake, name)["source_id"])


def _submit(
    fake: W1FailureFake,
    *,
    action: str,
    source_id: UUID,
    idempotency_key: str,
) -> dict[str, Any]:
    snapshot = fake.snapshot()
    return fake.submit_decision(
        action=action,
        source_id=source_id,
        expected_input_version=snapshot["input_version"],
        expected_result_version=snapshot["result_revision"],
        idempotency_key=idempotency_key,
    )


def _dispatch_a_only_after_b_is_excluded(fake: W1FailureFake) -> UUID:
    source_a_id = _source_id(fake, "A")
    source_b_id = _source_id(fake, "B")

    accepted_a = _submit(
        fake,
        action="retry",
        source_id=source_a_id,
        idempotency_key="partial-journey-retry-a",
    )

    assert accepted_a["status_code"] == 202
    assert accepted_a["accepted"] is True
    assert accepted_a["status"] == W1JobStatus.PAUSED_RATE_LIMIT.value
    assert accepted_a["dispatch_count"] == 0
    assert fake.dispatch_ready() == ()
    assert fake.snapshot()["active_slots"] == 0
    assert fake.request_analysis() is False

    accepted_b = _submit(
        fake,
        action="continue_limited",
        source_id=source_b_id,
        idempotency_key="partial-journey-exclude-b",
    )

    assert accepted_b["status_code"] == 202
    assert accepted_b["accepted"] is True
    assert accepted_b["status"] == W1JobStatus.QUEUED.value
    assert accepted_b["dispatch_count"] == 0
    assert fake.dispatch_ready() == (source_a_id,)
    assert fake.snapshot()["active_slots"] == 1
    assert _source_state(fake, "A")["dispatched"] is True
    assert _source_state(fake, "B")["decision"] == "continue_limited"
    assert _source_state(fake, "B")["dispatched"] is False
    assert fake.request_analysis() is False

    return source_a_id


def test_partial_result_journey_waits_for_choices_before_retry_and_analysis() -> None:
    fake = _fake()
    source_a_id = _dispatch_a_only_after_b_is_excluded(fake)

    assert fake.record_source_result(source_a_id, outcome="success") is True

    snapshot = fake.snapshot()
    assert snapshot["status"] == W1JobStatus.SUCCEEDED.value
    assert snapshot["completion_kind"] == CompletionKind.PARTIAL.value
    assert _source_state(fake, "A")["outcome"] == "success"
    assert _source_state(fake, "B")["outcome"] == "rate_limited"
    assert fake.request_analysis() is True
    assert fake.snapshot()["analysis_start_count"] == 1


def test_partial_result_journey_returns_new_structured_action_after_a_refailure() -> None:
    fake = _fake()
    source_a_id = _dispatch_a_only_after_b_is_excluded(fake)

    assert (
        fake.record_source_result(
            source_a_id,
            outcome="failure",
            failure={
                "stage": "parse",
                "code": "MISSING_REQUIRED_SECTION",
                "missing_sections": ["job_description"],
                "impact": "Retry source A still has missing content.",
            },
            core_decision_revision=205,
        )
        is True
    )

    snapshot = fake.snapshot()
    failure = snapshot["failures"][-1]
    required_action = snapshot["required_actions"]

    assert snapshot["status"] == W1JobStatus.WAITING_USER.value
    assert snapshot["completion_kind"] == CompletionKind.NONE.value
    assert snapshot["active_slots"] == 0
    assert failure["source_id"] == str(source_a_id)
    assert failure["code"] == "MISSING_REQUIRED_SECTION"
    assert failure["missing_sections"] == ["job_description"]
    assert failure["impact"] == "Retry source A still has missing content."
    assert failure["core_decision_revision"] == 205
    assert required_action == [
        {
            "action": "core_failure_decision",
            "source_id": str(source_a_id),
            "choices": ["continue_limited", "stop", "retry"],
            "expected_result_version": snapshot["result_revision"],
        }
    ]
    assert fake.request_analysis() is False
