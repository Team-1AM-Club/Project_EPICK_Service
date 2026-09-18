from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.runtime.core_decision_binding import (
    CoreDecisionContractError,
    core_decision_payload_digest,
    parse_w3_core_decision_event,
)

BACKEND_ROOT = Path(__file__).parents[2]
FIXTURE_ROOT = BACKEND_ROOT / "contracts" / "fixtures" / "v1" / "w3"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def test_valid_core_fixture_parses_with_expected_binding_fields() -> None:
    event = parse_w3_core_decision_event(_fixture("valid-core.json"))

    assert event.producer == "w3"
    assert event.decision_scope == "COMPANY_KNOWLEDGE"
    assert event.decision_owner == "W3"
    assert event.is_core is True
    assert event.decision_code == "CORE_REQUIRED"
    assert event.payload_digest.startswith("sha256:")
    assert len(event.payload_digest) == 71


def test_valid_non_core_fixture_preserves_fail_closed_pair() -> None:
    event = parse_w3_core_decision_event(_fixture("valid-non-core.json"))

    assert event.is_core is False
    assert event.decision_code == "NON_CORE_OPTIONAL"
    assert event.decision_owner == "W3"


def test_core_decision_schema_rejects_unknown_fields() -> None:
    payload = _fixture("valid-core.json")
    payload["untrusted_extension"] = "not-allowed"

    with pytest.raises(CoreDecisionContractError):
        parse_w3_core_decision_event(payload)


def test_canonical_digest_is_stable_across_object_key_order_and_unicode() -> None:
    payload = _fixture("valid-core.json")
    payload["reason_code"] = "REQUIRED_COMPANY_EVIDENCE"
    reversed_payload = dict(reversed(list(payload.items())))

    assert core_decision_payload_digest(payload) == core_decision_payload_digest(
        reversed_payload
    )
    assert core_decision_payload_digest(payload) == (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
    )


def test_nonfinite_json_number_is_rejected_before_schema_acceptance() -> None:
    body = (FIXTURE_ROOT / "valid-core.json").read_text(encoding="utf-8")
    body = body.replace('"decision_version": 1', '"decision_version": NaN')

    with pytest.raises(CoreDecisionContractError, match="CORE_DECISION_NONFINITE_NUMBER"):
        parse_w3_core_decision_event(body)


@pytest.mark.parametrize(
    "fixture_name",
    [
        "invalid-company.json",
        "invalid-core-pair.json",
        "invalid-input.json",
        "invalid-owner.json",
        "invalid-producer.json",
        "invalid-question.json",
        "invalid-revision.json",
        "invalid-scope.json",
    ],
)
def test_invalid_w3_contract_fixtures_are_rejected(fixture_name: str) -> None:
    with pytest.raises(CoreDecisionContractError):
        parse_w3_core_decision_event(_fixture(fixture_name))


def test_core_decision_body_larger_than_16_kib_is_rejected() -> None:
    body = json.dumps(_fixture("valid-core.json")) + (" " * 16_384)

    with pytest.raises(CoreDecisionContractError, match="CORE_DECISION_BODY_TOO_LARGE"):
        parse_w3_core_decision_event(body)
