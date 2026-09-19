from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.runtime.w4_question_core_decision import (
    W4QuestionCoreContractError,
    canonical_w4_question_core_json,
    parse_w4_question_core_event,
    w4_question_core_payload_digest,
)

BACKEND_ROOT = Path(__file__).parents[2]
FIXTURE_ROOT = BACKEND_ROOT / "contracts" / "fixtures" / "v1" / "w4"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def test_valid_w4_core_event_preserves_explicit_binding_and_digest() -> None:
    event = parse_w4_question_core_event(_fixture("valid-core.json"))

    assert event.producer == "w4"
    assert event.decision_scope == "QUESTION_MATCHING"
    assert event.decision_owner == "W4"
    assert event.company_id is None
    assert event.message_id != event.decision_id
    assert event.is_core is True
    assert event.decision_code == "CORE_REQUIRED"
    assert event.payload_digest == (
        "sha256:91149e2871f8bc3bf970a76997bff57bae0adf7bed35cff95b103d3993c3092a"
    )


def test_valid_w4_non_core_event_preserves_closed_pair() -> None:
    event = parse_w4_question_core_event(_fixture("valid-non-core.json"))

    assert event.is_core is False
    assert event.decision_code == "NON_CORE_OPTIONAL"


def test_w4_parser_rejects_candidate_negative_fixtures() -> None:
    negatives = json.loads((FIXTURE_ROOT / "negative-events.json").read_text(encoding="utf-8"))
    for case in negatives:
        with pytest.raises(W4QuestionCoreContractError):
            parse_w4_question_core_event(case["event"])


def test_w4_canonical_digest_is_order_independent_and_preserves_unicode() -> None:
    payload = _fixture("valid-core.json")
    reversed_payload = dict(reversed(list(payload.items())))

    assert w4_question_core_payload_digest(payload) == w4_question_core_payload_digest(
        reversed_payload
    )
    assert canonical_w4_question_core_json({"z": "한글", "a": [2, 1], "null": None}) == (
        b'{"a":[2,1],"null":null,"z":"\xed\x95\x9c\xea\xb8\x80"}'
    )


def test_w4_parser_rejects_missing_job_nonfinite_and_oversized_bodies() -> None:
    payload = _fixture("valid-core.json")
    payload.pop("job_id")
    with pytest.raises(W4QuestionCoreContractError):
        parse_w4_question_core_event(payload)

    nonfinite = (FIXTURE_ROOT / "valid-core.json").read_text(encoding="utf-8")
    nonfinite = nonfinite.replace('"decision_version": 3', '"decision_version": NaN')
    with pytest.raises(W4QuestionCoreContractError, match="W4_QUESTION_CORE_NONFINITE_NUMBER"):
        parse_w4_question_core_event(nonfinite)

    with pytest.raises(W4QuestionCoreContractError, match="W4_QUESTION_CORE_BODY_TOO_LARGE"):
        parse_w4_question_core_event(json.dumps(_fixture("valid-core.json")) + (" " * 16_384))
