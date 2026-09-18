from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.runtime.w2_commit_gate_contracts import (
    W2CommitGateContractError,
    canonical_json_digest,
    parse_w2_staged_result,
    staged_result_digest,
)

FIXTURE_ROOT = Path(__file__).parents[2] / "contracts" / "fixtures/v1/w2_commit_gate"


def _load(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def test_digest_vector_matches_pinned_cross_language_value() -> None:
    vector = _load("digest-vector.json")

    assert staged_result_digest(vector["command"], vector["result"]) == vector["expected_digest"]


def test_canonical_digest_sorts_keys_and_keeps_compact_utf8() -> None:
    left = {"b": ["합성", "😀"], "a": {"second": 2, "first": 1}}
    right = {"a": {"first": 1, "second": 2}, "b": ["합성", "😀"]}

    assert canonical_json_digest(left) == canonical_json_digest(right)
    assert canonical_json_digest(left).startswith("sha256:")


def test_canonical_digest_does_not_normalize_unicode() -> None:
    nfd = {"text": "e\u0301"}
    nfc = {"text": "é"}

    assert canonical_json_digest(nfd) != canonical_json_digest(nfc)


def test_non_finite_json_and_python_nan_are_rejected() -> None:
    staged = _load("staged-result.json")
    staged["result"]["message_ko"] = float("nan")

    with pytest.raises(W2CommitGateContractError):
        canonical_json_digest({"not_a_number": float("nan")})
    with pytest.raises(W2CommitGateContractError):
        parse_w2_staged_result(json.dumps(staged).replace('"NaN"', "NaN"))
