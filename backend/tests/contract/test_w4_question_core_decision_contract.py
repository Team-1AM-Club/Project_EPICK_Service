from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from app.runtime.core_decision_binding import core_decision_payload_digest

BACKEND_ROOT = Path(__file__).parents[2]
SCHEMA_PATH = BACKEND_ROOT / "contracts" / "w4" / "v1" / "question-core-decision.event.schema.json"
CANDIDATE_SCHEMA_PATH = (
    BACKEND_ROOT
    / "contracts"
    / "w4"
    / "v1"
    / "w4-question-core-decision.event.candidate.schema.json"
)
FIXTURE_ROOT = BACKEND_ROOT / "contracts" / "fixtures" / "v1" / "w4"


def _json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    schema = _json(SCHEMA_PATH)
    assert isinstance(schema, dict)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


@pytest.mark.parametrize("name", ("valid-core.json", "valid-non-core.json"))
def test_adopted_w4_fixture_is_a_closed_valid_wire_object(
    validator: Draft202012Validator, name: str
) -> None:
    payload = _json(FIXTURE_ROOT / name)
    assert isinstance(payload, dict)
    validator.validate(payload)
    assert payload["producer"] == "w4"
    assert payload["decision_scope"] == "QUESTION_MATCHING"
    assert payload["company_id"] is None
    assert payload["job_id"]
    assert payload["message_id"] != payload["decision_id"]


def test_w4_candidate_schema_bytes_are_preserved_for_provenance() -> None:
    assert hashlib.sha256(CANDIDATE_SCHEMA_PATH.read_bytes()).hexdigest() == (
        "1eb0506d9b13e22198ea11fb5fccd628096de2903b0fe5b1da3d383928d8407a"
    )


def test_candidate_negative_fixtures_fail_schema_validation(
    validator: Draft202012Validator,
) -> None:
    negatives = _json(FIXTURE_ROOT / "negative-events.json")
    assert isinstance(negatives, list)
    for case in negatives:
        assert isinstance(case, dict)
        with pytest.raises(ValidationError):
            validator.validate(case["event"])


def test_canonical_digest_vectors_match_shared_w1_algorithm() -> None:
    vectors = _json(FIXTURE_ROOT / "digest-vectors.json")
    assert isinstance(vectors, list)
    for vector in vectors:
        assert isinstance(vector, dict)
        if "fixture" in vector:
            value = _json(FIXTURE_ROOT / str(vector["fixture"]))
        else:
            value = vector["value"]
        assert isinstance(value, dict)
        assert core_decision_payload_digest(value) == vector["digest"]


def test_w4_contract_keeps_core_pair_and_explicit_job_binding_fail_closed(
    validator: Draft202012Validator,
) -> None:
    payload = _json(FIXTURE_ROOT / "valid-core.json")
    assert isinstance(payload, dict)

    missing_job = dict(payload)
    missing_job.pop("job_id")
    wrong_pair = dict(payload)
    wrong_pair["is_core"] = False

    for invalid in (missing_job, wrong_pair):
        with pytest.raises(ValidationError):
            validator.validate(invalid)
